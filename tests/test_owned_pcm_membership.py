"""Owned membership commands never replace the authenticated PCM writer."""
from __future__ import annotations

import io
import json
import os
import queue
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from api.audio_routes import AudioRouteDevice
from api.owned_pcm_process import OwnedPcmProcess
from api.windows_audio_sessions import ProcessIdentity


class OwnedPcmMembershipTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        executable = Path(self.folder.name) / "helper.exe"
        executable.touch()
        self.process = SimpleNamespace(pid=500, stdin=io.StringIO(), stdout=io.StringIO(),
            stderr=io.StringIO(), poll=lambda: None, returncode=0, wait=lambda **_: 0)
        self.sender = OwnedPcmProcess(executable, lambda *_args, **_kwargs: self.process,
            identity_reader=lambda: ProcessIdentity(os.getpid(), 100, r"C:\Python\python.exe"),
            pipe_factory=lambda *_: SimpleNamespace(write=lambda *_: None, close=lambda: None))
        self.addCleanup(self.sender.stop)
        self.one = AudioRouteDevice("one", "One")
        self.two = AudioRouteDevice("two", "Two")
        self.remote = AudioRouteDevice("airplay:fixture", "Local fixture", transport="airplay",
            host="127.0.0.1", port=7000, device_id="fixture")
        with patch.object(self.sender, "_bind_owned_process_identity"):
            self.sender.start_pcm((self.one,), 9)
        self.state = self.sender._mute_state

    def capability(self):
        self.sender._remember_native_event(self.state,
            {"event": "capabilities", "pcm_epoch": 9, "reconfigure_pcm": 1})

    def event(self, phase, confirmed, pending=(), revision=1, epoch=9):
        self.sender._remember_native_event(self.state, {"event": "membership", "pcm_epoch": epoch,
            "revision": revision, "state": phase, "confirmed_ids": list(confirmed), "pending_ids": list(pending)})

    def test_old_helper_stale_epoch_boolean_and_busy_do_not_change_writer(self):
        queue, writer = self.sender._pcm_queue, self.sender._pcm_thread
        self.assertFalse(self.sender.reconfigure_pcm((self.one, self.two), 9))
        self.capability()
        for epoch in (True, 8, 0):
            self.assertFalse(self.sender.reconfigure_pcm((self.one, self.two), epoch))
        self.assertTrue(self.sender.reconfigure_pcm((self.one, self.two), 9))
        self.assertFalse(self.sender.reconfigure_pcm((self.two,), 9))
        self.assertIs(self.sender._pcm_queue, queue)
        self.assertIs(self.sender._pcm_thread, writer)
        self.assertEqual(self.sender._pcm_epoch, 9)
        self.assertFalse(self.sender._pcm_stop.is_set())

    def test_canonical_command_reuses_only_validated_output_arguments(self):
        self.capability()
        self.assertTrue(self.sender.reconfigure_pcm((self.one, self.remote), 9))
        command = json.loads(self.process.stdin.getvalue())
        arguments = bytes.fromhex(command["outputs_hex"]).decode("utf-8").split("\0")
        self.assertEqual(command["epoch"], 9)
        self.assertEqual(command["revision"], 1)
        self.assertEqual(command["command"], "reconfigure_pcm")
        self.assertIn("--receiver-id", arguments)
        self.assertNotIn("--music-pid", arguments)
        self.assertNotIn("--pcm-parent-pid", arguments)
        self.assertNotIn("--source-endpoint", arguments)
        self.event("pending", ("one",), (self.remote.id,))
        self.assertEqual(self.sender.membership_state()["pending_ids"], (self.remote.id,))
        self.event("applied", (self.remote.id, "one"))
        self.assertEqual(self.sender._receiver_ids, (self.remote.id,))
        self.assertEqual(self.sender._local_endpoints, ("one",))
        self.assertTrue(self.sender.reconfigure_pcm((self.remote,), 9))
        self.event("applied", (self.remote.id,), revision=2)
        self.assertEqual(self.sender._local_endpoints, ())
        self.assertEqual(self.sender._pcm_epoch, 9)

    def test_rejected_add_keeps_previous_members_and_allows_later_request(self):
        self.capability()
        self.assertTrue(self.sender.reconfigure_pcm((self.one, self.two), 9))
        self.event("pending", ("one",), ("two",))
        self.event("rejected", ("one",))
        self.assertEqual(self.sender.membership_state()["state"], "rejected")
        self.assertEqual(self.sender._pcm_devices, (self.one,))
        self.assertTrue(self.sender.reconfigure_pcm((self.one,), 9))

    def test_untrusted_membership_evidence_cannot_confirm_arbitrary_members(self):
        self.capability()
        self.event("applied", (), revision=0)
        self.assertEqual(self.sender.membership_state(), {})
        self.assertEqual(self.sender._pcm_devices, (self.one,))
        self.assertTrue(self.sender.reconfigure_pcm((self.one, self.two), 9))
        for phase, ids, pending, revision, epoch in (
                ("applied", ("foreign",), (), 1, 9),
                ("applied", ("one", "two"), (), 2, 9),
                ("applied", ("one", "two"), (), 1, 10),
                ("pending", ("one",), ("one",), 1, 9),
                ("applied", ("one", "one"), (), 1, 9),
                ("rejected", ("one", "two"), (), 1, 9)):
            self.event(phase, ids, pending, revision, epoch)
            self.assertEqual(self.sender.membership_state(), {})
        self.event("applied", ("one", "two"))
        self.assertEqual(self.sender.membership_state()["state"], "applied")

    def test_duplicate_physical_target_changed_identity_and_live_budget_are_rejected(self):
        self.capability()
        for devices in ((self.one, self.one),
                (self.one, AudioRouteDevice("ONE", "Alias")),
                tuple(AudioRouteDevice(str(i), str(i)) for i in range(9)),
                (AudioRouteDevice("one", "Changed", transport="airplay", host="127.0.0.1", device_id="other"),)):
            self.assertFalse(self.sender.reconfigure_pcm(devices, 9))
        self.assertEqual(self.process.stdin.getvalue(), "")

    def test_stale_state_and_stop_revoke_capability(self):
        self.capability()
        self.assertTrue(self.sender.supports_pcm_reconfigure(9))
        self.sender._remember_native_event({}, {"event": "capabilities", "pcm_epoch": 9, "reconfigure_pcm": 0})
        self.assertTrue(self.sender.supports_pcm_reconfigure(9))
        self.sender.stop()
        self.capability()
        self.assertFalse(self.sender.supports_pcm_reconfigure(9))

    def test_presentation_capability_and_membership_queue_are_session_validated(self):
        self.sender._remember_native_event(self.state, {"event": "capabilities", "pcm_epoch": 9,
            "reconfigure_pcm": 1, "owned_presentation": True})
        self.assertFalse(self.sender.supports_owned_presentation(9))
        self.sender._remember_native_event(self.state, {"event": "capabilities", "pcm_epoch": 9,
            "reconfigure_pcm": 1, "owned_presentation": 1})
        self.assertTrue(self.sender.supports_owned_presentation(9))
        self.assertFalse(self.sender.supports_owned_presentation(True))
        self.assertFalse(self.sender.supports_owned_presentation(10))
        self.assertTrue(self.sender.reconfigure_pcm((self.one, self.two), 9))
        lines = [
            {"event": "membership", "pcm_epoch": 9, "revision": 1, "state": "applied",
                "confirmed_ids": ["foreign"], "pending_ids": []},
            {"event": "membership", "pcm_epoch": 9, "revision": 1, "state": "applied",
                "confirmed_ids": ["one", "two"], "pending_ids": []}]
        process = SimpleNamespace(stdout=io.StringIO("".join(json.dumps(event) + "\n" for event in lines)))
        events = queue.Queue(maxsize=32)
        self.sender._read_events(process, events, lambda event: self.sender._remember_native_event(self.state, event))
        self.assertEqual(events.qsize(), 1)
        self.assertEqual(events.get_nowait()["confirmed_ids"], ["one", "two"])
        self.sender.stop()
        self.assertFalse(self.sender.supports_owned_presentation(9))

    def test_volume_uses_identity_not_mutable_receiver_index(self):
        self.sender._pcm_devices = (self.one, self.remote)
        self.sender._receiver_ids = (self.remote.id,)
        self.capability()
        self.assertTrue(self.sender.set_device_volume(self.remote.id, .2))
        command = json.loads(self.process.stdin.getvalue())
        self.assertEqual(command["command"], "set_owned_volume")
        self.assertEqual(bytes.fromhex(command["receiver_hex"]).decode(), self.remote.id)
        self.assertEqual(command["epoch"], 9)
        self.assertFalse(self.sender.set_device_volume("pending-or-removed", .2))
        self.assertFalse(self.sender.set_device_volume(self.remote.id, float("nan")))


if __name__ == "__main__":
    unittest.main()
