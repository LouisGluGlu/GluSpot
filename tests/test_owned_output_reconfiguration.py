"""Controller contracts for preserving an established owned PCM epoch."""
from dataclasses import replace
import time
import unittest
from unittest.mock import patch

from tests.test_owned_audio_outputs import Backend, Bridge, Discovery, Sender
from api.owned_audio_outputs import OwnedAudioOutputController
from api.audio_routes import AudioRouteDevice
from local.audio_output_status import connected_output_kinds, OutputMembershipTracker


class HotSender(Sender):
    def __init__(self):
        super().__init__()
        self.membership = {}
        self.revision = 0
        self.accept = True
        self.presentation = False
    def supports_pcm_reconfigure(self, epoch): return True
    def supports_owned_presentation(self, epoch): return self.presentation
    def membership_state(self): return dict(self.membership)
    def reconfigure_pcm(self, devices, epoch):
        self.calls.append(("reconfigure", tuple(d.id for d in devices), epoch))
        if not self.accept: return False
        self.revision += 1
        return True
    def complete(self, ids, epoch, state="applied"):
        self.membership = dict(pcm_epoch=epoch, revision=self.revision,
            state=state, confirmed_ids=tuple(ids), pending_ids=())


class HandoffBridge(Bridge):
    def __init__(self):
        super().__init__()
        self.muted = False
        self.commit_ok = True
    def set_direct(self, enabled, epoch, token=None, *, muted=True):
        self.calls.append(("direct", enabled, epoch, muted))
        self.muted = enabled and muted
        return token is None or token == self.token
    def commit_direct_mute(self, epoch, token=None):
        self.calls.append(("commit", epoch))
        self.muted = self.commit_ok
        return self.commit_ok


