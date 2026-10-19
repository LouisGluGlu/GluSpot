"""Controller contracts for preserving an established owned PCM epoch."""
from dataclasses import replace
from types import SimpleNamespace
import threading
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
    def prepare_direct(self, epoch, token=None):
        self.calls.append(("prepare", epoch))
        self.muted = self.commit_ok
        return self.commit_ok and (token is None or token == self.token)


class ReservationBridge(HandoffBridge):
    """Keep the real bridge's epoch fence without creating Qt objects."""
    def __init__(self):
        super().__init__()
        self.direct_epoch = 0
        self.held = False

    def prepare_direct(self, epoch, token=None):
        accepted = super().prepare_direct(epoch, token)
        if accepted:
            self.direct_epoch, self.held = epoch, True
        return accepted

    def set_direct(self, enabled, epoch, token=None, *, muted=True):
        self.calls.append(("direct", enabled, epoch, muted))
        if enabled:
            if token is not None and token != self.token:
                return False
            self.direct_epoch, self.held, self.muted = epoch, False, muted
            return True
        if self.direct_epoch and epoch not in {0, self.direct_epoch}:
            return False
        self.direct_epoch, self.held, self.muted = 0, False, False
        return True


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

    def test_source_changes_while_preparing_does_not_forget_homepod_selection(self):
        self.establish(("airplay:one",))
        self.bridge.token = ("owned:netease:one", 2)
        prepare = self.bridge.prepare_direct

        def source_finishes_changing(epoch, token=None):
            self.bridge.token = ("owned:netease:two", 2)
            return prepare(epoch, token)

        self.bridge.prepare_direct = source_finishes_changing
        self.controller.media_changed(("owned:netease:one", 2))
        self.controller._step()
        self.assertEqual(self.controller._members, ("airplay:one",))
        self.assertNotEqual(self.controller.snapshot().state, "error")
        self.assertFalse(any(call[0] == "stop" for call in self.sender.calls))
        self.bridge.prepare_direct = prepare
        self.controller._step()
        self.assertEqual(self.sender.calls[-1], ("start", ("airplay:one",), 2))
        self.assertTrue(self.controller._running)
        self.assertEqual(self.controller._token, self.bridge.token)

    def test_first_homepod_selection_survives_source_change_during_preparation(self):
        prepare = self.bridge.prepare_direct

        def source_changes(epoch, token=None):
            self.bridge.token = ("owned:netease:two", 2)
            return prepare(epoch, token)

        self.bridge.prepare_direct = source_changes
        self.controller._choose(("airplay:one",))
        self.assertEqual(self.controller._members, ())
        self.assertEqual(self.controller._queued_members, ("airplay:one",))
        self.bridge.prepare_direct = prepare
        self.controller._step()
        self.assertTrue(self.controller._running)
        self.assertEqual(self.sender.calls[-1], ("start", ("airplay:one",), 1))
        self.assertEqual(self.controller._token, self.bridge.token)

    def test_superseded_successful_reservation_is_released_by_stop_close_and_solo(self):
        for action in ("stop", "close", "solo"):
            with self.subTest(action=action):
                self.bridge = ReservationBridge()
                self.controller = OwnedAudioOutputController(self.bridge, backend=self.backend,
                    discovery=self.discovery, sender=self.sender, start_worker=False)
                self.controller._inventory()
                self.establish(("airplay:one",))
                self.bridge.token = ("owned:netease:one", 2)
                prepare = self.bridge.prepare_direct

                def reserve_then_supersede(epoch, token=None):
                    accepted = prepare(epoch, token)
                    self.bridge.token = ("owned:netease:two", 2)
                    return accepted

                self.bridge.prepare_direct = reserve_then_supersede
                self.controller.media_changed(("owned:netease:one", 2))
                self.controller._step()
                self.assertEqual(self.controller._session_epoch, 1)
                self.assertEqual(self.bridge.direct_epoch, 2)
                self.assertTrue(self.bridge.held)
                self.assertTrue(self.bridge.muted)
                if action == "close":
                    self.controller.close()
                elif action == "stop":
                    self.controller._choose(())
                else:
                    self.controller._choose(("two",))
                self.assertFalse(self.bridge.held)
                self.assertFalse(self.bridge.muted)
                self.assertEqual(self.bridge.direct_epoch, 0)
                self.assertEqual(self.controller._preparation_epoch, 0)
                self.controller.close()

    def test_close_during_prepare_reply_cancels_exact_reservation_without_start(self):
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                self.setUp()
                prepare = self.bridge.prepare_direct

                def close_before_reply(epoch, token=None):
                    prepare(epoch, token)
                    self.controller.close()
                    return accepted

                self.bridge.prepare_direct = close_before_reply
                self.controller._choose(("airplay:one",))
                self.assertTrue(self.controller._closed.is_set())
                self.assertFalse(self.controller._running)
                self.assertEqual(self.controller._members, ())
                self.assertEqual(self.controller._preparation_epoch, 0)
                self.assertFalse(self.bridge.muted)
                self.assertFalse(any(call[0] == "start" for call in self.sender.calls))
                self.assertNotEqual(self.controller.snapshot().state, "connecting")

    def test_close_after_old_native_teardown_prevents_replacement_start(self):
        self.establish(("airplay:one",))
        self.controller._thread = SimpleNamespace(join=lambda *_: self.fail("close must not join Qt"))
        stop = self.sender.stop

        def teardown_then_close():
            stop()
            self.controller.close()

        self.sender.stop = teardown_then_close
        self.controller.media_changed(self.bridge.token, force=True)
        self.controller._step()
        self.assertTrue(self.controller._closed.is_set())
        self.assertFalse(self.controller._running)
        self.assertFalse(self.bridge.muted)
        self.assertEqual(self.sender.calls, [("stop",)])
        self.assertNotEqual(self.controller.snapshot().state, "connecting")

    def test_close_during_native_spawn_retires_new_helper_on_worker(self):
        start = self.sender.start_pcm

        def start_then_close(devices, epoch):
            start(devices, epoch)
            self.controller.close()

        self.sender.start_pcm = start_then_close
        self.controller._choose(("airplay:one",))
        self.assertTrue(self.controller._closed.is_set())
        self.assertFalse(self.controller._running)
        self.assertEqual(self.controller._members, ())
        self.assertEqual(self.controller._preparation_epoch, 0)
        self.assertFalse(self.bridge.muted)
        self.assertEqual(self.sender.calls, [("start", ("airplay:one",), 1), ("stop",)])
        self.assertNotEqual(self.controller.snapshot().state, "connecting")

    def test_close_does_not_wait_for_an_inflight_preparation_worker(self):
        entered, resume = threading.Event(), threading.Event()
        prepare = self.bridge.prepare_direct

        def wait_for_close(epoch, token=None):
            accepted = prepare(epoch, token)
            entered.set()
            if not resume.wait(2.):
                raise AssertionError("fixture worker was not released")
            return accepted

        self.bridge.prepare_direct = wait_for_close
        self.controller._thread = worker = threading.Thread(
            target=lambda: self.controller._choose(("airplay:one",)))
        worker.start()
        try:
            self.assertTrue(entered.wait(2.))
            self.controller.close()
            self.assertTrue(worker.is_alive())
        finally:
            resume.set()
            worker.join(2.)
        self.assertFalse(worker.is_alive())
        self.assertFalse(self.controller._running)
        self.assertEqual(self.controller._members, ())
        self.assertFalse(self.bridge.muted)
        self.assertFalse(any(call[0] == "start" for call in self.sender.calls))

    def test_close_during_prepare_does_not_poll_ready_old_helper_or_remute(self):
        self.controller._choose(("airplay:one",))
        entered, resume = threading.Event(), threading.Event()
        prepare = self.bridge.prepare_direct

        def wait_for_close(epoch, token=None):
            accepted = prepare(epoch, token)
            entered.set()
            if not resume.wait(2.):
                raise AssertionError("fixture worker was not released")
            return accepted

        self.bridge.prepare_direct = wait_for_close
        self.controller.media_changed(self.bridge.token, force=True)
        self.controller._thread = worker = threading.Thread(target=self.controller._step)
        worker.start()
        try:
            self.assertTrue(entered.wait(2.))
            self.controller.close()
            self.assertTrue(worker.is_alive())
            self.assertFalse(self.bridge.muted)
            snapshot = self.controller.snapshot()
            self.sender.events = [{"event": "waiting_audio"}]
        finally:
            resume.set()
            worker.join(2.)
        self.assertFalse(worker.is_alive())
        self.assertFalse(self.bridge.muted)
        self.assertEqual(self.controller._feeding_epoch, 0)
        self.assertEqual(self.controller.snapshot(), snapshot)
        self.assertEqual(self.sender.events, [{"event": "waiting_audio"}])
        self.controller._poll()  # A direct late caller obeys the same fence.
        self.assertFalse(self.bridge.muted)
        self.assertEqual(self.controller.snapshot(), snapshot)
        self.assertEqual(self.sender.events, [{"event": "waiting_audio"}])
        self.controller._stop_group()  # Production _run performs this in finally.

    def test_successful_reservation_superseded_before_return_keeps_latest_output_intent(self):
        self.establish(("airplay:one",))
        prepare = self.bridge.prepare_direct

        def reserve_then_change_source(epoch, token=None):
            result = prepare(epoch, token)
            self.bridge.token = ("owned:netease:two", 2)
            return result

        self.bridge.prepare_direct = reserve_then_change_source
        self.controller._choose(("airplay:one", "two"), restart=True)
        self.assertEqual(self.controller._members, ("airplay:one",))
        self.assertEqual(self.controller._queued_members, ("airplay:one", "two"))
        self.assertEqual(self.sender.calls, [])
        self.bridge.prepare_direct = prepare
        self.controller._step()
        self.assertEqual(self.sender.calls[-1], ("start", ("airplay:one", "two"), 2))

    def test_source_changes_during_old_helper_teardown_never_start_obsolete_song(self):
        self.establish(("airplay:one",))
        stop = self.sender.stop

        def stop_then_change_source():
            stop()
            self.bridge.token = ("owned:netease:three", 3)

        self.sender.stop = stop_then_change_source
        self.bridge.token = ("owned:netease:two", 2)
        self.controller.media_changed(self.bridge.token)
        self.controller._step()
        self.assertEqual(self.sender.calls, [("stop",)])
        self.assertEqual(self.controller._members, ("airplay:one",))
        self.assertEqual(self.controller.snapshot().pending_ids, ("airplay:one",))
        self.sender.stop = stop
        self.controller._step()
        self.assertEqual(self.sender.calls[-1], ("start", ("airplay:one",), 2))
        self.assertEqual(self.controller._token, ("owned:netease:three", 3))

    def test_source_changes_while_waiting_audio_enables_pcm_keeps_selection(self):
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                self.bridge.token = ("owned:netease:one", 1)
                self.controller._choose(("airplay:one",))
                self.sender.calls.clear()
                direct = self.bridge.set_direct

                def change_before_reply(enabled, epoch, token=None, *, muted=True):
                    self.bridge.token = ("owned:netease:two", 2)
                    return accepted

                self.bridge.set_direct = change_before_reply
                self.sender.events = [{"event": "waiting_audio"}]
                self.controller._step()
                self.assertEqual(self.controller._members, ("airplay:one",))
                self.assertTrue(self.controller._running)
                self.assertEqual(self.controller._feeding_epoch, 0)
                self.assertNotEqual(self.controller.snapshot().state, "error")
                self.assertEqual(self.sender.calls, [])
                self.bridge.set_direct = direct
                self.controller._step()
                self.assertEqual(self.sender.calls[-1][0:2], ("start", ("airplay:one",)))
                self.assertEqual(self.controller._token, self.bridge.token)

    def test_superseded_poll_queues_latest_source_without_losing_selection(self):
        self.establish(("airplay:one",))
        self.bridge.token = ("owned:netease:two", 2)
        self.controller._poll()
        self.assertEqual(self.sender.calls, [])
        self.assertEqual(self.controller._members, ("airplay:one",))
        self.controller._step()
        self.assertEqual(self.controller._token, self.bridge.token)
        self.assertEqual(self.sender.calls[-1], ("start", ("airplay:one",), 2))

    def test_actual_helper_failures_remain_terminal_while_source_changes(self):
        for failure in ("pipe_failed", "error", "stopped"):
            with self.subTest(failure=failure):
                self.bridge.token = ("owned:netease:one", 1)
                self.sender.error = ""
                self.establish(("airplay:one",))
                self.bridge.token = ("owned:netease:two", 2)
                if failure == "pipe_failed":
                    self.sender.error = failure
                else:
                    self.sender.events = [{"event": failure, "message": "transport failure"}]
                self.controller._poll()
                self.assertFalse(self.controller._running)
                self.assertEqual(self.controller._members, ())
                self.assertEqual(self.controller.snapshot().state, "error")
                self.assertEqual(self.controller._feeding_epoch, 0)
                self.assertIn(("direct", False, self.controller._session_epoch, True), self.bridge.calls)

    def test_explicit_stop_cancels_a_superseded_media_retry(self):
        self.establish(("airplay:one",))
        self.bridge.token = ("owned:netease:two", 2)
        self.controller._poll()
        self.controller.stop_cast()
        self.controller._step()
        self.sender.calls.clear()
        self.controller._step()
        self.assertFalse(self.controller._running)
        self.assertEqual(self.controller._members, ())
        self.assertEqual(self.sender.calls, [])

    def test_rapid_superseded_sources_coalesce_then_connect_only_latest_song(self):
        self.establish(("airplay:one",))
        prepare = self.bridge.prepare_direct
        for generation in range(2, 26):
            self.bridge.token = ("intermediate", generation)

            def supersede(epoch, token=None):
                self.bridge.token = (f"owned:netease:{generation}", generation)
                return prepare(epoch, token)

            self.bridge.prepare_direct = supersede
            self.controller.media_changed(("intermediate", generation))
            self.controller._step()
            self.assertEqual(self.controller._members, ("airplay:one",))
            self.assertEqual(len(self.controller._commands), 1)
            self.assertEqual(self.sender.calls, [])
        self.bridge.prepare_direct = prepare
        self.controller._step()
        self.assertEqual(self.sender.calls, [("stop",), ("start", ("airplay:one",), 2)])
        self.assertEqual(self.controller._token, ("owned:netease:25", 25))

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

    def test_first_group_is_muted_before_preparation_and_through_presentation(self):
        self.sender.presentation = True
        self.controller._choose(("airplay:one",))
        self.assertTrue(self.bridge.muted)
        self.assertIn(("prepare", 1), self.bridge.calls)
        self.sender.events = [{"event": "waiting_audio"}, self.signal()]
        self.controller._step()
        self.assertTrue(self.bridge.muted)
        self.assertTrue(self.controller.accepts_pcm(1))
        self.sender.events = [{"event": "streaming"}]
        self.controller._step()
        self.assertTrue(self.bridge.muted)
        self.assertNotIn(("direct", True, 1, False), self.bridge.calls)

    def test_preparation_failure_restores_qt_and_revokes_pcm(self):
        self.sender.presentation = True
        self.bridge.commit_ok = False
        self.controller._choose(("airplay:one",))
        self.assertFalse(self.bridge.muted)
        self.assertFalse(self.controller.accepts_pcm(1))
        self.assertEqual(self.controller.snapshot().state, "error")

    def test_expired_signal_cannot_confirm_later_presentation_or_release_mute(self):
        self.sender.presentation = True
        self.controller._choose(("airplay:one",))
        stamp = time.monotonic()
        with patch("api.owned_audio_outputs.time.monotonic", return_value=stamp):
            self.sender.events = [{"event": "waiting_audio"}, self.signal()]
            self.controller._step()
        with patch("api.owned_audio_outputs.time.monotonic", return_value=stamp + 2.1):
            self.sender.events = [{"event": "streaming"}]
            self.controller._step()
        self.assertTrue(self.bridge.muted)
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

    def test_track_restart_preserves_owned_mute_without_transient_local_output(self):
        self.establish()
        self.bridge.calls.clear()
        self.bridge.token = ("second-song", 2)
        self.controller.media_changed(self.bridge.token)
        self.controller._step()
        self.assertTrue(self.bridge.muted)
        self.assertEqual(self.bridge.calls, [("prepare", 2)])
        self.sender.events = [{"event": "waiting_audio"}, {"event": "streaming"}, self.signal()]
        self.controller._step()
        self.assertTrue(self.bridge.muted)
        self.assertEqual(self.controller.snapshot().state, "casting")
        self.assertFalse(any(c[0] == "direct" and (not c[1] or not c[3]) for c in self.bridge.calls))

    def test_cold_membership_restart_keeps_mute_and_failure_restores_it(self):
        self.establish()
        self.sender.supports_pcm_reconfigure = lambda epoch: False
        self.bridge.calls.clear()
        self.controller.toggle("two")
        self.controller._step()
        self.assertTrue(self.bridge.muted)
        self.assertEqual(self.bridge.calls, [("prepare", 2)])
        self.sender.events = [{"event": "error"}]
        self.controller._step()
        self.assertFalse(self.bridge.muted)
        self.assertEqual(self.controller.snapshot().state, "error")

    def test_helper_start_failure_releases_reserved_output(self):
        self.sender.start_pcm = lambda *_: (_ for _ in ()).throw(OSError("fixture start failure"))
        self.controller._choose(("airplay:one",))
        self.assertFalse(self.bridge.muted)
        self.assertFalse(self.controller._running)
        self.assertEqual(self.controller.snapshot().state, "error")

    def test_late_prepare_timeout_releases_exact_new_reservation(self):
        self.establish()
        def timed_out_prepare(epoch, token=None):
            self.bridge.calls.append(("prepare-timeout", epoch))
            self.bridge.muted = True
            return False
        self.bridge.prepare_direct = timed_out_prepare
        self.controller.media_changed(self.bridge.token, force=True)
        self.controller._step()
        self.assertFalse(self.bridge.muted)
        self.assertIn(("direct", False, 2, True), self.bridge.calls)
        self.assertFalse(self.controller._running)

    def test_old_helper_stop_exception_releases_new_reservation(self):
        self.establish()
        self.sender.stop = lambda: (_ for _ in ()).throw(OSError("fixture stop failure"))
        self.controller.media_changed(self.bridge.token, force=True)
        self.controller._step()
        self.assertFalse(self.bridge.muted)
        self.assertIn(("direct", False, 2, True), self.bridge.calls)
        self.assertFalse(self.controller._running)

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
