from __future__ import annotations

import io
import os
import queue
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QByteArray, QObject, Signal
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat
from PySide6.QtWidgets import QApplication

from api.audio_routes import AudioRouteDevice
from api.audio_routes import AudioRouteSnapshot
from api.owned_audio_outputs import OwnedAudioOutputController
from api.owned_pcm_process import OwnedPcmProcess
from api.providers import PlaybackSourceProvider
from api.windows_audio_devices import AudioOutputDevice
from api.windows_audio_sessions import ProcessIdentity
from local.owned_audio_bridge import OwnedAudioBridge


class Bridge:
    supported = True
    def __init__(self):
        self.token = ("owned:netease:one", 1)
        self.device = "one"
        self.calls = []
    def attach(self, controller): self.controller = controller
    def media_token(self): return self.token
    def current_device(self): return self.device
    def select_device(self, endpoint, token=None):
        self.calls.append(("select", endpoint))
        self.device = endpoint
        return True
    def set_direct(self, enabled, epoch, token=None):
        self.calls.append(("direct", enabled, epoch))
        return token is None or token == self.token
    def prepare_direct(self, epoch, token=None):
        self.calls.append(("prepare", epoch))
        return token is None or token == self.token
    def receiver_play_pause(self, epoch, identity, revision, expires_at, token=None):
        self.calls.append(("receiver_play_pause", epoch, identity, revision, token))
        return token == self.token and time.monotonic() < expires_at


class Backend:
    def __init__(self):
        self.devices = tuple(AudioOutputDevice(key, key + " speaker", "speaker", is_default=key == "one", volume=.25)
            for key in ("one", "two", "three"))
        self.writes = []
    def list_outputs(self): return self.devices
    def set_default(self, *_): raise AssertionError("owned routes must not change the system default")
    def set_endpoint_mute(self, *_): raise AssertionError("owned routes must not mute endpoints")
    def set_endpoint_volume(self, endpoint, volume): self.writes.append((endpoint, volume))
    def close(self): pass


class Discovery:
    state = "idle"
    def __init__(self):
        self.remote = (AudioRouteDevice("airplay:one", "HomePod", "homepod", "airplay", host="192.168.1.2", device_id="AABB"),)
    def start(self): self.state = "browsing"
    def devices(self): return self.remote
    def close(self): pass


class Sender:
    def __init__(self):
        self.calls, self.events, self.packets = [], [], []
        self.started_devices = []
        self.error = ""
    def start_pcm(self, devices, epoch):
        self.started_devices.append(devices)
        self.calls.append(("start", tuple(d.id for d in devices), epoch))
    def stop(self): self.calls.append(("stop",))
    def pcm_error(self): return self.error
    def poll_events(self):
        events, self.events = self.events, []
        return events
    def submit_pcm(self, data, epoch):
        self.packets.append((data, epoch))
        return True
    def receiver_volume_state(self, *_): return {"known": False}
    def set_device_volume(self, *args): self.calls.append(("volume",) + args)
    def latency_estimate(self): return None
    def diagnostics(self): return {}


