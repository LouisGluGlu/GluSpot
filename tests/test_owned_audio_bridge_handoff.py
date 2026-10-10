"""PCM prefill and presentation mute must share one fenced Qt session."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QByteArray, QObject, Signal
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat, QAudioOutput
from PySide6.QtWidgets import QApplication

from local.owned_audio_bridge import OwnedAudioBridge


class Device:
    def __init__(self, identity):
        self.identity = identity

    def id(self):
        return QByteArray(self.identity.encode())


class SignalAudio(QObject):
    mutedChanged = Signal(bool)

    def __init__(self, muted=False):
        super().__init__()
        self.muted = muted
        self.calls = []
        self.accepted = True

    def isMuted(self):
        return self.muted

    def setMuted(self, muted):
        self.calls.append((muted, threading.get_ident()))
        if self.accepted and muted != self.muted:
            self.muted = muted
            self.mutedChanged.emit(muted)


class Media(QObject):
    positionChanged = Signal(int)

    def setAudioBufferOutput(self, output):
        self.output = output


class Owner(QObject):
    changed = Signal()

    def __init__(self, audio=None):
        super().__init__()
        self.track = SimpleNamespace(track_id="fixture-song")
        self._epoch = 4
        self.audio = audio or SignalAudio()
        self.player = Media()


class OwnedAudioBridgeHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = Owner()
        self.bridge = OwnedAudioBridge(self.owner,
            media_devices=SimpleNamespace(audioOutputs=lambda: [Device("one")]))
        self.controller = SimpleNamespace(packets=[], failures=[], changes=[], accepted=True)
        self.controller.submit_pcm = lambda data, epoch: self.controller.packets.append((data, epoch)) or True
        self.controller.accepts_pcm = lambda epoch: self.controller.accepted
        self.controller.pcm_failed = lambda reason, epoch: self.controller.failures.append((reason, epoch))
        self.controller.media_changed = lambda token, **kwargs: self.controller.changes.append((token, kwargs))
        self.bridge.attach(self.controller)
        self.token = self.bridge.media_token()

    def tearDown(self):
        self.bridge.close()

    def buffer(self, start=0):
        format_ = QAudioFormat()
        format_.setSampleRate(44100)
        format_.setChannelCount(2)
        format_.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        return QAudioBuffer(b"\x10\0" * 1024, format_, start)

    def test_prefill_feeds_pcm_without_muting_then_commit_retains_same_timeline(self):
        self.assertTrue(self.bridge.set_direct(True, 10, self.token, muted=False))
        first = self.buffer()
        self.bridge._buffer(first)
        self.assertFalse(self.owner.audio.isMuted())
        self.assertEqual(self.owner.audio.calls, [])
        self.assertEqual(self.controller.packets, [(bytes(first.constData()), 10)])
        buffer_end = self.bridge._last_buffer_end
        self.assertTrue(self.bridge.commit_direct_mute(10, self.token))
        self.assertTrue(self.owner.audio.isMuted())
        self.assertEqual(self.bridge._last_buffer_end, buffer_end)
        self.bridge._buffer(self.buffer(start=buffer_end))
        self.assertEqual(len(self.controller.packets), 2)
        self.assertEqual(self.controller.changes, [])
        self.assertEqual(self.bridge._epoch, 10)
        self.assertTrue(self.bridge.set_direct(False, 10))
        self.assertFalse(self.owner.audio.isMuted())

    def test_legacy_direct_still_mutes_immediately_and_restores_its_change(self):
        self.assertTrue(self.bridge.set_direct(True, 10, self.token))
        self.assertTrue(self.owner.audio.isMuted())
        self.assertTrue(self.bridge.set_direct(False, 10))
        self.assertFalse(self.owner.audio.isMuted())

    def test_preexisting_or_user_mute_during_prefill_is_not_restored_by_bridge(self):
        for before_feed, commit in ((True, False), (True, True), (False, False), (False, True)):
            with self.subTest(before_feed=before_feed, commit=commit):
                self.owner.audio.setMuted(before_feed)
                self.assertTrue(self.bridge.set_direct(True, 10, muted=False))
                if not before_feed:
                    self.owner.audio.setMuted(True)
                self.owner.audio.calls.clear()
                if commit:
                    self.assertTrue(self.bridge.commit_direct_mute(10))
                self.assertTrue(self.bridge.set_direct(False, 10))
                self.assertTrue(self.owner.audio.isMuted())
                self.assertEqual(self.owner.audio.calls, [])

    def test_external_unmute_ends_ownership_and_later_external_mute_survives_release(self):
        self.assertTrue(self.bridge.set_direct(True, 10))
        self.owner.audio.setMuted(False)
        self.owner.audio.setMuted(True)
        self.owner.audio.calls.clear()
        self.assertTrue(self.bridge.set_direct(False, 10))
        self.assertTrue(self.owner.audio.isMuted())
        self.assertEqual(self.owner.audio.calls, [])

    def test_duplicate_commit_does_not_remute_an_explicit_external_unmute(self):
        self.assertTrue(self.bridge.set_direct(True, 10, muted=False))
        self.assertTrue(self.bridge.commit_direct_mute(10))
        self.owner.audio.setMuted(False)
        self.owner.audio.calls.clear()
        self.assertTrue(self.bridge.commit_direct_mute(10))
        self.assertFalse(self.owner.audio.isMuted())
        self.assertEqual(self.owner.audio.calls, [])
        self.assertTrue(self.bridge.set_direct(False, 10))
        self.assertFalse(self.owner.audio.isMuted())

    def test_commit_requires_active_same_epoch_same_media_token(self):
        self.assertFalse(self.bridge.commit_direct_mute(10))
        self.assertTrue(self.bridge.set_direct(True, 10, self.token, muted=False))
        self.assertFalse(self.bridge.commit_direct_mute(9, self.token))
        self.assertFalse(self.bridge.commit_direct_mute(10, ("old-song", 4)))
        self.assertFalse(self.owner.audio.isMuted())
        self.assertTrue(self.bridge.set_direct(False, 10))
        self.assertFalse(self.bridge.commit_direct_mute(10, self.token))
        self.assertFalse(self.owner.audio.isMuted())

    def test_track_change_and_seek_reject_pending_mute_and_old_pcm(self):
        for change in ("track", "seek"):
            with self.subTest(change=change):
                self.assertTrue(self.bridge.set_direct(True, 10, muted=False))
                if change == "track":
                    self.owner._epoch += 1
                    self.owner.track = SimpleNamespace(track_id="second-song")
                    self.owner.changed.emit()
                else:
                    self.bridge._position_changed(0)
                    self.bridge._position_changed(5000)
                self.assertFalse(self.bridge.commit_direct_mute(10))
                self.bridge._buffer(self.buffer())
                self.assertEqual(self.controller.packets, [])
                self.assertFalse(self.owner.audio.isMuted())
                self.assertTrue(self.controller.changes)
                self.assertTrue(self.bridge.set_direct(False, 10))

    def test_new_unmuted_feed_restores_old_owned_mute_and_old_release_cannot_touch_it(self):
        self.assertTrue(self.bridge.set_direct(True, 10))
        self.assertTrue(self.owner.audio.isMuted())
        self.assertTrue(self.bridge.set_direct(True, 11, muted=False))
        self.assertFalse(self.owner.audio.isMuted())
        self.assertTrue(self.bridge.commit_direct_mute(11))
        self.assertFalse(self.bridge.set_direct(False, 10))
        self.assertFalse(self.bridge.commit_direct_mute(10))
        self.assertTrue(self.owner.audio.isMuted())
        self.assertTrue(self.bridge.set_direct(False, 11))
        self.assertFalse(self.owner.audio.isMuted())

    def test_changed_owner_without_notification_cannot_append_pcm_or_commit_old_session(self):
        self.assertTrue(self.bridge.set_direct(True, 10, muted=False))
        self.owner._epoch += 1
        self.owner.track = SimpleNamespace(track_id="replacement-song")
        self.assertFalse(self.bridge.commit_direct_mute(10, self.token))
        self.assertFalse(self.bridge.commit_direct_mute(10))
        self.bridge._buffer(self.buffer())
        self.assertEqual(self.controller.packets, [])
        self.assertEqual(self.controller.changes[-1][0], self.bridge.media_token())
        self.assertFalse(self.owner.audio.isMuted())

    def test_native_pcm_rejection_during_prefill_does_not_mute_qt(self):
        self.assertTrue(self.bridge.set_direct(True, 10, muted=False))
        self.controller.accepted = False
        self.bridge._buffer(self.buffer())
        self.assertEqual(self.controller.packets, [])
        self.assertEqual(self.controller.failures, [])
        self.assertFalse(self.owner.audio.isMuted())

    def test_failed_mute_readback_rejects_commit_without_claiming_mute(self):
        self.assertTrue(self.bridge.set_direct(True, 10, muted=False))
        self.owner.audio.accepted = False
        self.assertFalse(self.bridge.commit_direct_mute(10))
        self.assertFalse(self.owner.audio.isMuted())
        self.assertTrue(self.bridge.set_direct(False, 10))
        self.assertFalse(self.owner.audio.isMuted())

    def test_worker_commit_mutates_only_on_owner_thread(self):
        self.assertTrue(self.bridge.set_direct(True, 10, self.token, muted=False))
        results = []
        worker = threading.Thread(target=lambda: results.append(self.bridge.commit_direct_mute(10, self.token)))
        worker.start()
        deadline = time.monotonic() + 2
        while worker.is_alive() and time.monotonic() < deadline:
            self.app.processEvents()
            worker.join(.005)
        self.assertEqual(results, [True])
        self.assertEqual(self.owner.audio.calls, [(True, threading.get_ident())])

    def test_expired_commit_cannot_mute_when_gui_recovers(self):
        self.assertTrue(self.bridge.set_direct(True, 10, self.token, muted=False))
        results = []
        worker = threading.Thread(target=lambda: results.append(self.bridge.commit_direct_mute(10, self.token)))
        worker.start()
        worker.join(2)
        self.assertEqual(results, [False])
        self.app.processEvents()
        self.assertFalse(self.owner.audio.isMuted())
        self.assertEqual(self.owner.audio.calls, [])

    def test_close_restores_owned_mute_and_rejects_late_commit(self):
        self.assertTrue(self.bridge.set_direct(True, 10, muted=False))
        self.assertTrue(self.bridge.commit_direct_mute(10))
        self.bridge.close()
        self.assertFalse(self.owner.audio.isMuted())
        self.assertFalse(self.bridge.commit_direct_mute(10))
        self.assertFalse(self.bridge.set_direct(True, 11, muted=False))
        self.assertIsNone(self.owner.player.output)

    def test_real_qaudiooutput_muted_signal_preserves_external_mute(self):
        audio = QAudioOutput()
        audio.setVolume(0)
        owner = Owner(audio)
        bridge = OwnedAudioBridge(owner)
        try:
            self.assertTrue(bridge.set_direct(True, 10, muted=False))
            self.assertFalse(audio.isMuted())
            self.assertTrue(bridge.commit_direct_mute(10))
            self.assertTrue(audio.isMuted())
            audio.setMuted(False)
            audio.setMuted(True)
            self.assertTrue(bridge.set_direct(False, 10))
            self.assertTrue(audio.isMuted())
            self.assertEqual(audio.volume(), 0)
        finally:
            bridge.close()


if __name__ == "__main__":
    unittest.main()
