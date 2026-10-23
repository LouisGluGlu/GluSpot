"""Stopped owned outputs retain diagnostics, not queued audio or live members."""
from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from api.audio_routes import AudioRouteDevice
from api.owned_pcm_process import OwnedPcmProcess
from api.windows_audio_sessions import ProcessIdentity


class OwnedPcmLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        executable = Path(self.folder.name) / "inert-helper.exe"
        executable.touch()
        self.created_pipes = []
        def process(*_args, **_kwargs):
            return SimpleNamespace(pid=500, stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO(),
                poll=lambda: 0, wait=lambda **_: 0, returncode=0)
        def delayed_pipe(_name, _process, _identity, stopped):
            # No native process or device. Model authentication not having
            # completed; cancellation must still release queued PCM and worker.
            stopped.wait(2)
            pipe = SimpleNamespace(closed=False, disposed=False)
            pipe.close = lambda: setattr(pipe, "closed", True)
            pipe.dispose = lambda: setattr(pipe, "disposed", True)
            self.created_pipes.append(pipe)
            return pipe
        self.sender = OwnedPcmProcess(executable, process,
            identity_reader=lambda: ProcessIdentity(os.getpid(), 100, r"C:\Python\python.exe"),
            pipe_factory=delayed_pipe)
        self.addCleanup(self.sender.stop)
        self.one = AudioRouteDevice("fixture-one", "In-memory output")

    def start(self, epoch=1):
        with patch.object(self.sender, "_bind_owned_process_identity"):
            self.sender.start_pcm((self.one,), epoch)

    def test_stop_discards_bounded_pending_pcm_and_resets_byte_accounting(self):
        self.start()
        for _ in range(5):
            self.assertTrue(self.sender.submit_pcm(b"\x10\x00" * 32768, 1))
        self.assertGreater(self.sender._pcm_bytes, 0)
        pending = self.sender._pcm_queue
        writer = self.sender._pcm_thread
        self.sender.stop()
        self.assertEqual(self.sender._pcm_bytes, 0)
        self.assertTrue(pending.empty())
        self.assertIsNone(self.sender._pcm_thread)
        self.assertFalse(writer.is_alive())
        self.assertTrue(all(pipe.closed and pipe.disposed for pipe in self.created_pipes))

    def test_short_decoder_buffers_use_the_audio_byte_budget(self):
        self.start()
        # One second in 10 ms decoder buffers must not hit an unrelated
        # descriptor-count limit while the pipe worker is being scheduled.
        for index in range(100):
            self.assertTrue(self.sender.submit_pcm(b"\x10\x00" * 882, 1), index)
        self.assertEqual(self.sender._pcm_bytes, 176400)
        self.assertEqual(self.sender.pcm_error(), "")

    def test_observed_gui_stall_burst_is_bounded_and_retains_failure_evidence(self):
        self.start()
        content = b"\x10\x00" * 882
        # Native Finder monitoring stalled the real GUI for 2.59 seconds.
        # A burst of 260 ten-millisecond buffers must retain every byte.
        for index in range(260):
            self.assertTrue(self.sender.submit_pcm(content, 1), index)
        self.assertEqual(self.sender._pcm_bytes, 458640)
        for _ in range(140):
            self.assertTrue(self.sender.submit_pcm(content, 1))
        self.assertEqual(self.sender._pcm_bytes, self.sender.MAX_QUEUED_BYTES)
        self.assertFalse(self.sender.submit_pcm(content, 1))
        self.sender.stop()
        evidence = self.sender.diagnostics()["pcm_queue"]
        self.assertEqual(evidence["queued_bytes"], 0)
        self.assertEqual(evidence["high_water_bytes"], self.sender.MAX_QUEUED_BYTES)
        self.assertEqual(evidence["high_water_buffers"], 400)
        self.assertEqual(evidence["backpressure_reason"], "byte_budget")

    def test_stop_revokes_active_member_references_but_preserves_terminal_diagnostics(self):
        self.start()
        self.sender._membership_evidence = {"pcm_epoch": 1, "confirmed_ids": (self.one.id,)}
        self.sender._member_evidence = {"receiver": {"state": "streaming"}}
        self.sender._local_evidence = {self.one.id: {"ready": True}}
        self.sender._last_terminal = {"event": "error", "message": "fixture terminal"}
        self.sender.stop()
        self.assertEqual(self.sender._pcm_devices, ())
        self.assertEqual(self.sender.membership_state(), {})
        self.assertEqual(self.sender._local_endpoints, ())
        self.assertEqual(self.sender._member_evidence, {})
        self.assertEqual(self.sender._local_evidence, {})
        self.assertEqual(self.sender._last_terminal["event"], "error")

    def test_repeated_inert_lifecycle_has_no_retained_pcm_threads_or_processes(self):
        baseline = {thread.ident for thread in threading.enumerate()}
        writers = []
        for epoch in range(1, 33):
            self.start(epoch)
            writers.append(self.sender._pcm_thread)
            self.assertTrue(self.sender.submit_pcm(b"\x10\x00" * 32768, epoch))
            self.sender.stop()
            self.assertIsNone(self.sender._process)
            self.assertIsNone(self.sender._reader)
            self.assertIsNone(self.sender._stderr_reader)
            self.assertIsNone(self.sender._pcm_pipe)
            self.assertEqual(self.sender._pcm_bytes, 0)
            self.assertTrue(self.sender._pcm_queue.empty())
        self.assertFalse(any(thread.is_alive() for thread in writers))
        self.assertFalse([thread for thread in threading.enumerate()
            if thread.ident not in baseline and thread.name.startswith("GluSpotOwned")])

    def test_submit_waiting_for_lock_cannot_repopulate_queue_after_stop(self):
        self.start()
        results = []
        entered = threading.Event()
        original_lock = self.sender._pcm_lock
        class ObservedLock:
            def __enter__(self):
                if threading.current_thread().name == "fixture-submit":
                    entered.set()
                original_lock.acquire()
            def __exit__(self, *_):
                original_lock.release()
        self.sender._pcm_lock = ObservedLock()
        with self.sender._pcm_lock:
            worker = threading.Thread(name="fixture-submit",
                target=lambda: results.append(self.sender.submit_pcm(b"\x10\x00" * 256, 1)))
            worker.start()
            self.assertTrue(entered.wait(1))
            # submit passed its first fence, but stop owns the final fence.
            self.sender._pcm_stop.set()
        worker.join(1)
        self.sender.stop()
        self.assertEqual(results, [False])
        self.assertEqual(self.sender._pcm_bytes, 0)
        self.assertTrue(self.sender._pcm_queue.empty())

    def test_pipe_close_error_cannot_skip_process_or_writer_cleanup(self):
        self.start()
        self.sender._pcm_pipe = SimpleNamespace(close=lambda: (_ for _ in ()).throw(OSError("fixture close")))
        self.sender._pcm_pipe_epoch = 1
        writer = self.sender._pcm_thread
        with self.assertRaises(OSError):
            self.sender.stop()
        self.assertFalse(writer.is_alive())
        self.assertIsNone(self.sender._process)
        self.assertIsNone(self.sender._reader)
        self.assertIsNone(self.sender._stderr_reader)
        self.assertIsNone(self.sender._pcm_thread)
        self.assertIsNone(self.sender._pcm_pipe)
        self.assertEqual(self.sender._pcm_bytes, 0)
        self.assertEqual(self.sender._pcm_epoch, 0)


if __name__ == "__main__":
    unittest.main()