class OwnedOutputControllerTests(unittest.TestCase):
    def setUp(self):
        self.bridge, self.backend, self.discovery, self.sender = Bridge(), Backend(), Discovery(), Sender()
        self.controller = OwnedAudioOutputController(self.bridge, backend=self.backend,
            discovery=self.discovery, sender=self.sender, start_worker=False)
        self.controller._inventory()
    def tearDown(self): self.controller.close()
    def choose(self, identity):
        self.assertTrue(self.controller.select(identity))
        self.controller._step()
    def ready(self):
        self.sender.events = [{"event": "waiting_audio"}]
        self.controller._step()

    def receiver_tap(self, **changes):
        event = {"event": "receiver_play_pause", "pcm_epoch": self.controller._session_epoch,
            "receiver_id": "airplay:one", "revision": 0, "cseq": "7", "age_ms": 0,
            "_received_at": time.monotonic()}
        event.update(changes)
        return event

    def test_authenticated_receiver_taps_toggle_existing_source_without_reconnect(self):
        self.choose("airplay:one")
        self.ready()
        before = tuple(self.sender.calls)
        self.sender.events = [self.receiver_tap(), self.receiver_tap(), self.receiver_tap(cseq="8")]
        self.controller._step()
        taps = [call for call in self.bridge.calls if call[0] == "receiver_play_pause"]
        self.assertEqual(taps, [("receiver_play_pause", 1, "airplay:one", 0, self.bridge.token)] * 2)
        self.assertEqual(tuple(self.sender.calls), before)
        self.assertEqual(self.backend.writes, [])

    def test_receiver_taps_reject_stale_wrong_member_epoch_revision_and_bad_fields(self):
        self.choose("airplay:one")
        self.ready()
        for changes in ({"pcm_epoch": 0}, {"pcm_epoch": True}, {"revision": 1}, {"revision": False},
                {"receiver_id": "one"}, {"receiver_id": "airplay:removed"}, {"receiver_id": []},
                {"cseq": "0007"}, {"cseq": "-7"}, {"cseq": "７"}, {"cseq": "1" * 17}, {"cseq": []},
                {"age_ms": True}, {"age_ms": 2000}, {"age_ms": -1}, {"_received_at": float("nan")},
                {"_received_at": True}, {"_received_at": time.monotonic() - 3},
                {"_received_at": time.monotonic() + 3},
                {"_received_at": time.monotonic() - 1.2, "age_ms": 1000}):
            with self.subTest(changes=changes):
                self.sender.events = [self.receiver_tap(**changes)]
                self.controller._step()
        self.assertFalse(any(call[0] == "receiver_play_pause" for call in self.bridge.calls))

    def test_terminal_or_superseded_source_never_applies_receiver_tap(self):
        self.choose("airplay:one")
        self.ready()
        self.bridge.token = ("owned:netease:two", 2)
        self.sender.events = [self.receiver_tap()]
        self.controller._step()
        self.assertFalse(any(call[0] == "receiver_play_pause" for call in self.bridge.calls))
        self.sender.events = [self.receiver_tap(), {"event": "stopped"}]
        self.controller._step()
        self.assertFalse(any(call[0] == "receiver_play_pause" for call in self.bridge.calls))

    def test_receiver_taps_require_feeding_and_current_confirmed_membership(self):
        self.choose("airplay:one")
        self.sender.events = [self.receiver_tap()]
        self.controller._step()
        self.ready()
        self.controller._requested_members = ("airplay:one", "two")
        self.controller._receiver_play_pause(self.receiver_tap())
        self.controller._requested_members = None
        self.sender.membership_state = lambda: {"revision": 3}
        self.controller._receiver_play_pause(self.receiver_tap())
        self.assertFalse(any(call[0] == "receiver_play_pause" for call in self.bridge.calls))
        self.controller._receiver_play_pause(self.receiver_tap(revision=3))
        self.assertEqual(sum(call[0] == "receiver_play_pause" for call in self.bridge.calls), 1)
        self.choose("two")
        self.controller._receiver_play_pause(self.receiver_tap(revision=3, cseq="8"))
        self.assertEqual(sum(call[0] == "receiver_play_pause" for call in self.bridge.calls), 1)

    def test_receiver_dedup_is_bounded_and_resets_for_next_owned_session(self):
        self.choose("airplay:one")
        self.ready()
        for number in range(150):
            self.controller._receiver_play_pause(self.receiver_tap(cseq=str(number)))
        self.assertEqual(len(self.controller._receiver_controls_seen), 128)
        self.bridge.token = ("owned:netease:two", 2)
        self.controller.media_changed(self.bridge.token)
        self.controller._step()
        self.ready()
        self.controller._receiver_play_pause(self.receiver_tap(cseq="149"))
        self.assertEqual(len(self.controller._receiver_controls_seen), 1)
        self.assertEqual([call for call in self.bridge.calls if call[0] == "receiver_play_pause"][-1][1], 2)

    def test_inventory_exposes_real_windows_and_airplay_with_owned_scope(self):
        snapshot = self.controller.snapshot()
        self.assertEqual(len(snapshot.devices), 4)
        self.assertEqual(snapshot.active_id, "one")
        self.assertEqual(snapshot.capture_scope, "owned-player")
        self.assertEqual(snapshot.devices[0].volume, .25)

    def test_single_windows_changes_only_qt_device_and_confirms_actual_endpoint(self):
        self.choose("two")
        self.assertIn(("select", "two"), self.bridge.calls)
        self.assertEqual(self.controller.snapshot().active_id, "two")
        self.assertEqual(self.sender.calls, [])
        self.assertEqual(self.backend.writes, [])

    def test_first_airplay_uses_owned_pcm_instead_of_computer_sound(self):
        self.choose("airplay:one")
        self.assertEqual(self.sender.calls[0][:2], ("start", ("airplay:one",)))
        self.assertFalse(any(call[:2] == ("direct", True) for call in self.bridge.calls))
        self.ready()
        self.assertIn(("direct", True, 1), self.bridge.calls)
        self.assertTrue(self.controller.submit_pcm(b"\x10\0" * 2, 1))
        self.assertFalse(self.controller.submit_pcm(b"\x10\0" * 2, 2))

    def test_first_airplay_plus_preserves_existing_qt_windows_output(self):
        self.assertTrue(self.controller.toggle("airplay:one"))
        self.controller._step()
        self.assertEqual(self.sender.calls[0][:2], ("start", ("one", "airplay:one")))

    def test_checkbox_extends_existing_qt_windows_output_and_shrinks_back(self):
        self.assertTrue(self.controller.toggle("two"))
        self.controller._step()
        self.assertEqual(self.sender.calls[0][:2], ("start", ("one", "two")))
        self.ready()
        self.assertTrue(self.controller.toggle("one"))
        self.controller._step()
        self.assertEqual(self.controller.snapshot().active_id, "two")
        self.assertIn(("stop",), self.sender.calls)
        self.assertIn(("direct", False, 1), self.bridge.calls)

    def test_streaming_requires_fresh_matching_owned_epoch_pcm(self):
        self.choose("airplay:one")
        self.ready()
        self.sender.events = [{"event": "streaming"}, {"event": "stats", "_received_at": time.monotonic(),
            "group": {"pcm_epoch": 0}, "capture_signal": {"mode": "direct_pcm", "scope": "owned-player",
            "meaningful_samples": 50, "last_meaningful_age_ms": 0}}]
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "waiting_audio")
        self.sender.events[0:0] = [{"event": "stats", "_received_at": time.monotonic(),
            "group": {"pcm_epoch": 1}, "capture_signal": {"mode": "direct_pcm", "scope": "owned-player",
            "meaningful_samples": 50, "last_meaningful_age_ms": 0}}]
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "casting")
        self.assertEqual(self.controller.snapshot().member_ids, ("airplay:one",))

    def test_new_track_restarts_epoch_and_rejects_old_pcm(self):
        self.choose("airplay:one")
        self.ready()
        self.bridge.token = ("owned:netease:two", 2)
        self.controller.media_changed(self.bridge.token)
        self.controller._step()
        self.assertEqual(self.sender.calls[-1], ("start", ("airplay:one",), 2))
        self.assertFalse(self.controller.submit_pcm(b"\x10\0" * 2, 1))
        self.ready()
        self.assertTrue(self.controller.submit_pcm(b"\x10\0" * 2, 2))

    def test_seek_restarts_same_track_with_new_native_epoch(self):
        self.choose("airplay:one")
        self.ready()
        self.controller.media_changed(self.bridge.token, force=True)
        self.controller._step()
        self.assertEqual(self.sender.calls[-1][-1], 2)

    def test_endpoint_removal_and_pipe_failure_restore_only_qt_output(self):
        for failure in ("endpoint", "pipe"):
            with self.subTest(failure=failure):
                self.controller._choose(("one", "two"))
                self.ready()
                if failure == "endpoint":
                    original = self.backend.devices
                    self.backend.devices = tuple(d for d in original if d.id != "two")
                    self.controller._inventory()
                    self.backend.devices = original
                    self.controller._inventory()
                else:
                    self.sender.error = "pipe_failed"
                    self.controller._step()
                self.assertFalse(self.controller._running)
                self.assertEqual(self.controller.snapshot().state, "error")
                self.assertEqual(self.controller._feeding_epoch, 0)

    def test_mdns_disappearance_does_not_cancel_live_authenticated_receiver(self):
        self.choose("airplay:one")
        self.ready()
        self.discovery.remote = ()
        self.controller._inventory()
        self.assertTrue(self.controller._running)
        self.assertTrue(any(d.id == "airplay:one" and not d.available for d in self.controller.snapshot().devices))

    def test_confirmed_receiver_restarts_after_mdns_disappearance(self):
        self.choose("airplay:one")
        self.ready()
        self.sender.events = [{"event": "streaming"}, {"event": "stats", "_received_at": time.monotonic(),
            "group": {"pcm_epoch": 1}, "capture_signal": {"mode": "direct_pcm", "scope": "owned-player",
            "meaningful_samples": 50, "last_meaningful_age_ms": 0}}]
        self.controller._step()
        self.discovery.remote = ()
        self.controller._inventory()
        self.bridge.token = ("owned:netease:two", 2)
        self.controller.media_changed(self.bridge.token)
        self.controller._step()
        self.assertEqual(self.sender.calls[-1], ("start", ("airplay:one",), 2))
        self.assertTrue(self.controller._running)
        self.assertEqual(self.controller.snapshot().state, "connecting")
        # Another song/seek before this restart confirms must retain the
        # original transport evidence even after inventory redraws pending.
        self.controller._inventory()
        self.bridge.token = ("owned:netease:three", 3)
        self.controller.media_changed(self.bridge.token)
        self.controller._step()
        self.assertEqual(self.sender.calls[-1], ("start", ("airplay:one",), 3))
        self.assertTrue(self.controller._running)
        self.assertEqual(self.sender.started_devices[-1][0].host, "192.168.1.2")

    def test_retained_confirmation_cannot_accept_changed_unavailable_address(self):
        self.test_confirmed_receiver_restarts_after_mdns_disappearance()
        self.controller._publish(devices=tuple(replace(d, host="192.168.1.99") if d.id == "airplay:one" else d
            for d in self.controller.snapshot().devices))
        self.controller.media_changed(self.bridge.token, force=True)
        self.controller._step()
        self.assertEqual(self.sender.started_devices[-1][0].host, "192.168.1.2")

    def test_retained_receiver_trust_is_revoked_by_stop_removal_and_failure(self):
        for reason in ("stop", "remove", "terminal", "pipe"):
            with self.subTest(reason=reason):
                self.discovery.remote = Discovery().remote
                self.sender.error = ""
                self.controller._inventory()
                self.controller._choose(("one", "airplay:one"))
                self.ready()
                epoch = self.controller._session_epoch
                self.sender.events = [{"event": "streaming"}, {"event": "stats", "_received_at": time.monotonic(),
                    "group": {"pcm_epoch": epoch}, "capture_signal": {"mode": "direct_pcm", "scope": "owned-player",
                    "meaningful_samples": 50, "last_meaningful_age_ms": 0}}]
                self.controller._step()
                self.discovery.remote = ()
                self.controller._inventory()
                if reason == "stop":
                    self.controller.stop_cast()
                elif reason == "remove":
                    self.controller.toggle("airplay:one")
                elif reason == "terminal":
                    self.sender.events = [{"event": "error"}]
                else:
                    self.sender.error = "pipe_failed"
                self.controller._step()
                self.assertEqual(self.controller._confirmed_receivers, {})
                self.controller._choose(("airplay:one",))
                self.assertEqual(self.controller.snapshot().state, "error")
                self.assertFalse(self.controller._running)

    def test_old_pcm_failure_cannot_stop_replacement_session(self):
        self.choose("airplay:one")
        self.ready()
        self.controller.media_changed(self.bridge.token, force=True)
        self.assertTrue(self.controller.pcm_failed("backpressure", 1))
        self.controller._step()
        self.assertEqual(self.controller._session_epoch, 2)
        self.assertTrue(self.controller._running)
        self.assertEqual(self.controller.snapshot().state, "connecting")
        self.assertFalse(self.controller.pcm_failed("format", 1))

    def test_old_track_failure_is_ignored_before_queued_media_change(self):
        self.choose("airplay:one")
        self.ready()
        self.assertTrue(self.controller.pcm_failed("format", 1))
        self.bridge.token = ("owned:netease:two", 2)
        self.controller.media_changed(self.bridge.token)
        self.controller._step()
        self.assertEqual(self.sender.calls[-1], ("start", ("airplay:one",), 2))
        self.assertTrue(self.controller._running)

    def test_current_pcm_failure_stops_and_restores_output(self):
        self.choose("airplay:one")
        self.ready()
        self.assertTrue(self.controller.pcm_failed("format", 1))
        self.controller._step()
        self.assertFalse(self.controller._running)
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertIn(("direct", False, 1), self.bridge.calls)

    def test_no_track_waits_and_does_not_invent_a_process_source(self):
        self.bridge.token = ("", 5)
        self.choose("airplay:one")
        self.assertEqual(self.controller.snapshot().state, "waiting_source")
        self.assertEqual(self.sender.calls, [])
        self.bridge.token = ("owned:netease:next", 6)
        self.controller.media_changed(self.bridge.token)
        self.controller._step()
        self.assertEqual(self.sender.calls[-1][0], "start")

    def test_volumes_are_explicit_bounded_and_invalid_devices_do_not_queue(self):
        self.assertFalse(self.controller.select("missing"))
        self.assertFalse(self.controller.set_volume("one", float("nan")))
        self.assertFalse(self.controller.set_volume("one", True))
        self.assertTrue(self.controller.set_volume("one", .7))
        self.controller._step()
        self.assertEqual(self.backend.writes, [("one", .7)])


