"""Queued Qt reservations must preserve outputs across a superseded song.

The bridge, Qt queued connection, QAudioBufferOutput and private QAudioOutput
are real. Receiver/backend objects are fixtures; no speaker is contacted and
no endpoint settings or system default are changed.
"""
from types import SimpleNamespace
import threading
import time
import unittest

from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QAudioOutput
from PySide6.QtWidgets import QApplication

from api.owned_audio_outputs import OwnedAudioOutputController
from local.owned_audio_bridge import OwnedAudioBridge
from tests.test_owned_audio_bridge_handoff import Owner
from tests.test_owned_audio_outputs import Backend, Discovery, Sender


class OwnedOutputSourceRaceQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.audio = QAudioOutput()
        self.audio.setVolume(0)
        self.owner = Owner(audio=self.audio)
        self.held = False
        self.owner.set_output_preparing = lambda value: setattr(self, "held", value)
        self.bridge = OwnedAudioBridge(self.owner)
        self.sender = Sender()
        self.controller = OwnedAudioOutputController(self.bridge, backend=Backend(),
            discovery=Discovery(), sender=self.sender, start_worker=False)
        self.controller._inventory()
        self.workers = []

    def tearDown(self):
        for worker, done, _errors in self.workers:
            self.spin_until(done.is_set)
            worker.join(.1)
        self.controller.close()
        self.bridge.close()
        self.owner.deleteLater()
        self.audio.deleteLater()
        self.app.processEvents()

    def spin_until(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        self.assertTrue(condition(), "Queued Qt bridge operation did not finish")

    def worker(self, operation):
        done, errors = threading.Event(), []
        def run():
            try:
                operation()
            except BaseException as error:
                errors.append(error)
            finally:
                done.set()
        worker = threading.Thread(target=run, daemon=True)
        self.workers.append((worker, done, errors))
        worker.start()
        return done, errors

    def change_song(self, identity):
        self.owner._epoch += 1
        self.owner.track = SimpleNamespace(track_id=identity)
        self.owner.changed.emit()

    def test_queued_obsolete_preparation_retains_solo_and_group_selection(self):
        for members in (("airplay:one",), ("one", "airplay:one")):
            with self.subTest(members=members):
                self.controller._choose(members)
                self.sender.calls.clear()
                requested = threading.Event()
                def observe(request):
                    if request[0] == "prepare":
                        requested.set()
                self.bridge.requested.connect(observe, Qt.ConnectionType.DirectConnection)
                self.change_song("intermediate")
                done, errors = self.worker(self.controller._step)
                try:
                    # Deliberately let the real worker queue its reservation
                    # before Qt assigns the final new song, without draining it.
                    self.assertTrue(requested.wait(1))
                    self.change_song("final-song")
                    self.spin_until(done.is_set)
                    self.assertEqual(errors, [])
                    self.assertEqual(self.controller._members, members)
                    self.assertNotEqual(self.controller.snapshot().state, "error")
                    self.assertFalse(any(call[0] == "stop" for call in self.sender.calls))
                    self.controller._step()
                    self.assertTrue(self.controller._running)
                    self.assertEqual(self.controller._token, self.bridge.media_token())
                    self.assertEqual(self.sender.calls[-1][0:2], ("start", members))
                    self.assertTrue(self.audio.isMuted())
                    self.assertTrue(self.held)
                finally:
                    self.bridge.requested.disconnect(observe)
                    self.controller._choose(())

    def test_superseded_successful_native_reservation_releases_on_stop(self):
        self.controller._choose(("airplay:one",))
        self.change_song("intermediate")
        prepared, reply = threading.Event(), threading.Event()
        prepare = self.bridge.prepare_direct
        def reserved_before_source_changes(epoch, token=None):
            accepted = prepare(epoch, token)
            prepared.set()
            if not reply.wait(2):
                raise AssertionError("Reservation reply gate timed out")
            return accepted
        self.bridge.prepare_direct = reserved_before_source_changes
        done, errors = self.worker(self.controller._step)
        try:
            self.spin_until(prepared.is_set)
            self.assertEqual(self.bridge._epoch, self.controller._session_epoch + 1)
            self.change_song("final-song")
            reply.set()
            self.spin_until(done.is_set)
            self.assertEqual(errors, [])
            self.assertTrue(self.audio.isMuted())
            self.assertTrue(self.held)
            self.controller._choose(())
            self.assertFalse(self.audio.isMuted())
            self.assertFalse(self.held)
            self.assertFalse(self.bridge._direct)
            self.assertEqual(self.controller._preparation_epoch, 0)
            self.assertEqual(self.audio.volume(), 0)
        finally:
            reply.set()
            self.bridge.prepare_direct = prepare


if __name__ == "__main__":
    unittest.main()