class OwnedReconfigurationTests(unittest.TestCase):
    def setUp(self):
        self.bridge, self.backend, self.discovery, self.sender = HandoffBridge(), Backend(), Discovery(), HotSender()
        self.controller = OwnedAudioOutputController(self.bridge, backend=self.backend,
            discovery=self.discovery, sender=self.sender, start_worker=False)
        self.controller._inventory()
    def tearDown(self): self.controller.close()
    def signal(self, epoch=None):
        return {"event": "stats", "_received_at": time.monotonic(),
            "group": {"pcm_epoch": self.controller._session_epoch if epoch is None else epoch},
            "capture_signal": {"mode": "direct_pcm", "scope": "owned-player",
                "meaningful_samples": 100, "last_meaningful_age_ms": 0}}
    def establish(self, ids=("one", "airplay:one")):
        self.controller._choose(ids)
        self.sender.events = [{"event": "waiting_audio"}]
        self.controller._step()
        self.sender.events = [{"event": "streaming"}, self.signal()]
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "casting")
        self.sender.calls.clear()
    def complete(self, ids, state="applied"):
        self.sender.complete(ids, self.controller._session_epoch, state)
        self.controller._step()

    def test_plus_airplay_keeps_current_qt_windows_member(self):
        self.controller.toggle("airplay:one")
        self.controller._step()
        self.assertEqual(self.sender.calls[0][1], ("one", "airplay:one"))

    def test_join_preserves_feed_epoch_old_members_and_latency_until_confirmed(self):
        self.establish()
        old = self.controller.snapshot()
        self.controller.toggle("two")
        self.controller._step()
        current = self.controller.snapshot()
        self.assertEqual(self.sender.calls, [("reconfigure", ("one", "airplay:one", "two"), 1)])
        self.assertEqual(current.member_ids, old.member_ids)
        self.assertEqual(current.pending_ids, ("two",))
        self.assertEqual((current.state, current.group_id, current.cast_latency_seconds),
            ("reconfiguring", old.group_id, old.cast_latency_seconds))
        self.assertTrue(self.controller.submit_pcm(b"\x10\0" * 2, 1))
        self.assertTrue(self.bridge.muted)
        self.complete(("one", "airplay:one", "two"))
        self.assertEqual(self.controller.snapshot().member_ids, ("one", "airplay:one", "two"))
        self.assertEqual(self.controller._session_epoch, 1)
        self.assertFalse(any(c[0] in {"stop", "start"} for c in self.sender.calls))

    def test_solo_windows_retains_native_clock_and_removes_other_members(self):
        self.establish()
        self.controller.select("one")
        self.controller._step()
        self.complete(("one",))
        self.assertEqual(self.controller.snapshot().member_ids, ("one",))
        self.assertEqual(self.controller._feeding_epoch, 1)
        self.assertEqual(self.sender.calls, [("reconfigure", ("one",), 1)])

    def test_rejected_or_refused_join_does_not_interrupt_old_group(self):
        self.establish()
        self.controller.toggle("two")
        self.controller._step()
        self.complete(("one", "airplay:one"), "rejected")
        self.assertEqual(self.controller.snapshot().member_ids, ("one", "airplay:one"))
        self.assertIn("连接失败", self.controller.snapshot().status_message)
        self.sender.accept = False
        self.controller.select("two")
        self.controller._step()
        self.assertTrue(self.controller.accepts_pcm(1))
        self.assertFalse(any(c[0] == "stop" for c in self.sender.calls))

    def test_latest_solo_intent_follows_pending_join_without_restart(self):
        self.establish()
        self.controller.toggle("two")
        self.controller._step()
        self.controller.toggle("three")
        self.controller.select("airplay:one")
        self.controller._step()
        self.assertEqual(len(self.sender.calls), 1)
        self.complete(("one", "airplay:one", "two"))
        self.assertEqual(self.sender.calls[-1], ("reconfigure", ("airplay:one",), 1))
        self.complete(("airplay:one",))
        self.assertEqual(self.controller.snapshot().member_ids, ("airplay:one",))

    def test_seek_revokes_pending_epoch_and_uses_latest_target(self):
        self.establish()
        self.controller.toggle("two")
        self.controller._step()
        self.controller.media_changed(self.bridge.token, force=True)
        self.controller._step()
        self.assertEqual(self.sender.calls[-1], ("start", ("one", "airplay:one", "two"), 2))
        self.assertFalse(self.controller.accepts_pcm(1))
        self.sender.complete(("one", "airplay:one", "two"), 1)
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "connecting")

    def test_wrong_epoch_revision_and_members_cannot_confirm(self):
        self.establish()
        self.controller.toggle("two")
        self.controller._step()
        for epoch, revision, ids in [(2, 1, ("one", "airplay:one", "two")),
                (1, 0, ("one", "airplay:one", "two")), (1, 1, ("two",))]:
            self.sender.membership = dict(pcm_epoch=epoch, revision=revision,
                state="applied", confirmed_ids=ids)
            self.controller._step()
            self.assertEqual(self.controller.snapshot().state, "reconfiguring")

    def test_first_group_prefills_without_muting_then_hands_off_at_valid_stream(self):
        self.sender.presentation = True
        self.controller._choose(("airplay:one",))
        self.sender.events = [{"event": "waiting_audio"}, self.signal()]
        self.controller._step()
        self.assertFalse(self.bridge.muted)
        self.assertTrue(self.controller.accepts_pcm(1))
        self.sender.events = [{"event": "streaming"}]
        self.controller._step()
        self.assertTrue(self.bridge.muted)
        self.assertEqual(self.bridge.calls.count(("commit", 1)), 1)

    def test_handoff_failure_restores_qt_and_revokes_pcm(self):
        self.sender.presentation = True
        self.bridge.commit_ok = False
        self.controller._choose(("airplay:one",))
        self.sender.events = [{"event": "waiting_audio"}, {"event": "streaming"}, self.signal()]
        self.controller._step()
        self.assertFalse(self.bridge.muted)
        self.assertFalse(self.controller.accepts_pcm(1))
        self.assertEqual(self.controller.snapshot().state, "error")

    def test_expired_signal_cannot_commit_later_presentation(self):
        self.sender.presentation = True
        self.controller._choose(("airplay:one",))
        stamp = time.monotonic()
        with patch("api.owned_audio_outputs.time.monotonic", return_value=stamp):
            self.sender.events = [{"event": "waiting_audio"}, self.signal()]
            self.controller._step()
        with patch("api.owned_audio_outputs.time.monotonic", return_value=stamp + 2.1):
            self.sender.events = [{"event": "streaming"}]
            self.controller._step()
        self.assertFalse(self.bridge.muted)
        self.assertEqual(self.controller.snapshot().state, "waiting_audio")
        self.sender.events = [self.signal()]
        self.controller._step()
        self.assertTrue(self.bridge.muted)

    def test_user_change_before_handoff_cannot_create_unmuted_casting_group(self):
        self.sender.presentation = True
        self.controller._choose(("airplay:one",))
        self.sender.events = [{"event": "waiting_audio"}, {"event": "streaming"}]
        self.controller._step()
        self.controller.toggle("two")
        self.controller._step()
        self.assertFalse(any(c[0] == "reconfigure" for c in self.sender.calls))
        self.assertEqual(self.controller._session_epoch, 2)
        self.assertEqual(self.controller.snapshot().state, "connecting")
        self.sender.events = [{"event": "waiting_audio"}, {"event": "streaming"}, self.signal()]
        self.controller._step()
        self.assertTrue(self.bridge.muted)
        self.assertFalse(self.controller._handoff_pending)

    def test_source_restart_with_missing_device_revokes_old_native_timeline(self):
        self.establish(("one", "two"))
        self.controller._publish(devices=tuple(d for d in self.controller.snapshot().devices if d.id != "two"))
        self.bridge.token = ("new-song", 2)
        self.controller.media_changed(self.bridge.token)
        self.controller._step()
        self.assertFalse(self.controller.accepts_pcm(1))
        self.assertFalse(self.bridge.muted)
        self.assertEqual(self.controller.snapshot().state, "error")

    def test_fresh_surface_shows_confirmed_group_while_new_device_connects(self):
        self.establish()
        self.controller.toggle("two")
        self.controller._step()
        route = self.controller.snapshot()
        self.assertEqual(connected_output_kinds(route), ("speaker", "homepod"))
        tracker = OutputMembershipTracker()
        self.assertEqual(tuple(d.id for d in tracker.update(route).current.members), ("one", "airplay:one"))


if __name__ == "__main__":
    unittest.main()
