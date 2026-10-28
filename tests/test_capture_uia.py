"""STA ownership, timeout quarantine, low-frequency sampling and COM faults."""
import asyncio
import threading
import unittest
from types import SimpleNamespace
from dataclasses import replace

from api.capture.uia import StaWorker, UiaBackend
from api.capture.position import PositionModel
from api.capture.contracts import TimelineSample
from tests.test_capture_smtc import visual


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_sta_initialization_io_and_cleanup_share_thread(self):
        threads = []
        cleaned = threading.Event()
        def initialize():
            threads.append(threading.get_ident())
            def cleanup():
                threads.append(threading.get_ident())
                cleaned.set()
            return cleanup
        worker = StaWorker(initializer=initialize)
        result = await worker.call(lambda: threading.get_ident())
        worker.close()
        await asyncio.to_thread(cleaned.wait, 1)
        self.assertEqual(threads, [result, result])
        self.assertNotEqual(result, threading.get_ident())

    async def test_timeout_quarantines_and_never_queues_second_mutation(self):
        release = threading.Event()
        completed = threading.Event()
        writes = []
        worker = StaWorker(timeout=.01, initializer=lambda: None)
        def operation():
            writes.append('first')
            release.wait(1)
            completed.set()
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await worker.call(operation)
            self.assertTrue(worker.busy)
            with self.assertRaises(RuntimeError):
                await worker.call(lambda: writes.append('second'))
            self.assertEqual(writes, ['first'])
        finally:
            worker.close()
            release.set()
            await asyncio.to_thread(completed.wait, 1)

    async def test_access_denied_or_com_initialization_error_fails_closed(self):
        def denied():
            raise PermissionError('UIPI')
        worker = StaWorker(initializer=denied)
        calls = []
        try:
            with self.assertRaises(PermissionError):
                await worker.call(lambda: calls.append(1))
            self.assertEqual(calls, [])
        finally:
            worker.close()

    async def test_calibrator_rate_limit_and_reader_failure_recovery(self):
        now = [100.0]
        calls = []
        class Reader:
            def read_progress_position(self, *_args):
                calls.append(now[0])
                return SimpleNamespace(position_seconds=20, duration_seconds=200, is_playing=True)
            def seek_playback_position_once(self, *_args):
                calls.append('seek')
                return True
        worker = StaWorker(initializer=lambda: None)
        backend = UiaBackend('netease', visual().track, worker=worker, reader_factory=Reader, clock=lambda: now[0])
        try:
            self.assertIsNotNone(await backend.calibrate())
            self.assertIsNone(await backend.calibrate())
            now[0] = 108
            self.assertIsNotNone(await backend.calibrate())
            self.assertTrue(await backend.seek(80, request_id='one'))
            self.assertFalse(await backend.seek(80, request_id='one'))
            self.assertEqual(calls, [100, 108, 'seek'])
        finally:
            backend.close()
            worker.close()

    async def test_element_disappearance_invalidates_reader_without_stale_sample(self):
        readers = []
        class Reader:
            def __init__(self):
                readers.append(self)
            def read_progress_position(self, *_args):
                return None
            def reset(self):
                pass
        worker = StaWorker(initializer=lambda: None)
        backend = UiaBackend('qq', visual().track, worker=worker, reader_factory=Reader)
        try:
            self.assertIsNone(await backend.read())
            self.assertIsNone(await backend.read())
            self.assertEqual(len(readers), 2)
        finally:
            backend.close()
            worker.close()


class CalibrationModelTests(unittest.TestCase):
    def test_stale_smtc_anchor_cannot_undo_uia_correction(self):
        model = PositionModel()
        initial = TimelineSample('app', 'song', 10, 100, 1000, updated_at=1000, is_playing=True)
        model.observe(initial)
        correction = replace(initial, source='uia', position_seconds=80, observed_at=101)
        self.assertTrue(model.calibrate(correction))
        model.observe(replace(initial, observed_at=102, wall_time=1002))
        self.assertEqual(model.position(102), 81)
        model.observe(replace(initial, position_seconds=20, observed_at=103, wall_time=1003, updated_at=1003))
        self.assertEqual(model.position(103), 20)