class Device:
    def __init__(self, identity): self.identity = identity
    def id(self): return QByteArray(self.identity.encode())


class Audio:
    def __init__(self): self.selected, self.muted = Device("one"), False
    def device(self): return self.selected
    def setDevice(self, value): self.selected = value
    def isMuted(self): return self.muted
    def setMuted(self, value): self.muted = value


class Media(QObject):
    def setAudioBufferOutput(self, value): self.output = value


class Owner(QObject):
    changed = Signal()
    def __init__(self):
        super().__init__()
        self.track = SimpleNamespace(track_id="track-one")
        self._epoch = 1
        self.audio, self.player = Audio(), Media()


class OwnedAudioBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])
    def setUp(self):
        self.owner = Owner()
        self.bridge = OwnedAudioBridge(self.owner, media_devices=SimpleNamespace(audioOutputs=lambda: [Device("one"), Device("two")]))
        self.controller = SimpleNamespace(packets=[], failures=[], changes=[])
        self.controller.submit_pcm = lambda data, epoch: self.controller.packets.append((data, epoch)) or True
        self.controller.accepts_pcm = lambda epoch: True
        self.controller.pcm_failed = lambda reason, epoch: self.controller.failures.append((reason, epoch))
        self.controller.media_changed = lambda token, **kw: self.controller.changes.append((token, kw))
        self.bridge.attach(self.controller)
    def tearDown(self): self.bridge.close()
    def buffer(self, rate=44100, sample_format=QAudioFormat.SampleFormat.Int16, start=0, frames=512):
        format_ = QAudioFormat()
        format_.setSampleRate(rate)
        format_.setChannelCount(2)
        format_.setSampleFormat(sample_format)
        return QAudioBuffer(b"\x10\0" * (frames * 2), format_, start)

    def test_device_ids_are_exact_and_stale_token_cannot_apply(self):
        self.assertTrue(self.bridge.select_device("two"))
        self.assertEqual(self.bridge.current_device(), "two")
        self.assertFalse(self.bridge.select_device("one", token=("old", 0)))
        self.assertFalse(self.bridge.select_device("missing"))
        self.assertEqual(self.bridge.current_device(), "two")

    def test_receiver_play_pause_reuses_owned_command_and_exact_gui_fences(self):
        calls = []
        self.owner.send_playback_command = lambda command: calls.append(command) or True
        self.controller.accepts_receiver_control = lambda epoch, receiver, revision, token: (
            epoch == 4 and receiver == "airplay:one" and revision == 2 and token == self.bridge.media_token())
        self.assertFalse(self.bridge.receiver_play_pause(4, "airplay:one", 2, time.monotonic() + 2))
        self.assertTrue(self.bridge.set_direct(True, 4))
        self.assertTrue(self.bridge.receiver_play_pause(4, "airplay:one", 2, time.monotonic() + 2))
        for epoch, identity, revision, expires in ((3, "airplay:one", 2, time.monotonic() + 2),
                (4, "airplay:removed", 2, time.monotonic() + 2), (4, "airplay:one", 1, time.monotonic() + 2),
                (4, "airplay:one", 2, time.monotonic() - .1)):
            self.assertFalse(self.bridge.receiver_play_pause(epoch, identity, revision, expires))
        self.assertFalse(self.bridge.receiver_play_pause(4, "airplay:one", 2, time.monotonic() + 2, token=("old", 0)))
        self.assertEqual(calls, ["toggle_play_pause"])

    def test_tap_expiring_in_gui_queue_cannot_toggle_later(self):
        calls, result = [], []
        self.owner.send_playback_command = lambda command: calls.append(command) or True
        self.controller.accepts_receiver_control = lambda *_: True
        self.assertTrue(self.bridge.set_direct(True, 1))
        expires = time.monotonic() + .05
        worker = threading.Thread(target=lambda: result.append(self.bridge.receiver_play_pause(1, "airplay:one", 0, expires)))
        worker.start()
        time.sleep(.07)
        self.app.processEvents()
        worker.join(2)
        self.assertEqual(result, [False])
        self.assertEqual(calls, [])

    def test_direct_mutes_only_owned_output_and_restores_existing_mute(self):
        for originally_muted in (False, True):
            self.owner.audio.muted = originally_muted
            self.assertTrue(self.bridge.set_direct(True, 1))
            self.assertTrue(self.owner.audio.muted)
            self.assertFalse(self.bridge.set_direct(False, 2))
            self.assertTrue(self.bridge.set_direct(False, 1))
            self.assertEqual(self.owner.audio.muted, originally_muted)

    def test_valid_pcm_is_fixed_format_and_large_buffers_split_bounded_packets(self):
        self.assertTrue(self.bridge.set_direct(True, 3))
        self.bridge._buffer(self.buffer(frames=20000))
        self.assertEqual([len(data) for data, _ in self.controller.packets], [65536, 14464])
        self.assertTrue(all(epoch == 3 for _, epoch in self.controller.packets))
        self.assertEqual(self.controller.failures, [])

    def test_wrong_sample_format_never_reaches_native_pcm(self):
        self.bridge.set_direct(True, 1)
        self.bridge._buffer(self.buffer(rate=48000))
        self.assertEqual(self.controller.packets, [])
        self.assertEqual(self.controller.failures, [("format", 1)])

    def test_missing_qt_buffer_feature_retains_windows_selection(self):
        owner = Owner()
        with patch("local.owned_audio_bridge.QtMultimedia.QAudioBufferOutput", None):
            bridge = OwnedAudioBridge(owner, media_devices=SimpleNamespace(audioOutputs=lambda: [Device("one"), Device("two")]))
        controller = OwnedAudioOutputController(bridge, backend=Backend(), discovery=Discovery(),
            sender=Sender(), start_worker=False)
        try:
            self.assertFalse(bridge.supported)
            self.assertIsNone(bridge.output)
            controller._inventory()
            controller._choose(("two",))
            self.assertEqual(controller.snapshot().active_id, "two")
            controller._choose(("airplay:one",))
            self.assertEqual(controller.snapshot().state, "error")
            self.assertIn("不支持", controller.snapshot().status_message)
            self.assertFalse(controller._running)
            self.assertFalse(owner.audio.isMuted())
        finally:
            controller.close()
            bridge.close()

    def test_seek_and_track_change_notify_fresh_generation_without_old_pcm(self):
        self.bridge.set_direct(True, 1)
        self.bridge._buffer(self.buffer())
        self.bridge._buffer(self.buffer(start=5_000_000))
        self.assertEqual(len(self.controller.packets), 1)
        self.assertTrue(self.controller.changes[-1][1]["force"])
        self.owner._epoch += 1
        self.owner.changed.emit()
        self.assertEqual(self.controller.changes[-1][0], ("track-one", 2))

    def test_paused_seek_immediately_restarts_and_drops_prior_pcm(self):
        self.bridge.set_direct(True, 1)
        self.bridge._position_changed(1000)
        self.bridge._position_changed(5000)
        self.assertTrue(self.controller.changes[-1][1]["force"])
        self.bridge._buffer(self.buffer())
        self.assertEqual(self.controller.packets, [])

    def test_normal_delayed_clock_observation_does_not_restart(self):
        self.bridge.set_direct(True, 1)
        with patch("local.owned_audio_bridge.time.monotonic", side_effect=[10.0, 12.0]):
            self.bridge._position_changed(1000)
            self.bridge._position_changed(3000)
        self.assertEqual(self.controller.changes, [])

    def test_unprocessed_worker_request_expires_without_late_gui_mutation(self):
        result = []
        worker = threading.Thread(target=lambda: result.append(self.bridge.select_device("two")))
        worker.start()
        worker.join(2)
        self.assertEqual(result, [False])
        self.app.processEvents()
        self.assertEqual(self.bridge.current_device(), "one")

    def test_expired_release_restores_mute_when_gui_recovers(self):
        self.assertTrue(self.bridge.set_direct(True, 1))
        result = []
        worker = threading.Thread(target=lambda: result.append(self.bridge.set_direct(False, 1)))
        worker.start()
        worker.join(2)
        self.assertEqual(result, [False])
        self.assertTrue(self.owner.audio.isMuted())
        self.owner._epoch += 1
        self.app.processEvents()
        self.assertFalse(self.owner.audio.isMuted())

    def test_expired_old_release_cannot_unmute_replacement(self):
        self.assertTrue(self.bridge.set_direct(True, 1))
        worker = threading.Thread(target=lambda: self.bridge.set_direct(False, 1))
        worker.start()
        worker.join(2)
        self.assertTrue(self.bridge.set_direct(True, 2))
        self.app.processEvents()
        self.assertTrue(self.owner.audio.isMuted())
        self.assertEqual(self.bridge._epoch, 2)
        self.assertTrue(self.bridge.set_direct(False, 2))
        self.assertFalse(self.owner.audio.isMuted())

    def test_controller_close_restores_private_mute_before_gui_loop_ends(self):
        controller = OwnedAudioOutputController(self.bridge, backend=Backend(), discovery=Discovery(),
            sender=Sender(), start_worker=False)
        controller._session_epoch = 1
        self.assertTrue(self.bridge.set_direct(True, 1))
        self.assertTrue(self.owner.audio.isMuted())
        controller.close()
        self.assertFalse(self.owner.audio.isMuted())
        self.assertFalse(controller.accepts_pcm(1))


class OwnedPcmTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "GluSpotAirPlay.exe"
        self.path.touch()
        self.identity = ProcessIdentity(os.getpid(), 100, r"C:\Python\python.exe")
        self.process = SimpleNamespace(pid=500, stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO(), returncode=0,
            poll=lambda: 0, wait=lambda **kw: 0)
        self.packets, self.args = [], []
        pipe = SimpleNamespace(write=self.packets.append, close=lambda: None)
        def popen(args, **kw):
            self.args.append(args)
            return self.process
        self.sender = OwnedPcmProcess(self.path, popen, identity_reader=lambda: self.identity, pipe_factory=lambda *_: pipe)
        self.device = AudioRouteDevice("one", "Speaker")
    def tearDown(self):
        self.sender.stop()
        self.temp.cleanup()

    def test_direct_args_use_real_parent_creation_and_no_external_capture_params(self):
        self.sender.start_pcm((self.device,), 7)
        args = self.args[0]
        self.assertEqual(args[args.index("--pcm-parent-pid") + 1], str(os.getpid()))
        self.assertEqual(args[args.index("--pcm-parent-created-at") + 1], "100")
        self.assertEqual(args[args.index("--pcm-epoch") + 1], "7")
        self.assertNotIn("--music-pid", args)
        self.assertNotIn("--source-endpoint", args)

    def test_native_receiver_taps_require_live_pcm_child_and_exact_membership(self):
        self.sender._pcm_epoch = 7
        self.sender._pcm_stop.clear()
        self.sender._receiver_ids = ("airplay:one",)
        self.sender._receiver_connected = True
        event = {"event": "receiver_play_pause", "pcm_epoch": 7, "receiver_id": "airplay:one",
            "revision": 0, "cseq": "8", "age_ms": 0}
        state = self.sender._mute_state
        self.assertTrue(self.sender._remember_native_event(state, event))
        for changes in ({"pcm_epoch": 6}, {"pcm_epoch": True}, {"revision": 2}, {"revision": False},
                {"receiver_id": "one"}, {"receiver_id": []}, {"cseq": "08"}, {"cseq": "７"},
                {"cseq": "8" * 17}, {"cseq": []}, {"age_ms": -1}, {"age_ms": True}, {"age_ms": 2000}):
            with self.subTest(changes=changes):
                self.assertFalse(self.sender._remember_native_event(state, event | changes))
        self.assertFalse(self.sender._remember_native_event({}, event))
        self.sender._membership_pending_revision = 1
        self.assertFalse(self.sender._remember_native_event(state, event))
        self.sender._membership_pending_revision = 0
        self.sender._membership_evidence = {"revision": 1}
        self.assertFalse(self.sender._remember_native_event(state, event))
        self.assertTrue(self.sender._remember_native_event(state, event | {"revision": 1}))
        self.sender._last_terminal = {"event": "stopped"}
        self.assertFalse(self.sender._remember_native_event(state, event | {"revision": 1}))
        self.sender._last_terminal = {}
        self.sender._pcm_stop.set()
        self.assertFalse(self.sender._remember_native_event(state, event | {"revision": 1}))

    def test_group_reader_stamps_taps_and_drops_rejected_owned_child_events(self):
        import json
        from api.airplay_group_process import AirPlayGroupProcess
        event = {"event": "receiver_play_pause", "_received_at": -999, "pcm_epoch": 3}
        process = SimpleNamespace(stdout=io.StringIO(json.dumps(event) + "\n"))
        events = queue.Queue(maxsize=32)
        AirPlayGroupProcess._read_events(process, events, lambda _: True)
        self.assertGreater(events.get_nowait()["_received_at"], 0)
        process.stdout = io.StringIO(json.dumps(event) + "\n")
        AirPlayGroupProcess._read_events(process, events, lambda _: False)
        self.assertTrue(events.empty())

    def test_packets_preserve_pcm_and_have_strict_sequence_and_epoch(self):
        self.sender.start_pcm((self.device,), 8)
        data = b"\x10\0\x20\0" * 100
        self.assertTrue(self.sender.submit_pcm(data, 8))
        self.assertTrue(self.sender.submit_pcm(data, 8))
        deadline = time.monotonic() + 1
        while len(self.packets) < 2 and time.monotonic() < deadline:
            time.sleep(.001)
        self.assertEqual(len(self.packets), 2)
        for number, packet in enumerate(self.packets, 1):
            self.assertEqual(self.sender.HEADER.unpack(packet[:24]), (b"GPCM", 8, number, 100))
            self.assertEqual(packet[24:], data)

    def test_bad_epoch_identity_members_and_oversized_packet_are_rejected(self):
        for epoch in (0, True, 2**64):
            with self.assertRaises(ValueError): self.sender.start_pcm((self.device,), epoch)
        with self.assertRaises(ValueError): self.sender.start_pcm((self.device, self.device), 1)
        self.identity = ProcessIdentity(os.getpid() + 1, 100, r"C:\Python\python.exe")
        with self.assertRaises(RuntimeError): self.sender.start_pcm((self.device,), 1)
        self.identity = ProcessIdentity(os.getpid(), 100, r"C:\Python\python.exe")
        self.sender.start_pcm((self.device,), 1)
        self.assertFalse(self.sender.submit_pcm(b"x" * 65540, 1))
        self.assertFalse(self.sender.submit_pcm(b"x" * 4, 2))
        self.assertFalse(self.sender.submit_pcm(b"xxx", 1))

    def test_backpressure_fails_closed_instead_of_unbounded_growth(self):
        self.sender._process = self.process
        self.sender._pcm_epoch = 1
        for _ in range(5): self.assertTrue(self.sender.submit_pcm(b"x" * 65536, 1))
        self.assertFalse(self.sender.submit_pcm(b"x" * 65536, 1))
        self.assertEqual(self.sender.pcm_error(), "backpressure")
        self.assertLessEqual(self.sender._pcm_bytes, self.sender.MAX_QUEUED_BYTES)

    def test_blocked_write_is_cancelled_and_disposed_before_restart(self):
        entered, cancelled, disposed = threading.Event(), threading.Event(), threading.Event()
        class BlockedPipe:
            def write(self, _data):
                entered.set()
                self.assert_cancelled = cancelled.wait(2)
                if not self.assert_cancelled:
                    raise AssertionError("write was not cancelled")
                raise OSError("cancelled")
            def close(self): cancelled.set()
            def dispose(self): disposed.set()
        self.sender._pipe_factory = lambda *_: BlockedPipe()
        self.sender.start_pcm((self.device,), 1)
        self.assertTrue(self.sender.submit_pcm(b"\x10\0" * 100, 1))
        self.assertTrue(entered.wait(1))
        thread = self.sender._pcm_thread
        started = time.monotonic()
        self.sender.stop()
        self.assertLess(time.monotonic() - started, 1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(disposed.is_set())
        self.assertEqual(self.sender.pcm_error(), "")

    def test_late_old_constructor_cannot_publish_or_clear_new_pipe(self):
        entered, release, old_disposed = threading.Event(), threading.Event(), threading.Event()
        old_pipe = SimpleNamespace(write=lambda *_: None, close=lambda: None, dispose=old_disposed.set)
        new_pipe = SimpleNamespace(write=lambda *_: None, close=lambda: None, dispose=lambda: None)
        attempts = []
        def connect(*_):
            attempts.append(True)
            if len(attempts) == 1:
                entered.set()
                release.wait(3)
                return old_pipe
            return new_pipe
        self.sender._pipe_factory = connect
        self.sender.start_pcm((self.device,), 1)
        self.assertTrue(entered.wait(1))
        old_thread = self.sender._pcm_thread
        self.sender.start_pcm((self.device,), 2)
        deadline = time.monotonic() + 1
        while self.sender._pcm_pipe is not new_pipe and time.monotonic() < deadline:
            time.sleep(.001)
        self.assertIs(self.sender._pcm_pipe, new_pipe)
        release.set()
        old_thread.join(1)
        self.assertFalse(old_thread.is_alive())
        self.assertTrue(old_disposed.is_set())
        self.assertIs(self.sender._pcm_pipe, new_pipe)
        self.assertEqual(self.sender._pcm_pipe_epoch, 2)


class OwnedOutputProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])
    def setUp(self):
        self.owner, self.calls = Owner(), []
        self.settings = {"v2_owned_playback_enabled": True, "v2_system_capture_enabled": False}
        self.system = SimpleNamespace(settings_setter=None,
            suspend_live_audio=lambda: self.calls.append(("suspend",)),
            stop_audio_cast=lambda: self.calls.append(("system_stop",)))
        self.provider = PlaybackSourceProvider(self.system, self.owner, lambda key, default=None: self.settings.get(key, default))
        self.outputs = SimpleNamespace(snapshot=lambda: AudioRouteSnapshot(capture_scope="owned-player", state="ready"),
            refresh=lambda: self.calls.append(("refresh",)) or True,
            select=lambda value: self.calls.append(("select", value)) or True,
            toggle=lambda value: self.calls.append(("toggle", value)) or True,
            set_volume=lambda *value: self.calls.append(("volume",) + value) or True,
            add_homepod=lambda *value: self.calls.append(("homepod",) + value) or True,
            stop_cast=lambda: self.calls.append(("owned_stop",)) or True)
        self.provider._owned_outputs = self.outputs
    def tearDown(self): self.provider._owned_audio_bridge.close()

    def test_owned_only_all_output_controls_use_owned_controller(self):
        self.assertEqual(self.provider.get_audio_route_snapshot().capture_scope, "owned-player")
        self.assertTrue(self.provider.refresh_audio_outputs())
        self.assertTrue(self.provider.select_audio_output("device"))
        self.assertTrue(self.provider.toggle_audio_output("device"))
        self.assertTrue(self.provider.set_audio_output_volume("device", .2))
        self.assertTrue(self.provider.add_homepod("192.168.1.2"))
        self.assertTrue(self.provider.stop_audio_cast())
        self.assertEqual(self.calls, [("refresh",), ("select", "device"), ("toggle", "device"),
            ("volume", "device", .2), ("homepod", "192.168.1.2", 7000), ("owned_stop",)])

    def test_owned_only_idle_still_enumerates_devices(self):
        self.owner.track = None
        self.assertIs(self.provider.start_audio_outputs(), self.outputs)
        self.assertTrue(self.provider.refresh_audio_outputs())
        self.assertEqual(self.calls, [("refresh",)])

    def test_disabling_owned_cancels_cast_even_before_any_track(self):
        self.owner.track = None
        self.settings["v2_owned_playback_enabled"] = False
        self.owner.changed.emit()
        self.assertEqual(self.calls, [("owned_stop",)])
        self.assertFalse(self.provider.select_audio_output("device"))

    def test_owned_takeover_stops_external_capture_without_polling_it(self):
        self.owner.changed.emit()
        self.owner.changed.emit()
        self.assertEqual(self.calls, [("suspend",), ("system_stop",)])


if __name__ == "__main__": unittest.main()
