from __future__ import annotations

import threading
import time
import unittest
import json
from pathlib import Path
import tempfile
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest.mock import patch

from api.airplay_discovery import AirPlayDiscovery, SERVICE_TYPES, receiver_from_service
from api.audio_route_controller import AudioRouteController
from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from api.windows_audio_devices import AudioOutputDevice
from api.windows_audio_sessions import AudioSessionEvidence, MusicAudioSource, ProcessIdentity
from api.windows_app_audio_routes import WindowsAppAudioRoutes, _PREFIX, _RENDER_SUFFIX
from api.airplay_group_process import AirPlayGroupProcess


HOME = AudioRouteDevice("airplay:AABBCCDDEEFF", "书房 HomePod", "homepod-mini", "airplay",
                       host="192.0.2.10", port=7000, device_id="AABBCCDDEEFF")
OTHER_HOME = replace(HOME, id="airplay:112233445566", name="客厅 HomePod", host="192.0.2.11")


class _Backend:
    def __init__(self):
        self.outputs = (AudioOutputDevice("speakers", "电脑扬声器", "speaker", False, True),
                        AudioOutputDevice("headphones", "蓝牙耳机", "headphones", True))
        self.selected = []
        self.error = ""
        self.closed = False
        self.mutes = {"speakers": False, "headphones": False}
        self.mute_calls = []
        self.volumes = {"speakers": 0.6, "headphones": 0.25}
        self.volume_calls = []
        self.volume_error = ""

    def list_outputs(self):
        if self.error:
            raise OSError(self.error)
        return tuple(replace(d, volume=self.volumes.get(d.id), volume_muted=self.mutes.get(d.id, False))
                     for d in self.outputs)

    def set_default(self, identity):
        self.selected.append(identity)
        self.outputs = tuple(replace(d, is_default=d.id == identity) for d in self.outputs)

    def close(self):
        self.closed = True

    def get_endpoint_mute(self, identity):
        return self.mutes[identity]

    def set_endpoint_mute(self, identity, muted):
        self.mute_calls.append((identity, muted))
        self.mutes[identity] = muted

    def get_endpoint_volume(self, identity):
        return self.volumes[identity]

    def set_endpoint_volume(self, identity, volume):
        if self.volume_error:
            raise OSError(self.volume_error)
        self.volume_calls.append((identity, volume))
        self.volumes[identity] = volume


class _Discovery:
    state = "idle"

    def __init__(self):
        self.receivers = (HOME, OTHER_HOME)
        self.starts = 0
        self.closed = False

    def devices(self):
        return self.receivers

    def start(self):
        self.starts += 1
        self.state = "browsing"

    def close(self):
        self.closed = True


class _Sender:
    def __init__(self):
        self.starts = []
        self.stops = 0
        self.events = []
        self.running = False
        self.start_error = ""
        self.stop_error = ""
        self.needs_local_output_restore = False
        self.volume_state = {"volume": None, "known": False, "source": "unknown", "pending": False, "last_code": 0}
        self.volume_calls = []
        self.volume_accept = True

    def start(self, device, source):
        if self.start_error:
            raise RuntimeError(self.start_error)
        self.starts.append((device.id, source))
        self.events = []
        self.running = True
        self.volume_state = {"volume": None, "known": False, "source": "unknown", "pending": False, "last_code": 0}

    def stop(self):
        self.stops += 1
        self.running = False
        self.events = []
        self.volume_state = {"volume": None, "known": False, "source": "unknown", "pending": False, "last_code": 0}
        if self.stop_error:
            raise OSError(self.stop_error)

    def poll_events(self):
        events, self.events = tuple(self.events), []
        return events

    def receiver_volume_state(self):
        return dict(self.volume_state)

    def set_volume(self, value):
        self.volume_calls.append(value)
        self.volume_state["pending"] = True
        return self.volume_accept


class _GroupSender(_Sender):
    def __init__(self):
        super().__init__()
        self.volume_states = {}
        self.member_states = {}
        self.estimate = None
        self.source_evidence = {}

    def start_group(self, devices, source, endpoint):
        AirPlayGroupProcess._validate_group(devices, source, endpoint)
        if self.start_error:
            raise RuntimeError(self.start_error)
        self.starts.append((tuple(device.id for device in devices), endpoint,
                            source.process_id, source.process_created_at_100ns))
        self.running, self.events = True, []
        self.volume_states = {}

    def receiver_volume_state(self, identity):
        return dict(self.volume_states.get(identity, {}))

    def receiver_states(self):
        return dict(self.member_states)

    def set_device_volume(self, identity, volume):
        self.volume_calls.append((identity, volume))
        return self.volume_accept

    def latency_estimate(self):
        return self.estimate

    def diagnostics(self):
        return {"local_output": dict(self.source_evidence), "schema_version": 1}

    @staticmethod
    def ready_events():
        return [{"event": "waiting_audio"}, {"event": "stats", "_received_at": time.monotonic(),
            "capture_signal": {"mode": "process_loopback", "scope": "music_process_tree", "peak": .3,
                "nonzero_samples": 100, "last_nonzero_age_ms": 0,
                "meaningful_samples": 80, "last_meaningful_age_ms": 0}}, {"event": "streaming"}]

    def stop(self):
        super().stop()
        self.volume_states = {}


class _AppRouter:
    def __init__(self, read_source, write_source):
        self.read_source, self.write_source = read_source, write_source
        self.allow = False
        self.calls = []
        self.guards = []
        self.during_wait = None
        self.wait_error = ""
        self.check_error = ""
        self.restore_error = ""
        self.occupied = set()
        self.pending = None

    def recover_pending(self):
        self.calls.append(("recover",))
        return False

    def pending_recovery(self):
        return dict(self.pending) if self.pending is not None else None

    def retire_resume_observation(self):
        self.calls.append(("retire",))
        if self.pending is not None and self.pending.get("stage") != "resume_verification_pending":
            raise RuntimeError("original music route writes still require recovery")
        retired, self.pending = self.pending is not None, None
        return retired

    def endpoint_is_unoccupied(self, endpoint, source):
        return endpoint not in self.occupied

    def prepare(self, source, spare, local):
        self.calls.append(("prepare", spare, local))
        if not self.allow or spare in self.occupied:
            raise RuntimeError("spare occupied")
        guard = _AppGuard(self, source, spare)
        self.guards.append(guard)
        return guard

    def close(self, *, timeout=5.0):
        self.calls.append(("close", timeout))


class _AppGuard:
    def __init__(self, owner, source, spare):
        self.owner, self.source, self.spare_endpoint = owner, source, spare
        self.restored = False

    def apply(self):
        self.owner.calls.append(("apply", self.spare_endpoint))

    def wait_migrated(self, *, timeout=5.0, cancelled=None):
        self.owner.calls.append(("wait", timeout))
        if self.owner.during_wait:
            self.owner.during_wait()
        if cancelled is not None and cancelled():
            raise RuntimeError("migration cancelled")
        if self.owner.wait_error:
            raise RuntimeError(self.owner.wait_error)
        fresh = replace(self.source, sessions=tuple(replace(session, endpoint_id=self.spare_endpoint)
                                                   for session in self.source.sessions))
        self.owner.write_source(fresh)
        return fresh

    def check(self):
        self.owner.calls.append(("check",))
        if self.owner.check_error:
            raise RuntimeError(self.owner.check_error)

    def restore(self):
        self.owner.calls.append(("restore", self.spare_endpoint))
        if self.owner.restore_error:
            raise RuntimeError(self.owner.restore_error)
        if not self.restored:
            self.owner.write_source(self.source)
            self.restored = True


class AudioRouteControllerTests(unittest.TestCase):
    def setUp(self):
        self.backend, self.discovery, self.sender = _Backend(), _Discovery(), _Sender()
        self.router = _AppRouter(lambda: None, lambda _source: None)
        self.controller = AudioRouteController(self.backend, self.discovery, self.sender,
            app_router=self.router, start_worker=False)
        self.addCleanup(self.controller.close)
        self.controller._refresh_devices()

    def _cast(self, device=HOME):
        self.assertTrue(self.controller.select(device.id))
        self.controller._step()
        self.sender.events = [{"event": "streaming"}]
        self.controller._step()
        return self.controller.snapshot()

    def test_snapshots_are_immutable_and_initial_baseline_does_not_announce_old_devices(self):
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot.active_id, "speakers")
        self.assertEqual(snapshot.state, "ready")
        self.assertEqual(snapshot.event_serial, 0)
        with self.assertRaises(FrozenInstanceError):
            snapshot.state = "casting"
        self.assertEqual({d.id for d in snapshot.devices}, {"speakers", "headphones", HOME.id, OTHER_HOME.id})

    def test_passive_monitoring_does_not_start_network_or_sender(self):
        self.controller._refresh_devices()
        self.controller._step()
        self.assertEqual(self.discovery.starts, 0)
        self.assertEqual(self.sender.starts, [])
        self.assertEqual(self.backend.selected, [])

    def test_connected_bluetooth_announces_once_and_does_not_repeat_on_poll(self):
        new = AudioOutputDevice("new-headset", "新耳机", "headset", True)
        self.backend.outputs += (new,)
        self.controller._refresh_devices()
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot.event_name, "新耳机")
        self.assertIn("蓝牙", snapshot.event_message)
        serial = snapshot.event_serial
        self.controller._refresh_devices()
        self.assertEqual(self.controller.snapshot().event_serial, serial)

    def test_external_default_changes_are_observed_without_output_mutations(self):
        self.backend.outputs = tuple(replace(d, is_default=d.id == "headphones") for d in self.backend.outputs)
        self.controller._refresh_devices()
        self.assertEqual(self.controller.snapshot().active_id, "headphones")
        self.assertEqual(self.backend.selected, [])

    def test_select_local_output_requires_successful_backend_change(self):
        self.assertTrue(self.controller.select("headphones"))
        self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertEqual(self.backend.selected, ["headphones"])
        self.assertEqual(snapshot.active_id, "headphones")
        self.assertEqual(snapshot.pending_id, "")
        self.assertEqual(snapshot.state, "ready")
        self.assertEqual(snapshot.event_message, "已切换音频输出")

    def test_local_failure_preserves_real_default_and_surfaces_reason(self):
        def fail(_identity):
            raise OSError("policy denied")

        self.backend.set_default = fail
        self.controller.select("headphones")
        self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot.active_id, "speakers")
        self.assertEqual(snapshot.state, "error")
        self.assertIn("policy denied", snapshot.status_message)
        self.assertEqual(snapshot.event_serial, 0)

    def test_device_disconnected_before_selection_has_no_backend_write(self):
        self.controller.select("headphones")
        self.backend.outputs = (self.backend.outputs[0],)
        self.controller._step()
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.controller.snapshot().state, "error")

    def test_unknown_device_cannot_be_queued(self):
        self.assertFalse(self.controller.select("not-present"))
        self.controller._step()
        self.assertEqual(self.backend.selected, [])

    def test_rapid_selection_keeps_only_latest_queued_target(self):
        self.controller.select(HOME.id)
        self.controller.select(OTHER_HOME.id)
        self.controller.select("headphones")
        self.controller._step()
        self.assertEqual(self.backend.selected, ["headphones"])
        self.assertEqual(self.sender.starts, [])

    def test_stop_supersedes_queued_cast_without_connecting(self):
        self.controller.select(HOME.id)
        self.controller.stop_cast()
        self.controller._step()
        self.assertEqual(self.sender.starts, [])
        self.assertEqual(self.controller.snapshot().pending_id, "")

    def test_connecting_does_not_claim_audio_streaming_or_apply_lyric_latency(self):
        self.controller.select(HOME.id)
        self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertEqual(self.sender.starts, [(HOME.id, "speakers")])
        self.assertEqual(snapshot.state, "connecting")
        self.assertEqual(snapshot.pending_id, HOME.id)
        self.assertEqual(snapshot.cast_latency_seconds, 0.0)
        self.assertEqual(snapshot.cast_source_id, "speakers")
        self.assertEqual(self.backend.selected, [])

    def test_streaming_event_confirms_target_and_starts_automatic_protocol_delay(self):
        self.assertFalse(self.controller.set_latency(2.25))
        self.controller._step()
        snapshot = self._cast()
        self.assertEqual(snapshot.active_id, HOME.id)
        self.assertEqual(snapshot.state, "casting")
        self.assertEqual(snapshot.pending_id, "")
        self.assertEqual(snapshot.cast_latency_seconds, 1.5)
        self.assertTrue(snapshot.cast_latency_auto)

    def test_handshake_waiting_for_pcm_keeps_pending_target_without_lyric_delay(self):
        self.controller.select(HOME.id)
        self.controller._step()
        self.sender.events = [{"event": "waiting_audio"}]
        self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot.state, "waiting_audio")
        self.assertEqual(snapshot.pending_id, HOME.id)
        self.assertEqual(snapshot.cast_latency_seconds, 0.0)
        self.assertEqual(snapshot.event_serial, 0)
        self.controller.select(HOME.id)
        self.controller._step()
        self.assertEqual(self.sender.starts, [(HOME.id, "speakers")])
        self.sender.events = [{"event": "streaming"}]
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "casting")

    @staticmethod
    def _capture_stats(nonzero=0, age=None, *, peak=0.0, received=100.0,
                       mode="endpoint_loopback", scope="selected_endpoint"):
        return {"event": "stats", "_received_at": received, "capture_signal": {
            "nonzero_samples": nonzero, "last_nonzero_age_ms": age,
            "peak": peak, "mode": mode, "scope": scope,
        }}

    def _poll_at(self, now, *events):
        self.sender.events = list(events)
        with patch("api.audio_route_controller.time.monotonic", return_value=now):
            self.controller._step()
        return self.controller.snapshot()

    def _waiting_at(self, now=100.0, device=HOME):
        with patch("api.audio_route_controller.time.monotonic", return_value=now):
            self.controller.select(device.id)
            self.controller._step()
        return self._poll_at(now, {"event": "waiting_audio"})

    def test_authenticated_zero_capture_warns_after_three_seconds_without_routing_or_streaming(self):
        self._waiting_at()
        self._poll_at(100.0, self._capture_stats(received=100.0))
        starts, stops = tuple(self.sender.starts), self.sender.stops
        before = self._poll_at(102.999, self._capture_stats(received=102.999))
        self.assertEqual(before.status_message, "已连接 HomePod，等待电脑播放声音")
        silent = self._poll_at(103.0, self._capture_stats(received=103.0))
        self.assertEqual(silent.status_message, self.controller.CAPTURE_SILENCE_MESSAGE)
        self.assertEqual(silent.state, "waiting_audio")
        self.assertEqual(silent.active_id, "")
        self.assertEqual(silent.pending_id, HOME.id)
        self.assertEqual(silent.cast_latency_seconds, 0.0)
        self.assertEqual(silent.event_serial, 0)
        self.assertEqual(tuple(self.sender.starts), starts)
        self.assertEqual(self.sender.stops, stops)
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.mute_calls, [])

    def test_capture_resuming_only_clears_warning_and_native_streaming_still_owns_connection(self):
        self._waiting_at()
        self._poll_at(100.0, self._capture_stats(received=100.0))
        self._poll_at(103.0, self._capture_stats(received=103.0))
        resumed = self._poll_at(103.5, self._capture_stats(20, 10, peak=.2, received=103.5))
        self.assertEqual(resumed.state, "waiting_audio")
        self.assertEqual(resumed.status_message, "已连接 HomePod，等待电脑播放声音")
        self.assertEqual(resumed.pending_id, HOME.id)
        self.assertEqual(resumed.event_serial, 0)
        confirmed = self._poll_at(103.6, {"event": "streaming"})
        self.assertEqual(confirmed.state, "casting")
        self.assertEqual(confirmed.status_message, f"正在投放到 {HOME.name}")
        self.assertEqual(confirmed.event_serial, 1)

    def test_casting_silent_passage_reports_evidence_and_recovers_without_restart_or_seek(self):
        with patch("api.audio_route_controller.time.monotonic", return_value=100.0):
            self._cast()
        starts, stops = tuple(self.sender.starts), self.sender.stops
        before = self._poll_at(103.0, self._capture_stats(20, 2999, received=103.0))
        self.assertEqual(before.status_message, f"正在投放到 {HOME.name}")
        silent = self._poll_at(103.1, self._capture_stats(20, 3000, received=103.1))
        self.assertEqual(silent.status_message, self.controller.CAPTURE_SILENCE_MESSAGE)
        self.assertEqual(silent.state, "casting")
        self.assertEqual(silent.active_id, HOME.id)
        self.assertEqual(silent.cast_latency_seconds, 1.5)
        resumed = self._poll_at(103.5, self._capture_stats(40, 5, peak=.1, received=103.5))
        self.assertEqual(resumed.status_message, f"正在投放到 {HOME.name}")
        self.assertEqual(resumed.event_serial, before.event_serial)
        self.assertEqual(tuple(self.sender.starts), starts)
        self.assertEqual(self.sender.stops, stops)
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.mute_calls, [])

    def test_capture_stats_never_authenticate_a_connecting_receiver(self):
        with patch("api.audio_route_controller.time.monotonic", return_value=100.0):
            self.controller.select(HOME.id)
            self.controller._step()
        current = self.controller.snapshot()
        observed = self._poll_at(104.0, self._capture_stats(20, 0, peak=.2, received=104.0))
        self.assertEqual(observed, current)
        self.assertIsNone(self.controller._capture_signal_nonzero)

    def test_old_helper_without_capture_signal_keeps_its_existing_status(self):
        self._waiting_at()
        current = self.controller.snapshot()
        self.assertEqual(self._poll_at(110.0, {"event": "stats", "captured_frames": 48000}), current)
        self._poll_at(110.1, self._capture_stats(received=110.1))
        self.assertEqual(self.controller.snapshot(), current)
        self._poll_at(113.1, self._capture_stats(received=113.1))
        silent = self.controller.snapshot()
        self.assertEqual(self._poll_at(113.2, {"event": "stats", "captured_frames": 96000}), silent)

    def test_invalid_capture_signal_cannot_claim_silence_or_sound(self):
        self._waiting_at()
        current = self.controller.snapshot()
        invalid = (
            {"nonzero_samples": True}, {"nonzero_samples": -1}, {"nonzero_samples": "0"},
            {"nonzero_samples": 2**64}, {"nonzero_samples": 1},
            {"last_nonzero_age_ms": 0}, {"last_nonzero_age_ms": -1},
            {"last_nonzero_age_ms": True}, {"last_nonzero_age_ms": float("nan")},
            {"last_nonzero_age_ms": float("inf")}, {"last_nonzero_age_ms": 10**1000},
            {"peak": True}, {"peak": -.1}, {"peak": 1.1}, {"peak": "0"},
            {"peak": float("nan")}, {"peak": float("inf")}, {"peak": 10**1000},
            {"peak": .1}, {"mode": "microphone"}, {"scope": "one_player"},
            {"mode": "process_loopback"},
        )
        for override in invalid:
            with self.subTest(override=override):
                event = self._capture_stats(received=104.0)
                event["capture_signal"].update(override)
                self.assertEqual(self._poll_at(104.0, event), current)
                self.assertIsNone(self.controller._capture_signal_nonzero)
        for signal in (None, [], "silent", {}):
            with self.subTest(signal=signal):
                self.assertEqual(self._poll_at(104.0, {"event": "stats", "capture_signal": signal}), current)

    def test_process_capture_evidence_has_same_status_contract_as_endpoint_capture(self):
        self._waiting_at()
        self._poll_at(100.0, self._capture_stats(received=100.0,
                      mode="process_loopback", scope="all_other_processes"))
        silent = self._poll_at(103.0, self._capture_stats(received=103.0,
                               mode="process_loopback", scope="all_other_processes"))
        self.assertEqual(silent.status_message, self.controller.CAPTURE_SILENCE_MESSAGE)
        self.assertEqual(silent.state, "waiting_audio")

    def test_stale_future_repeated_and_out_of_order_capture_stats_do_not_clear_warning(self):
        self._waiting_at()
        silent = self._poll_at(104.0, self._capture_stats(20, 4000, received=104.0))
        for received in (None, False, "104", float("nan"), float("inf"), 10**1000,
                         101.0, 104.0, 103.9, 104.6):
            with self.subTest(received=received):
                # None represents a missing timestamp on new signal evidence.
                event = self._capture_stats(30, 0, peak=.1, received=received)
                if received is None:
                    event.pop("_received_at")
                self.assertEqual(self._poll_at(104.5, event), silent)

    def test_same_session_counter_or_last_audio_age_regression_is_not_fresh_sound(self):
        self._waiting_at()
        silent = self._poll_at(104.0, self._capture_stats(20, 4000, received=104.0))
        for count in (0, 19, 20):
            with self.subTest(count=count):
                age = None if count == 0 else 0
                event = self._capture_stats(count, age, received=104.5)
                self.assertEqual(self._poll_at(104.5, event), silent)

    def test_new_receiver_fences_previous_signal_and_resets_counter_and_silence_grace(self):
        self._waiting_at()
        self._poll_at(104.0, self._capture_stats(20, 4000, received=104.0))
        self._waiting_at(105.0, OTHER_HOME)
        current = self.controller.snapshot()
        old = self._capture_stats(30, 0, peak=.1, received=104.5)
        self.assertEqual(self._poll_at(105.1, old), current)
        self.assertIsNone(self.controller._capture_signal_nonzero)
        self._poll_at(105.2, self._capture_stats(received=105.2))
        self.assertEqual(self._poll_at(107.9, self._capture_stats(received=107.9)), current)
        silent = self._poll_at(108.2, self._capture_stats(received=108.2))
        self.assertEqual(silent.status_message, self.controller.CAPTURE_SILENCE_MESSAGE)
        self.assertEqual(silent.pending_id, OTHER_HOME.id)

    def test_terminal_events_win_over_entire_capture_batch_without_false_connection_notice(self):
        for terminal in ("error", "stopped"):
            with self.subTest(terminal=terminal):
                with patch("api.audio_route_controller.time.monotonic", return_value=100.0):
                    self.controller.select(HOME.id)
                    self.controller._step()
                previous = self.controller.snapshot().event_serial
                result = self._poll_at(104.0, {"event": "streaming"},
                          self._capture_stats(received=104.0),
                          {"event": terminal, "message": "receiver disconnected"},
                          self._capture_stats(20, 0, peak=.1, received=104.0))
                self.assertEqual(result.state, "error")
                self.assertEqual(result.status_message, "receiver disconnected")
                self.assertEqual(result.event_serial, previous)
                self.assertEqual(result.active_id, "speakers")
                self.assertIsNone(self.controller._capture_authenticated_at)

    def test_poll_result_cannot_publish_capture_for_a_session_that_ended_during_poll(self):
        self._waiting_at()
        def end_during_poll():
            self.controller._end_cast()
            self.controller._publish(state="ready", status_message="HomePod 投放已停止")
            return (self._capture_stats(received=104.0), {"event": "streaming"})
        self.sender.poll_events = end_during_poll
        result = self._poll_at(104.0)
        self.assertEqual(result.state, "ready")
        self.assertEqual(result.status_message, "HomePod 投放已停止")
        self.assertEqual(result.active_id, "speakers")
        self.assertEqual(result.event_serial, 0)

    def test_capture_recovery_does_not_erase_an_unrelated_receiver_volume_error(self):
        self._waiting_at()
        self._poll_at(104.0, self._capture_stats(20, 4000, received=104.0))
        self.controller._publish(status_message="HomePod 音量调整失败：接收端返回 500")
        result = self._poll_at(104.5, self._capture_stats(40, 0, peak=.1, received=104.5))
        self.assertEqual(result.status_message, "HomePod 音量调整失败：接收端返回 500")

    def test_close_arriving_during_selection_refresh_prevents_sender_start(self):
        self.controller.select(HOME.id)
        original = self.backend.list_outputs

        def close_during_read():
            result = original()
            self.controller.close()
            return result

        self.backend.list_outputs = close_during_read
        self.controller._step()
        self.assertEqual(self.sender.starts, [])

    def test_same_receiver_selection_does_not_restart_healthy_sender(self):
        self._cast()
        self.controller.select(HOME.id)
        self.controller._step()
        self.assertEqual(self.sender.starts, [(HOME.id, "speakers")])

    def test_receiver_switch_stops_previous_and_uses_same_actual_source(self):
        self._cast()
        stops = self.sender.stops
        self.controller.select(OTHER_HOME.id)
        self.controller._step()
        self.assertGreater(self.sender.stops, stops)
        self.assertEqual(self.sender.starts[-1], (OTHER_HOME.id, "speakers"))
        self.assertEqual(self.controller.snapshot().state, "connecting")
        self.assertEqual(self.controller.snapshot().cast_latency_seconds, 0.0)

    def test_selecting_windows_output_stops_cast_and_clears_latency(self):
        self._cast()
        self.controller.select("headphones")
        self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertFalse(self.sender.running)
        self.assertEqual(snapshot.cast_source_id, "")
        self.assertEqual(snapshot.cast_latency_seconds, 0.0)
        self.assertEqual(snapshot.active_id, "headphones")

    def test_sender_failure_does_not_allow_later_events_in_same_batch_to_revive_cast(self):
        self._cast()
        self.sender.events = [{"event": "error", "message": "pairing refused"}, {"event": "streaming"}]
        self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot.state, "error")
        self.assertEqual(snapshot.active_id, "speakers")
        self.assertEqual(snapshot.cast_latency_seconds, 0.0)
        self.assertIn("pairing refused", snapshot.status_message)
        self.assertFalse(self.sender.running)

    def test_sender_start_failure_is_not_a_successful_connection(self):
        self.sender.start_error = "native engine missing"
        self.controller.select(HOME.id)
        self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot.state, "error")
        self.assertEqual(snapshot.active_id, "speakers")
        self.assertEqual(snapshot.pending_id, "")
        self.assertIn("native engine missing", snapshot.status_message)

    def test_no_local_audio_source_cannot_start_cast(self):
        self.backend.outputs = ()
        self.controller.select(HOME.id)
        self.controller._step()
        self.assertEqual(self.sender.starts, [])
        self.assertEqual(self.controller.snapshot().state, "error")

    def test_source_disconnection_ends_cast_and_clears_lyric_delay(self):
        self._cast()
        self.backend.outputs = (replace(self.backend.outputs[1], is_default=True),)
        self.controller._refresh_devices()
        snapshot = self.controller.snapshot()
        self.assertFalse(self.sender.running)
        self.assertEqual(snapshot.state, "error")
        self.assertEqual(snapshot.cast_latency_seconds, 0.0)
        self.assertEqual(snapshot.active_id, "headphones")

    def test_sender_stop_error_cannot_leave_remote_marked_as_active(self):
        self._cast()
        self.sender.stop_error = "stop pipe closed"
        with self.assertRaises(OSError):
            self.controller._end_cast()
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot.active_id, "speakers")
        self.assertEqual(snapshot.pending_id, "")
        self.assertEqual(snapshot.cast_source_id, "")
        self.assertEqual(snapshot.cast_latency_seconds, 0.0)

    def test_hard_killed_sender_restores_only_its_original_local_endpoint(self):
        self._cast()
        self.backend.mutes["speakers"] = True
        self.sender.needs_local_output_restore = True
        self.backend.set_default("headphones")
        self.controller.stop_cast()
        self.controller._step()
        self.assertEqual(self.backend.mute_calls, [("speakers", False)])
        self.assertEqual(self.controller.snapshot().active_id, "headphones")

    def test_fallback_preserves_original_mute_and_user_explicit_unmute(self):
        for originally_muted in (False, True):
            with self.subTest(originally_muted=originally_muted):
                self.backend.mutes["speakers"] = originally_muted
                self._cast()
                self.sender.needs_local_output_restore = True
                self.backend.mutes["speakers"] = originally_muted
                self.controller.stop_cast()
                self.controller._step()
                self.assertEqual(self.backend.mute_calls, [])

    def test_exit_after_hard_kill_restores_local_sound_before_backend_close(self):
        self._cast()
        self.backend.mutes["speakers"] = True
        self.sender.needs_local_output_restore = True
        self.controller.close()
        self.assertEqual(self.backend.mute_calls, [("speakers", False)])
        self.assertTrue(self.backend.closed)

    def test_mdns_disappearance_preserves_live_cast_but_marks_receiver_unavailable(self):
        self._cast()
        self.discovery.receivers = ()
        self.controller._merge_devices()
        snapshot = self.controller.snapshot()
        current = next(d for d in snapshot.devices if d.id == HOME.id)
        self.assertFalse(current.available)
        self.assertEqual(snapshot.state, "casting")
        self.assertFalse(self.controller.select(HOME.id))
        self.assertTrue(self.sender.running)

    def test_manual_target_is_address_only_and_deduplicates_a_discovered_host(self):
        self.assertTrue(self.controller.add_homepod(HOME.host, HOME.port))
        self.controller._step()
        devices = [d for d in self.controller.snapshot().devices if d.host == HOME.host]
        self.assertEqual(devices, [HOME])
        self.assertEqual(self.sender.starts, [])
        self.assertNotEqual(self.controller.snapshot().state, "casting")

    def test_invalid_manual_addresses_and_ports_are_rejected(self):
        for host, port in (("0.0.0.0", 7000), ("224.0.0.1", 7000), ("::1", 7000),
                           ("example.com", 7000), ("192.0.2.12", 0), ("192.0.2.12", 65536)):
            with self.subTest(host=host, port=port):
                self.assertFalse(self.controller.add_homepod(host, port))

    def test_legacy_manual_latency_cannot_change_automatic_compensation(self):
        for value in (float("nan"), float("inf"), None, -1, 99, 1.75):
            self.assertFalse(self.controller.set_latency(value))
            self.controller._step()
            self.assertEqual(self.controller._latency, 1.5)
            self.assertEqual(self.controller.snapshot().cast_latency_seconds, 0.0)
        self._cast()
        self.assertFalse(self.controller.set_auto_latency(False))
        self.controller._step()
        self.assertEqual(self.controller.snapshot().cast_latency_seconds, 1.5)
        self.assertTrue(self.controller.snapshot().cast_latency_auto)

    def test_legacy_constructor_options_do_not_restore_manual_runtime_mode(self):
        controller = AudioRouteController(_Backend(), _Discovery(), _Sender(),
            app_router=_AppRouter(lambda: None, lambda _source: None), start_worker=False,
            latency_seconds=3.2, auto_latency_enabled=False)
        self.addCleanup(controller.close)
        controller._refresh_devices()
        controller.select(HOME.id)
        controller._step()
        controller._sender.events = [{"event": "streaming"}]
        controller._step()
        self.assertTrue(controller.snapshot().cast_latency_auto)
        self.assertEqual(controller.snapshot().cast_latency_seconds, 1.5)

    def test_local_volume_is_actual_named_device_scalar_and_never_changes_selection(self):
        self.assertEqual([d.volume for d in self.controller.snapshot().devices[:2]], [0.6, 0.25])
        self.assertTrue(self.controller.set_volume("headphones", 0.75))
        self.assertEqual(self.backend.volume_calls, [])
        self.controller._step()
        changed = next(d for d in self.controller.snapshot().devices if d.id == "headphones")
        self.assertEqual(changed.volume, 0.75)
        self.assertEqual(self.backend.volume_calls, [("headphones", 0.75)])
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.mute_calls, [])
        self.assertEqual(self.controller.snapshot().active_id, "speakers")
        self.backend.volumes["headphones"] = 0.42
        self.controller._refresh_devices()
        self.assertEqual(next(d.volume for d in self.controller.snapshot().devices if d.id == "headphones"), 0.42)

    def test_volume_during_cast_preserves_local_mute_and_sender_session(self):
        self._cast()
        self.backend.mutes["speakers"] = True
        self.controller._refresh_devices()
        starts, stops = tuple(self.sender.starts), self.sender.stops
        self.assertTrue(self.controller.set_volume("speakers", 0.35))
        self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertTrue(self.backend.mutes["speakers"])
        self.assertEqual(self.backend.mute_calls, [])
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(tuple(self.sender.starts), starts)
        self.assertEqual(self.sender.stops, stops)
        self.assertEqual(snapshot.state, "casting")
        local = next(d for d in snapshot.devices if d.id == "speakers")
        self.assertEqual((local.volume, local.volume_muted), (0.35, True))

    def test_unknown_remote_volume_is_not_invented_and_send_requires_connection(self):
        self.assertIsNone(next(d.volume for d in self.controller.snapshot().devices if d.id == HOME.id))
        self.assertFalse(self.controller.set_volume(HOME.id, 0.5))
        self._cast()
        self.assertFalse(self.controller.set_volume(HOME.id, 0.5))
        self.sender.volume_state.update(volume=0.38, known=True, source="receiver_read")
        self.controller._step()
        home = next(d for d in self.controller.snapshot().devices if d.id == HOME.id)
        self.assertAlmostEqual(home.volume, 0.38)
        self.assertFalse(home.volume_muted)
        self.assertTrue(self.controller.set_volume(HOME.id, 0.75))
        self.controller._step()
        self.assertEqual(self.sender.volume_calls, [0.75])
        self.assertEqual(next(d.volume for d in self.controller.snapshot().devices if d.id == HOME.id), 0.38)
        self.sender.volume_state.update(volume=0.75, pending=False, source="receiver_ack")
        self.controller._step()
        self.assertEqual(next(d.volume for d in self.controller.snapshot().devices if d.id == HOME.id), 0.75)
        self.assertFalse(self.controller.set_volume(OTHER_HOME.id, 0.6))
        self.controller.stop_cast()
        self.controller._step()
        self.assertIsNone(next(d.volume for d in self.controller.snapshot().devices if d.id == HOME.id))

    def test_remote_zero_is_receiver_mute_only_and_invalid_evidence_is_unknown(self):
        self._cast()
        self.backend.mutes["speakers"] = True
        self.sender.volume_state.update(volume=0.4, known=True, source="receiver_read")
        self.controller._step()
        self.assertTrue(self.controller.set_volume(HOME.id, 0.0))
        self.controller._step()
        self.sender.volume_state.update(volume=0.0, source="receiver_ack")
        self.controller._step()
        home = next(d for d in self.controller.snapshot().devices if d.id == HOME.id)
        self.assertEqual((home.volume, home.volume_muted), (0.0, True))
        self.assertTrue(self.backend.mutes["speakers"])
        self.assertEqual(self.backend.mute_calls, [])
        for evidence in ({"volume": 0.5, "known": False, "source": "receiver_read"},
                         {"volume": 0.5, "known": True, "source": "sent"},
                         {"volume": float("nan"), "known": True, "source": "receiver_read"}):
            self.sender.volume_state = evidence
            self.controller._step()
            self.assertIsNone(next(d.volume for d in self.controller.snapshot().devices if d.id == HOME.id))

    def test_receiver_info_is_real_initial_volume_and_async_failure_does_not_stop_cast(self):
        self._cast()
        self.sender.volume_state.update(volume=0.38, known=True, source="receiver_info", operation="info")
        self.controller._step()
        self.assertEqual(next(d.volume for d in self.controller.snapshot().devices if d.id == HOME.id), 0.38)
        self.assertTrue(self.controller.set_volume(HOME.id, 0.7))
        self.controller._step()
        stops = self.sender.stops
        for code in (500, 0):
            self.sender.volume_state.update(pending=False, last_code=code, operation="set")
            self.controller._step()
            self.assertIn("音量调整失败", self.controller.snapshot().status_message)
            self.assertEqual(self.controller.snapshot().state, "casting")
            self.assertEqual(self.sender.stops, stops)
            self.assertEqual(next(d.volume for d in self.controller.snapshot().devices if d.id == HOME.id), 0.38)
        # An old failure snapshot must not overwrite the next request's status
        # while native code is still receiving it; only new evidence reports.
        self.assertTrue(self.controller.set_volume(HOME.id, 0.6))
        self.controller._step()
        self.assertIn("正在调整", self.controller.snapshot().status_message)
        self.sender.volume_state.update(volume=0.6, source="receiver_ack", pending=False, last_code=200)
        self.controller._step()
        self.assertEqual(self.controller.snapshot().status_message, "已调整 HomePod 音量")
        self.sender.volume_state.update(known=False, volume=None, source="unknown", last_code=501, operation="read")
        self.controller._step()
        self.assertIn("无法读取", self.controller.snapshot().status_message)
        self.assertEqual(self.controller.snapshot().state, "casting")
        self.assertEqual(self.sender.stops, stops)
        self.assertIsNone(next(d.volume for d in self.controller.snapshot().devices if d.id == HOME.id))

    def test_rapid_volume_requests_coalesce_per_device_and_errors_do_not_stop_cast(self):
        self._cast()
        for value in (0.2, 0.5, 0.2):
            self.assertTrue(self.controller.set_volume("headphones", value))
        self.assertTrue(self.controller.set_volume("speakers", 0.8))
        self.controller._step()
        self.assertEqual(self.backend.volume_calls, [("headphones", 0.2), ("speakers", 0.8)])
        self.backend.volume_error = "volume access denied"
        stops = self.sender.stops
        self.assertTrue(self.controller.set_volume("headphones", 0.9))
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "casting")
        self.assertIn("volume access denied", self.controller.snapshot().status_message)
        self.assertEqual(self.sender.stops, stops)
        self.assertEqual(self.backend.mute_calls, [])

    def test_invalid_volume_stale_session_and_disconnection_do_not_mutate_outputs(self):
        for value in (None, True, "0.4", -0.1, 1.1, float("nan"), 10 ** 400):
            self.assertFalse(self.controller.set_volume("speakers", value))
        self.assertFalse(self.controller.set_volume("unknown", 0.5))
        self.controller.set_volume("headphones", 0.5)
        self.backend.outputs = (self.backend.outputs[0],)
        self.controller._refresh_devices()
        self.controller._step()
        self.assertEqual(self.backend.volume_calls, [])
        self._cast()
        self.sender.volume_state.update(volume=0.4, known=True, source="receiver_read")
        self.controller._step()
        self.controller.select(OTHER_HOME.id)
        self.assertTrue(self.controller.set_volume(HOME.id, 0.8))
        self.controller._step()
        self.assertEqual(self.sender.volume_calls, [])
        self.assertEqual(self.backend.volume_calls, [])

    def test_close_is_idempotent_and_public_operations_are_rejected(self):
        self._cast()
        self.controller.close()
        self.controller.close()
        self.assertTrue(self.backend.closed)
        self.assertTrue(self.discovery.closed)
        self.assertFalse(self.sender.running)
        self.assertFalse(self.controller.refresh())
        self.assertFalse(self.controller.select(HOME.id))
        self.assertFalse(self.controller.stop_cast())
        self.assertFalse(self.controller.set_volume("speakers", 0.5))

    def test_endpoint_enumeration_error_during_cast_stops_sender(self):
        self._cast()
        self.backend.error = "audio service unavailable"
        self.assertFalse(self.controller._refresh_devices())
        snapshot = self.controller.snapshot()
        self.assertFalse(self.sender.running)
        self.assertEqual(snapshot.active_id, "")
        self.assertEqual(snapshot.pending_id, "")
        self.assertEqual(snapshot.cast_source_id, "")
        self.assertEqual(snapshot.cast_latency_seconds, 0.0)
        self.assertEqual(snapshot.state, "error")
        self.assertIn(self.backend.error, snapshot.status_message)
        self.backend.error = ""
        self.assertTrue(self.controller._refresh_devices())
        self.assertEqual(self.controller.snapshot().active_id, "speakers")
        self.assertEqual(self.controller.snapshot().state, "ready")
        self.assertEqual(len(self.sender.starts), 1)

    def test_enumeration_recovery_does_not_leave_stale_error_forever(self):
        self.backend.error = "audio service unavailable"
        self.controller._refresh_devices()
        self.backend.error = ""
        self.controller._refresh_devices()
        self.assertEqual(self.controller.snapshot().state, "ready")

    def test_external_default_change_cannot_leave_cast_reading_an_idle_old_endpoint(self):
        self._cast()
        self.backend.set_default("headphones")
        self.controller._refresh_devices()
        self.assertFalse(self.sender.running)
        self.assertEqual(self.controller.snapshot().cast_latency_seconds, 0.0)

    def test_failed_post_selection_refresh_does_not_announce_success(self):
        original = self.backend.set_default

        def fail_readback(identity):
            original(identity)
            self.backend.error = "device lost during readback"

        self.backend.set_default = fail_readback
        self.controller.select("headphones")
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertEqual(self.controller.snapshot().event_serial, 0)

    def test_queued_cast_cannot_restart_sender_after_controller_is_closed(self):
        self.controller.select(HOME.id)
        self.controller.close()
        self.controller._step()
        self.assertEqual(self.sender.starts, [])

    def test_cleanup_failure_still_closes_discovery_and_backend(self):
        self.sender.stop_error = "cleanup failed"
        try:
            self.controller.close()
        except OSError:
            pass
        self.assertTrue(self.backend.closed)
        self.assertTrue(self.discovery.closed)


class AudioRouteGroupControllerTests(unittest.TestCase):
    def setUp(self):
        self.backend, self.discovery = _Backend(), _Discovery()
        self.single, self.group = _Sender(), _GroupSender()
        session = AudioSessionEvidence("speakers", 310, "session", "instance")
        self.source = MusicAudioSource("cloudmusic.exe", 300, 1000, r"C:\Music\cloudmusic.exe",
                                       (310,), ("session",), (session,), 100.0)
        self.resolve_calls = []
        self.router = _AppRouter(lambda: self.source, lambda source: setattr(self, "source", source))

        def resolver(app_id):
            self.resolve_calls.append(app_id)
            return self.source

        self.controller = AudioRouteController(self.backend, self.discovery, self.single,
            group_sender=self.group, source_resolver=resolver, app_router=self.router, start_worker=False)
        self.addCleanup(self.controller.close)
        self.controller._refresh_devices()
        self.controller.set_music_source("cloudmusic.exe")
        self.controller._step()

    def _stream(self, identity=HOME.id):
        # Protocol and ownership fixtures deliberately exercise a one-member
        # group primitive. Public checkbox mode boundaries are tested below.
        self.controller._begin_group((identity,))
        self.group.events = self.group.ready_events()
        self.controller._step()
        return self.controller.snapshot()

    def _source_check_due(self):
        self.controller._next_source_resolve_at = 0
        self.controller._step()

    def test_group_primitive_captures_only_selected_music_process(self):
        self.controller._begin_group((HOME.id,))
        snapshot = self.controller.snapshot()
        self.assertEqual(self.group.starts, [((HOME.id,), "speakers", 300, 1000)])
        self.assertEqual(self.single.starts, [])
        self.assertEqual(snapshot.state, "connecting")
        self.assertEqual(snapshot.member_ids, ())
        self.assertEqual(snapshot.pending_ids, (HOME.id,))
        self.assertEqual(snapshot.capture_scope, "music_process_tree")
        self.assertEqual(snapshot.music_source_app_id, "cloudmusic.exe")
        self.assertEqual(self.backend.selected, [])
        self.group.events = self.group.ready_events()
        self.controller._step()
        self.assertEqual(self.controller.snapshot().member_ids, (HOME.id,))
        self.assertEqual(self.controller.snapshot().pending_ids, ())

    def test_second_homepod_toggle_rebuilds_one_shared_helper_with_both_members(self):
        self._stream()
        self.controller.toggle(OTHER_HOME.id)
        self.controller._step()
        self.assertEqual(self.group.starts[-1][0], (HOME.id, OTHER_HOME.id))
        self.assertEqual(self.single.starts, [])
        self.assertEqual(self.controller.snapshot().pending_ids, (HOME.id, OTHER_HOME.id))
        self.group.events = self.group.ready_events()
        self.controller._step()
        self.assertEqual(self.controller.snapshot().member_ids, (HOME.id, OTHER_HOME.id))
        selected = [d for d in self.controller.snapshot().devices if d.id in self.controller.snapshot().member_ids]
        self.assertTrue(all(d.route_state == "active" for d in selected))

    def test_bluetooth_member_addition_uses_fixed_source_without_default_switch(self):
        self._stream()
        self.controller.toggle("headphones")
        self.controller._step()
        self.assertEqual(self.group.starts[-1][0], (HOME.id, "headphones"))
        self.assertEqual(self.group.starts[-1][1], "speakers")
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.volume_calls, [])

    def _add_group_spare(self, identity="spare"):
        self.backend.outputs += (AudioOutputDevice(identity, "备用电脑输出", "speaker"),)
        self.backend.mutes[identity], self.backend.volumes[identity] = False, .4
        self.router.allow = True
        self.controller._refresh_devices()

    def _stream_four_members(self):
        self._add_group_spare()
        self.controller._begin_group((HOME.id, OTHER_HOME.id, "speakers", "headphones"))
        self.group.events = self.group.ready_events()
        self.controller._step()
        return self.controller.snapshot()

    def test_windows_checkbox_addition_preserves_existing_windows_and_airplay_members(self):
        self._add_group_spare()
        self._stream()
        for identity in (OTHER_HOME.id, "speakers", "headphones"):
            self.assertTrue(self.controller.toggle(identity))
            self.controller._step()
            expected = (HOME.id, OTHER_HOME.id, "speakers", "headphones")[:
                (OTHER_HOME.id, "speakers", "headphones").index(identity) + 2]
            self.assertEqual(self.group.starts[-1][0], expected)
            self.assertEqual(self.controller.snapshot().pending_ids, expected)
            self.assertEqual(self.controller.snapshot().member_ids, ())
            self.group.events = self.group.ready_events()
            self.controller._step()
            self.assertEqual(self.controller.snapshot().member_ids, expected)
        self.assertEqual(self.controller.snapshot().capture_scope, "music_process_tree")
        self.assertEqual(self.group.starts[-1][1], "spare")
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.single.starts, [])
        self.assertEqual(self.backend.mute_calls, [])
        self.assertEqual(self.backend.volume_calls, [])

    def test_source_matching_second_windows_member_is_migrated_away_from_all_outputs(self):
        self._add_group_spare()
        self.source = replace(self.source, sessions=(replace(self.source.sessions[0], endpoint_id="headphones"),))
        self.controller._begin_group((HOME.id, OTHER_HOME.id, "speakers", "headphones"))
        self.assertEqual(self.group.starts, [((HOME.id, OTHER_HOME.id, "speakers", "headphones"),
                                              "spare", 300, 1000)])
        self.assertEqual([call for call in self.router.calls if call[0] == "prepare"],
                         [("prepare", "spare", "speakers")])
        self.assertEqual({session.endpoint_id for session in self.source.sessions}, {"spare"})
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.mute_calls, [])

    def test_distinct_music_source_needs_no_route_transaction_for_two_windows_outputs(self):
        self._add_group_spare()
        self.source = replace(self.source, sessions=(replace(self.source.sessions[0], endpoint_id="spare"),))
        self.controller._begin_group((HOME.id, OTHER_HOME.id, "speakers", "headphones"))
        self.assertEqual(self.group.starts[-1][1], "spare")
        self.assertEqual(self.router.guards, [])
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.mute_calls, [])

    def test_all_selected_windows_outputs_are_excluded_from_capture_spare_candidates(self):
        self._add_group_spare("monitor")
        self.controller._begin_group((HOME.id, OTHER_HOME.id, "speakers", "headphones", "monitor"))
        self.assertEqual(self.group.starts, [])
        self.assertEqual([call for call in self.router.calls if call[0] == "prepare"], [])
        self.assertEqual(self.router.guards, [])
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertIn("空闲电脑输出", self.controller.snapshot().status_message)
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.mute_calls, [])
        self.assertEqual(self.backend.volume_calls, [])

    def test_four_outputs_without_unused_source_never_start_or_mute_music_session(self):
        self.router.allow = True
        self.controller._begin_group((HOME.id, OTHER_HOME.id, "speakers", "headphones"))
        self.assertEqual(self.group.starts, [])
        self.assertEqual(self.single.starts, [])
        self.assertEqual(self.router.guards, [])
        self.assertEqual(self.backend.mute_calls, [])
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertEqual(self.controller.snapshot().member_ids, ())
        self.assertEqual({session.endpoint_id for session in self.source.sessions}, {"speakers"})

    def test_occupied_unused_endpoint_cannot_authorize_four_output_group(self):
        self._add_group_spare()
        self.router.occupied.add("spare")
        self.controller._begin_group((HOME.id, OTHER_HOME.id, "speakers", "headphones"))
        self.assertEqual(self.group.starts, [])
        self.assertEqual(self.router.guards, [])
        self.assertEqual(self.backend.mute_calls, [])
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.controller.snapshot().state, "error")

    def test_four_members_have_independent_windows_volume_and_named_airplay_ack(self):
        self._stream_four_members()
        self.group.volume_states = {
            HOME.id: {"volume": .3, "known": True, "source": "receiver_read"},
            OTHER_HOME.id: {"volume": .7, "known": True, "source": "receiver_info"},
        }
        self.controller._step()
        requested = (("speakers", .22), ("headphones", .41), (HOME.id, .18), (OTHER_HOME.id, .62))
        for identity, volume in requested:
            self.assertTrue(self.controller.set_volume(identity, volume))
        self.controller._step()
        self.assertEqual(self.backend.volume_calls, [("speakers", .22), ("headphones", .41)])
        self.assertEqual(self.group.volume_calls, [(HOME.id, .18), (OTHER_HOME.id, .62)])
        volumes = {device.id: device.volume for device in self.controller.snapshot().devices}
        self.assertEqual((volumes[HOME.id], volumes[OTHER_HOME.id]), (.3, .7))
        self.assertEqual((volumes["speakers"], volumes["headphones"], volumes["spare"]), (.22, .41, .4))
        self.group.volume_states[OTHER_HOME.id].update(volume=.62, source="receiver_ack")
        self.controller._step()
        volumes = {device.id: device.volume for device in self.controller.snapshot().devices}
        self.assertEqual((volumes[HOME.id], volumes[OTHER_HOME.id]), (.3, .62))
        self.assertEqual(self.controller.snapshot().state, "casting")
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.mute_calls, [])

    def test_removing_one_windows_checkbox_preserves_the_other_and_both_airplay_members(self):
        self._stream_four_members()
        self.controller.toggle("speakers")
        self.controller._step()
        self.assertEqual(self.group.starts[-1][0], (HOME.id, OTHER_HOME.id, "headphones"))
        self.assertEqual(self.group.starts[-1][1], "speakers")
        self.assertTrue(self.router.guards[-1].restored)
        self.group.events = self.group.ready_events()
        self.controller._step()
        self.assertEqual(self.controller.snapshot().member_ids, (HOME.id, OTHER_HOME.id, "headphones"))
        self.assertEqual(self.controller.snapshot().capture_scope, "music_process_tree")
        self.assertEqual(self.backend.selected, [])

    def test_four_member_rebuild_restores_owned_source_and_route_before_restarting(self):
        self._stream_four_members()
        order = []
        guard = self.router.guards[-1]
        original_stop, original_restore = self.group.stop, guard.restore
        original_mute, original_start = self.backend.set_endpoint_mute, self.group.start_group
        self.group.stop = lambda: (order.append("helper_stop"), original_stop())[-1]
        self.backend.set_endpoint_mute = lambda identity, value: (
            order.append(("source_restore", identity, value)), original_mute(identity, value))[-1]
        guard.restore = lambda: (order.append("app_restore"), original_restore())[-1]
        self.group.start_group = lambda devices, source, endpoint: (
            order.append(("helper_start", tuple(device.id for device in devices))),
            original_start(devices, source, endpoint))[-1]
        self.backend.mutes["spare"] = True
        self.group.needs_local_output_restore = True
        self.group.source_evidence = {"endpoint_id": "spare", "original_muted": False, "phase": "muted"}
        self.controller.toggle("headphones")
        self.controller._step()
        self.assertEqual(order, ["helper_stop", ("source_restore", "spare", False), "app_restore",
                                 ("helper_start", (HOME.id, OTHER_HOME.id, "speakers"))])
        self.assertEqual(self.group.starts[-1][1], "spare")
        self.assertEqual(self.backend.volume_calls, [])
        self.assertEqual(self.backend.selected, [])

    def test_disconnection_of_either_windows_member_stops_and_restores_four_output_group(self):
        original_outputs = self.backend.outputs
        for disconnected in ("speakers", "headphones"):
            with self.subTest(disconnected=disconnected):
                self.backend.outputs = original_outputs
                self._stream_four_members()
                guard = self.router.guards[-1]
                self.backend.mutes["spare"] = True
                self.group.needs_local_output_restore = True
                self.group.source_evidence = {"endpoint_id": "spare", "original_muted": False, "phase": "muted"}
                self.backend.outputs = tuple(device for device in self.backend.outputs if device.id != disconnected)
                self.controller._refresh_devices()
                self.assertFalse(self.group.running)
                self.assertTrue(guard.restored)
                self.assertEqual(self.controller.snapshot().member_ids, ())
                self.assertEqual(self.controller.snapshot().state, "error")
                self.assertIn("设备已断开", self.controller.snapshot().status_message)
                self.assertFalse(self.backend.mutes["spare"])
                self.assertEqual({session.endpoint_id for session in self.source.sessions}, {"speakers"})
                self.assertEqual(self.backend.volume_calls, [])
                self.assertEqual(self.backend.selected, [])

    def test_eight_output_limit_counts_each_windows_member_without_replacing_it(self):
        self._add_group_spare()
        self.discovery.receivers = tuple(replace(HOME, id=f"receiver-{index}", host=f"192.0.2.{index + 1}")
                                         for index in range(7))
        self.controller._refresh_devices()
        desired = tuple(receiver.id for receiver in self.discovery.receivers[:6]) + ("speakers", "headphones")
        for identity in desired:
            self.controller.toggle(identity)
            self.controller._step()
            sender = self.single if len(self.controller._group_member_ids) < 2 else self.group
            sender.events = self.group.ready_events() if sender is self.group else [{"event": "streaming"}]
            self.controller._step()
        self.assertEqual(self.controller.snapshot().member_ids, desired)
        self.assertEqual(self.group.starts[-1][0], desired)
        starts = tuple(self.group.starts)
        self.controller.toggle(self.discovery.receivers[6].id)
        self.controller._step()
        self.assertEqual(tuple(self.group.starts), starts)
        self.assertEqual(self.controller.snapshot().member_ids, desired)
        self.assertTrue(self.group.running)
        self.assertIn("最多支持 8", self.controller.snapshot().status_message)
        self.assertEqual(self.backend.selected, [])

    def test_same_source_local_render_without_safe_physical_spare_fails_closed(self):
        self._stream()
        starts = len(self.group.starts)
        self.controller.toggle("speakers")
        self.controller._step()
        self.assertEqual(len(self.group.starts), starts)
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertIn("空闲电脑输出", self.controller.snapshot().status_message)
        self.assertEqual(self.controller.snapshot().member_ids, ())

    def test_windows_only_second_checkbox_requires_safe_capture_spare(self):
        self.controller.toggle("headphones")
        self.controller._step()
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.group.starts, [])
        self.assertEqual(self.controller.snapshot().active_id, "speakers")
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertIn("空闲电脑输出", self.controller.snapshot().status_message)

    def test_remove_last_homepod_returns_remaining_bluetooth_to_local_selection(self):
        self._stream()
        self.controller.toggle("headphones")
        self.controller._step()
        self.group.events = self.group.ready_events()
        self.controller._step()
        self.controller.toggle(HOME.id)
        self.controller._step()
        self.assertFalse(self.group.running)
        self.assertEqual(self.backend.selected, ["headphones"])
        self.assertEqual(self.controller.snapshot().member_ids, ())
        self.assertEqual(self.controller.snapshot().capture_scope, "")

    def test_waiting_music_source_preserves_desired_members_and_limits_retry_frequency(self):
        self.source = None
        with patch("api.audio_route_controller.time.monotonic", return_value=100):
            self.controller._begin_group((HOME.id,))
            self.controller._step()
            self.controller._refresh_devices()
            self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot.state, "waiting_source")
        self.assertEqual(snapshot.pending_ids, (HOME.id,))
        self.assertEqual(len(self.resolve_calls), 1)
        self.assertEqual(self.group.starts, [])
        with patch("api.audio_route_controller.time.monotonic", return_value=102):
            self.controller._step()
        self.assertEqual(len(self.resolve_calls), 2)

    def test_empty_or_unknown_source_does_not_use_display_name_or_all_sound(self):
        self.controller.set_music_source("")
        self.controller._step()
        self.controller._begin_group((HOME.id,))
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "waiting_source")
        self.assertEqual(self.resolve_calls, [])
        self.assertEqual(self.single.starts, [])
        self.assertEqual(self.group.starts, [])

    def test_new_exact_player_identity_rebuilds_group_but_same_player_track_does_not(self):
        self._stream()
        original = tuple(self.group.starts)
        self.controller.set_music_source("cloudmusic.exe")
        self.controller._step()
        self.assertEqual(tuple(self.group.starts), original)
        self.source = replace(self.source, source_app_id="Spotify.exe", process_id=400,
                              process_created_at_100ns=2000, process_path=r"C:\Spotify\Spotify.exe")
        self.controller.set_music_source("Spotify.exe")
        self.controller._step()
        self.assertEqual(self.group.starts[-1][0], (HOME.id,))
        self.assertEqual(self.group.starts[-1][2:], (400, 2000))
        self.assertEqual(self.controller.snapshot().music_source_app_id, "Spotify.exe")

    def test_source_pinned_to_nondefault_endpoint_does_not_immediately_stop_group(self):
        self.source = replace(self.source, sessions=(replace(self.source.sessions[0], endpoint_id="headphones"),))
        self._stream()
        self.controller._refresh_devices()
        self.assertEqual(self.controller.snapshot().state, "casting")
        self.assertTrue(self.group.running)
        self.assertEqual(self.controller.snapshot().cast_source_id, "headphones")
        self.assertEqual(self.backend.selected, [])

    def test_positive_source_endpoint_migration_stops_group_and_does_not_recast(self):
        self._stream()
        self.source = replace(self.source, sessions=(replace(self.source.sessions[0], endpoint_id="headphones"),))
        self._source_check_due()
        self.assertFalse(self.group.running)
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertEqual(self.controller.snapshot().member_ids, ())
        self.assertIn("输出已改变", self.controller.snapshot().status_message)
        starts = tuple(self.group.starts)
        self._source_check_due()
        self.assertEqual(tuple(self.group.starts), starts)

    def test_positive_root_pid_or_creation_change_stops_group(self):
        for changes in ({"process_id": 301}, {"process_created_at_100ns": 1001}):
            with self.subTest(changes=changes):
                self.source = replace(self.source, process_id=300, process_created_at_100ns=1000)
                self._stream()
                self.source = replace(self.source, **changes)
                self._source_check_due()
                self.assertFalse(self.group.running)
                self.assertEqual(self.controller.snapshot().state, "error")

    def test_pause_without_active_source_evidence_holds_existing_group(self):
        self._stream()
        self.source = None
        self._source_check_due()
        self.assertTrue(self.group.running)
        self.assertEqual(self.controller.snapshot().state, "casting")

    def test_multiple_source_endpoints_are_reported_instead_of_default_guessing(self):
        self.source = replace(self.source, sessions=self.source.sessions + (
            replace(self.source.sessions[0], endpoint_id="headphones", session_instance_id="second"),))
        self.controller._begin_group((HOME.id,))
        self.controller._step()
        self.assertEqual(self.group.starts, [])
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertIn("多个输出", self.controller.snapshot().status_message)

    def test_remote_member_volume_uses_named_group_and_waits_for_real_ack(self):
        self._stream()
        self.controller.toggle(OTHER_HOME.id)
        self.controller._step()
        self.group.events = self.group.ready_events()
        self.group.volume_states = {
            HOME.id: {"volume": .3, "known": True, "source": "receiver_read"},
            OTHER_HOME.id: {"volume": .7, "known": True, "source": "receiver_info"},
        }
        self.controller._step()
        self.assertTrue(self.controller.set_volume(OTHER_HOME.id, .23))
        self.controller._step()
        self.assertEqual(self.group.volume_calls, [(OTHER_HOME.id, .23)])
        self.assertEqual(self.single.volume_calls, [])
        self.assertEqual(next(d.volume for d in self.controller.snapshot().devices if d.id == OTHER_HOME.id), .7)
        self.group.volume_states[OTHER_HOME.id].update(volume=.23, source="receiver_ack")
        self.controller._step()
        self.assertEqual(next(d.volume for d in self.controller.snapshot().devices if d.id == OTHER_HOME.id), .23)
        self.assertEqual(next(d.volume for d in self.controller.snapshot().devices if d.id == HOME.id), .3)

    def test_stale_volume_generation_is_fenced_after_group_rebuild(self):
        self._stream()
        self.group.volume_states[HOME.id] = {"volume": .3, "known": True, "source": "receiver_read"}
        self.controller._step()
        old_generation = self.controller._generation
        self.controller.toggle(OTHER_HOME.id)
        self.controller._step()
        self.group.events = self.group.ready_events()
        self.group.volume_states[HOME.id] = {"volume": .3, "known": True, "source": "receiver_read"}
        self.controller._step()
        self.controller._set_volume(HOME.id, .9, old_generation)
        self.assertEqual(self.group.volume_calls, [])
        self.assertEqual(self.controller.snapshot().state, "casting")

    def test_group_error_wins_over_streaming_and_clears_retry_intent(self):
        self.controller._begin_group((HOME.id,))
        self.controller._step()
        self.group.events = [{"event": "streaming"}, {"event": "error", "message": "PTP timeline lost"}]
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertEqual(self.controller.snapshot().status_message, "PTP timeline lost")
        self.assertEqual(self.controller.snapshot().event_serial, 0)
        self.assertEqual(self.controller.snapshot().pending_ids, ())
        starts = tuple(self.group.starts)
        self._source_check_due()
        self.assertEqual(tuple(self.group.starts), starts)

    def test_group_stop_restores_only_owned_source_after_abnormal_helper_exit(self):
        self._stream()
        self.backend.mutes["speakers"] = True
        self.group.needs_local_output_restore = True
        self.group.source_evidence = {"endpoint_id": "speakers", "original_muted": False, "phase": "muted"}
        self.controller.stop_cast()
        self.controller._step()
        self.assertEqual(self.backend.mute_calls, [("speakers", False)])
        self.assertEqual(self.backend.volume_calls, [])
        self.assertEqual(self.controller.diagnostics()["schema_version"], 1)

    def test_selected_local_disappearance_stops_shared_group(self):
        self._stream()
        self.controller.toggle("headphones")
        self.controller._step()
        self.group.events = self.group.ready_events()
        self.controller._step()
        self.backend.outputs = tuple(device for device in self.backend.outputs if device.id != "headphones")
        self.controller._refresh_devices()
        self.assertFalse(self.group.running)
        self.assertIn("蓝牙设备已断开", self.controller.snapshot().status_message)

    def test_temporary_mdns_loss_keeps_live_member_volume_and_group_edit_evidence(self):
        self._stream()
        self.discovery.receivers = (OTHER_HOME,)
        self.group.volume_states[HOME.id] = {"volume": .3, "known": True, "source": "receiver_read"}
        self.controller._step()
        home = next(d for d in self.controller.snapshot().devices if d.id == HOME.id)
        self.assertFalse(home.available)
        self.assertEqual(home.volume, .3)
        self.controller.toggle(OTHER_HOME.id)
        self.controller._step()
        self.assertEqual(self.group.starts[-1][0], (HOME.id, OTHER_HOME.id))

    def test_stop_supersedes_queued_group_toggle_before_any_cast(self):
        self.controller.toggle(HOME.id)
        self.controller.toggle(OTHER_HOME.id)
        self.controller.stop_cast()
        self.controller._step()
        self.assertEqual(self.group.starts, [])
        self.assertEqual(self.controller.snapshot().state, "ready")

    def test_legacy_select_after_group_replaces_it_with_one_compatible_sender(self):
        self._stream()
        self.controller.select(HOME.id)
        self.controller._step()
        self.assertFalse(self.group.running)
        self.assertEqual(self.single.starts, [(HOME.id, "speakers")])
        self.assertEqual(self.controller.snapshot().pending_ids, ())

    def test_total_member_limit_preserves_current_group_when_extra_receiver_is_rejected(self):
        self.discovery.receivers = tuple(replace(HOME, id=f"receiver-{index}", host=f"192.0.2.{index + 1}")
                                         for index in range(9))
        self.controller._refresh_devices()
        for receiver in self.discovery.receivers[:8]:
            self.controller.toggle(receiver.id)
            self.controller._step()
            self.group.events = self.group.ready_events()
            self.controller._step()
        starts = tuple(self.group.starts)
        self.controller.toggle(self.discovery.receivers[8].id)
        self.controller._step()
        self.assertEqual(tuple(self.group.starts), starts)
        self.assertEqual(len(self.controller.snapshot().member_ids), 8)
        self.assertIn("最多", self.controller.snapshot().status_message)

    def test_two_queued_toggles_cancel_instead_of_coalescing_into_one_selection(self):
        self.controller.toggle(HOME.id)
        self.controller.toggle(HOME.id)
        self.controller._step()
        self.assertFalse(self.group.running)
        self.assertEqual(self.controller.snapshot().pending_ids, ())
        self.assertEqual(self.controller.snapshot().member_ids, ())

    def test_transport_streaming_before_positive_stats_waits_then_confirms(self):
        self.controller._begin_group((HOME.id,))
        self.controller._step()
        self.group.events = [{"event": "streaming"}]
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "waiting_audio")
        self.assertEqual(self.controller.snapshot().member_ids, ())
        self.group.events = [self.group.ready_events()[1]]
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "casting")
        self.assertEqual(self.controller.snapshot().member_ids, (HOME.id,))

    def test_positive_stats_without_transport_streaming_do_not_claim_group_playback(self):
        self.controller._begin_group((HOME.id,))
        self.controller._step()
        self.group.events = self.group.ready_events()[:2]
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "waiting_audio")
        self.assertEqual(self.controller.snapshot().member_ids, ())
        self.group.events = [{"event": "streaming"}]
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "casting")

    def test_dither_only_or_missing_meaningful_evidence_cannot_confirm_group_streaming(self):
        self.controller._begin_group((HOME.id,))
        self.controller._step()
        sample = self.group.ready_events()[1]
        sample["capture_signal"].update(meaningful_samples=0, last_meaningful_age_ms=None, peak=1 / 32768)
        self.group.events = [{"event": "waiting_audio"}, sample, {"event": "streaming"}]
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "waiting_audio")
        sample = self.group.ready_events()[1]
        del sample["capture_signal"]["meaningful_samples"]
        del sample["capture_signal"]["last_meaningful_age_ms"]
        self.group.events = [sample]
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "waiting_audio")

    def test_stale_positive_stats_cannot_confirm_group_after_transport_readiness(self):
        self.controller._begin_group((HOME.id,))
        self.controller._step()
        sample = self.group.ready_events()[1]
        sample["_received_at"] -= 10
        self.group.events = [{"event": "waiting_audio"}, sample, {"event": "streaming"}]
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "waiting_audio")
        self.group.events = [self.group.ready_events()[1]]
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "casting")

    def test_unrelated_all_sound_signal_cannot_authorize_music_group(self):
        self.controller._begin_group((HOME.id,))
        self.controller._step()
        sample = self.group.ready_events()[1]
        sample["capture_signal"]["scope"] = "all_other_processes"
        self.group.events = [{"event": "waiting_audio"}, sample, {"event": "streaming"}]
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "waiting_audio")

    def test_group_silence_uses_meaningful_sample_age_despite_nonzero_dither(self):
        self._stream()
        sample = self.group.ready_events()[1]
        sampled_at = self.controller._capture_signal_received_at + 1
        sample["_received_at"] = sampled_at
        sample["capture_signal"].update(nonzero_samples=200, last_nonzero_age_ms=0,
                                       meaningful_samples=80, last_meaningful_age_ms=3500, peak=1 / 32768)
        self.group.events = [sample]
        with patch("api.audio_route_controller.time.monotonic", return_value=sampled_at):
            self.controller._step()
        self.assertIn("暂未检测到音乐软件声音", self.controller.snapshot().status_message)
        starts = tuple(self.group.starts)
        sample = self.group.ready_events()[1]
        sample["_received_at"] = sampled_at + 1
        sample["capture_signal"].update(nonzero_samples=300, meaningful_samples=160)
        self.group.events = [sample]
        with patch("api.audio_route_controller.time.monotonic", return_value=sampled_at + 1):
            self.controller._step()
        self.assertNotIn("暂未检测到音乐软件声音", self.controller.snapshot().status_message)
        self.assertEqual(tuple(self.group.starts), starts)

    def _bluetooth_source_group(self):
        self.router.allow = True
        self.source = replace(self.source, sessions=(replace(self.source.sessions[0], endpoint_id="headphones"),))
        self._stream()
        self.controller.toggle("headphones")
        self.controller._step()

    def test_same_bluetooth_source_moves_only_music_to_verified_spare_before_native_start(self):
        self._bluetooth_source_group()
        self.assertEqual(self.group.starts[-1], ((HOME.id, "headphones"), "speakers", 300, 1000))
        self.assertEqual(self.controller.snapshot().cast_source_id, "speakers")
        self.assertEqual(self.router.calls[:5], [("recover",), ("recover",),
                                               ("prepare", "speakers", "headphones"),
                                               ("apply", "speakers"), ("wait", 5.0)])
        self.assertEqual({session.endpoint_id for session in self.source.sessions}, {"speakers"})
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.volume_calls, [])
        self.assertEqual(self.backend.mute_calls, [])

    def test_route_guard_restores_after_helper_stop_and_source_mute_restoration(self):
        self._bluetooth_source_group()
        order = []
        original_stop = self.group.stop
        original_mute = self.backend.set_endpoint_mute
        original_restore = self.router.guards[-1].restore
        self.group.stop = lambda: (order.append("helper_stop"), original_stop())[-1]
        self.backend.set_endpoint_mute = lambda identity, value: (order.append("mute_restore"), original_mute(identity, value))[-1]
        self.router.guards[-1].restore = lambda: (order.append("app_restore"), original_restore())[-1]
        self.backend.mutes["speakers"] = True
        self.group.needs_local_output_restore = True
        self.controller.stop_cast()
        self.controller._step()
        self.assertEqual(order, ["helper_stop", "mute_restore", "app_restore"])
        self.assertEqual({session.endpoint_id for session in self.source.sessions}, {"headphones"})
        self.assertIsNone(self.controller._app_route_guard)

    def test_migration_timeout_restores_guard_and_never_starts_native_for_undelayed_source(self):
        self.router.allow, self.router.wait_error = True, "music stream did not migrate"
        self.source = replace(self.source, sessions=(replace(self.source.sessions[0], endpoint_id="headphones"),))
        self._stream()
        starts = tuple(self.group.starts)
        self.controller.toggle("headphones")
        self.controller._step()
        self.assertEqual(tuple(self.group.starts), starts)
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertIn("did not migrate", self.controller.snapshot().status_message)
        self.assertTrue(self.router.guards[-1].restored)
        self.assertIsNone(self.controller._app_route_guard)

    def test_queued_stop_cancels_route_wait_then_clears_preserved_intent_without_native_start(self):
        self.router.allow = True
        self.source = replace(self.source, sessions=(replace(self.source.sessions[0], endpoint_id="headphones"),))
        self._stream()
        starts = tuple(self.group.starts)
        self.router.during_wait = self.controller.stop_cast
        self.controller.toggle("headphones")
        self.controller._step()
        self.assertEqual(tuple(self.group.starts), starts)
        self.assertTrue(self.router.guards[-1].restored)
        self.assertEqual(self.controller.snapshot().pending_ids, (HOME.id, "headphones"))
        self.controller._step()
        self.assertEqual(self.controller.snapshot().pending_ids, ())
        self.assertEqual(self.controller.snapshot().state, "ready")

    def test_cancelled_membership_migration_keeps_desired_group_for_next_edit(self):
        self.router.allow = True
        self.source = replace(self.source, sessions=(replace(self.source.sessions[0], endpoint_id="headphones"),))
        self._stream()

        def queue_edit():
            self.router.during_wait = None
            self.controller.toggle(OTHER_HOME.id)

        self.router.during_wait = queue_edit
        self.controller.toggle("headphones")
        self.controller._step()
        self.assertEqual(self.controller.snapshot().pending_ids, (HOME.id, "headphones"))
        self.controller._step()
        self.assertEqual(self.group.starts[-1][0], (HOME.id, "headphones", OTHER_HOME.id))
        self.assertEqual(self.group.starts[-1][1], "speakers")

    def test_external_application_route_change_stops_group_and_restores_owned_guard(self):
        self._bluetooth_source_group()
        self.group.events = self.group.ready_events()
        self.controller._step()
        self.router.check_error = "application chose another endpoint"
        self._source_check_due()
        self.assertFalse(self.group.running)
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertTrue(self.router.guards[-1].restored)
        self.assertEqual(self.controller.snapshot().member_ids, ())

    def test_guard_restore_failure_retains_recovery_owner_without_claiming_ready(self):
        self._bluetooth_source_group()
        self.router.restore_error = "owned application output recovery pending"
        self.controller.stop_cast()
        with self.assertRaisesRegex(RuntimeError, "recovery pending"):
            self.controller._step()
        self.assertIs(self.controller._app_route_guard, self.router.guards[-1])
        self.assertFalse(self.group.running)
        self.assertEqual(self.controller.snapshot().member_ids, ())
        self.router.restore_error = ""
        self.controller._end_cast()
        self.assertIsNone(self.controller._app_route_guard)

    def test_member_rebuild_restores_old_app_route_before_new_owned_transaction(self):
        self._bluetooth_source_group()
        self.group.events = self.group.ready_events()
        self.controller._step()
        self.controller.toggle(OTHER_HOME.id)
        self.controller._step()
        self.assertTrue(self.router.guards[0].restored)
        self.assertEqual(len(self.router.guards), 2)
        self.assertFalse(self.router.guards[-1].restored)
        self.assertEqual(self.group.starts[-1][0], (HOME.id, "headphones", OTHER_HOME.id))

    def test_shutdown_router_close_does_not_repeat_the_full_restoration_wait(self):
        self._bluetooth_source_group()
        self.controller.close()
        self.assertTrue(self.router.guards[-1].restored)
        self.assertEqual(self.router.calls[-1], ("close", 0.0))
        self.assertTrue(self.backend.closed)

    def test_group_shutdown_join_budget_covers_all_native_members_and_app_restoration(self):
        self._stream()
        thread = SimpleNamespace(join=lambda **kwargs: setattr(self, "joined_timeout", kwargs["timeout"]))
        self.controller._thread = thread
        self.controller.close()
        self.assertEqual(self.joined_timeout, 17.5)
        self.controller._thread = None
        self.controller._dispose()

    def test_music_shared_source_without_unused_spare_does_not_mute_other_application(self):
        self.router.occupied.add("speakers")
        self.controller._begin_group((HOME.id,))
        self.controller._step()
        self.assertEqual(self.group.starts, [])
        self.assertEqual(self.backend.mute_calls, [])
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertIn("空闲电脑输出", self.controller.snapshot().status_message)

    def test_music_shared_source_uses_unused_spare_without_global_output_switch(self):
        self.backend.outputs += (AudioOutputDevice("spare", "备用电脑扬声器", "speaker"),)
        self.backend.mutes["spare"], self.backend.volumes["spare"] = False, .4
        self.router.occupied.add("speakers")
        self.router.allow = True
        self.controller._refresh_devices()
        self.controller._begin_group((HOME.id,))
        self.controller._step()
        self.assertEqual(self.group.starts, [((HOME.id,), "spare", 300, 1000)])
        self.assertIn(("prepare", "spare", "speakers"), self.router.calls)
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.mute_calls, [])

    def test_another_application_on_existing_capture_output_stops_and_restores_mute(self):
        self._stream()
        self.backend.mutes["speakers"] = True
        self.group.needs_local_output_restore = True
        self.router.occupied.add("speakers")
        self._source_check_due()
        self.assertFalse(self.group.running)
        self.assertEqual(self.backend.mute_calls, [("speakers", False)])
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertIn("另一应用占用", self.controller.snapshot().status_message)

    def test_current_capture_child_identity_is_forwarded_to_occupancy_and_spare_guard(self):
        self._bluetooth_source_group()
        self.group.events = self.group.ready_events()
        self.controller._step()
        helper = ProcessIdentity(450, 2000, r"C:\GluSpot\GluSpotAirPlay.exe")
        self.group.owned_process_identity = lambda: helper
        checked = []
        guard = self.router.guards[-1]
        guard.check = lambda **ownership: checked.append(("guard", ownership))
        self.router.endpoint_is_unoccupied = lambda endpoint, source, **ownership: (
            checked.append(("endpoint", ownership)) or ownership.get("owned_process") == helper)
        self._source_check_due()
        self.assertEqual(checked, [("guard", {"owned_process": helper}), ("endpoint", {"owned_process": helper})])
        self.assertTrue(self.group.running)
        self.assertEqual(self.controller.snapshot().state, "casting")
        self.group.owned_process_identity = lambda: None
        self._source_check_due()
        self.assertFalse(self.group.running)
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertIn("另一应用占用", self.controller.snapshot().status_message)

    def test_unknown_spare_ownership_fails_closed_before_any_native_start(self):
        with patch.object(self.router, "endpoint_is_unoccupied", side_effect=OSError("ownership unavailable")):
            self.controller._begin_group((HOME.id,))
            self.controller._step()
        self.assertEqual(self.group.starts, [])
        self.assertEqual(self.backend.mute_calls, [])
        self.assertEqual(self.controller.snapshot().state, "error")

    def test_paused_stop_reports_policy_restoration_without_claiming_live_output(self):
        self._bluetooth_source_group()
        guard = self.router.guards[-1]
        original_restore = guard.restore

        def deferred_restore():
            original_restore()
            self.source = None
            guard.restoration_state = "policy_restored_no_active_stream"
            self.router.pending = {"stage": "resume_verification_pending"}

        guard.restore = deferred_restore
        self.controller.stop_cast()
        self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot.state, "ready")
        self.assertIn("原输出设置已恢复，等待音乐继续播放", snapshot.status_message)
        self.assertEqual(snapshot.member_ids, ())
        self.assertIsNone(self.controller._app_route_guard)
        self.assertTrue(self.controller._resume_recovery_pending)
        recover_before = self.router.calls.count(("recover",))
        self.controller._step()
        self.assertEqual(self.router.calls.count(("recover",)), recover_before)

        def resume_observed():
            self.router.calls.append(("recover",))
            self.router.pending = None
            return True

        self.router.recover_pending = resume_observed
        self.controller._next_route_recovery_at = 0
        self.controller._step()
        self.assertFalse(self.controller._resume_recovery_pending)
        self.assertEqual(self.controller.snapshot().status_message, "音乐软件输出恢复检查已结束")
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.volume_calls, [])

    def test_deferred_resume_record_does_not_block_waiting_source_or_run_idle_recovery(self):
        self.router.pending = {"stage": "resume_verification_pending"}
        self.source = None
        self.controller._begin_group((HOME.id,))
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "waiting_source")
        self.assertEqual(self.controller.snapshot().pending_ids, (HOME.id,))
        self.assertEqual(self.controller.snapshot().status_message, self.controller.APP_ROUTE_RESUME_MESSAGE)
        recover_before = self.router.calls.count(("recover",))
        self.controller._next_route_recovery_at = 0
        self.controller._step()
        self.assertEqual(self.router.calls.count(("recover",)), recover_before)

    def test_idle_resume_observation_never_recovers_an_active_group_owned_route(self):
        self._stream()
        self.router.pending = {"stage": "resume_verification_pending"}
        self.controller._resume_recovery_pending = True
        self.controller._next_route_recovery_at = 0
        recover_before = self.router.calls.count(("recover",))
        self.controller._step()
        self.assertEqual(self.router.calls.count(("recover",)), recover_before)
        self.assertTrue(self.group.running)
        self.assertEqual(self.controller.snapshot().state, "casting")

    def test_queued_stop_after_failed_recovery_does_not_claim_original_policy_restored(self):
        self.router.pending = {"stage": "effective_recovery_required"}

        def failed_recovery():
            self.controller.stop_cast()
            raise RuntimeError("saved output still owned")

        self.router.recover_pending = failed_recovery
        self.controller._begin_group((HOME.id,))
        self.controller._step()
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertIn("原输出仍需恢复", self.controller.snapshot().status_message)
        self.assertEqual(self.group.starts, [])
        self.assertEqual(self.backend.mute_calls, [])

    def test_resume_observation_does_not_clear_unrelated_receiver_error(self):
        self.controller._resume_recovery_pending = True
        self.router.pending = None
        self.controller._publish(state="error", status_message="HomePod 网络已断开")
        self.controller._next_route_recovery_at = 0
        self.controller._step()
        self.assertFalse(self.controller._resume_recovery_pending)
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertEqual(self.controller.snapshot().status_message, "HomePod 网络已断开")

    def test_confirmed_explicit_local_output_retires_only_pure_paused_observation(self):
        self.router.pending = {"stage": "resume_verification_pending"}
        self.controller._resume_recovery_pending = True
        self.controller.select("headphones")
        self.controller._step()
        self.assertEqual(self.backend.selected, ["headphones"])
        self.assertIn(("retire",), self.router.calls)
        self.assertIsNone(self.router.pending)
        self.assertFalse(self.controller._resume_recovery_pending)
        self.assertEqual(self.controller.snapshot().state, "ready")
        self.assertEqual(self.controller.snapshot().active_id, "headphones")

    def test_unconfirmed_explicit_local_output_keeps_paused_observation(self):
        self.router.pending = {"stage": "resume_verification_pending"}
        self.controller._resume_recovery_pending = True
        self.backend.set_default = self.backend.selected.append
        self.controller.select("headphones")
        self.controller._step()
        self.assertNotIn(("retire",), self.router.calls)
        self.assertEqual(self.router.pending["stage"], "resume_verification_pending")
        self.assertEqual(self.controller.snapshot().state, "error")

    def test_explicit_local_selection_cannot_discard_unreturned_application_route_writes(self):
        self.router.pending = {"stage": "effective_recovery_required"}
        self.controller.select("headphones")
        self.controller._step()
        self.assertEqual(self.backend.selected, ["headphones"])
        self.assertIn(("retire",), self.router.calls)
        self.assertEqual(self.router.pending["stage"], "effective_recovery_required")
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertIn("require recovery", self.controller.snapshot().status_message)


class AudioRouteModeBoundaryTests(unittest.TestCase):
    """Exercise public checkbox sequences, including capture and restore order."""

    setUp = AudioRouteGroupControllerTests.setUp

    def _toggle(self, identity):
        self.assertTrue(self.controller.toggle(identity))
        self.controller._step()
        self.assertFalse(self.single.running and self.group.running)
        return self.controller.snapshot()

    def _confirm_single(self):
        self.single.events = [{"event": "streaming"}]
        self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot.state, "casting")
        self.assertEqual(snapshot.capture_scope, "all_system")
        self.assertTrue(self.single.running)
        self.assertFalse(self.group.running)
        return snapshot

    def _confirm_group(self):
        self.group.events = self.group.ready_events()
        self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot.state, "casting")
        self.assertEqual(snapshot.capture_scope, "music_process_tree")
        self.assertFalse(self.single.running)
        self.assertTrue(self.group.running)
        return snapshot

    def test_checkbox_sequence_changes_one_two_one_zero_capture_scope(self):
        scopes = []
        snapshot = self._toggle(HOME.id)
        scopes.append(snapshot.capture_scope)
        self.assertEqual(self.single.starts, [(HOME.id, "speakers")])
        self.assertEqual(self.group.starts, [])
        self.assertEqual(snapshot.pending_id, HOME.id)
        self.assertEqual(snapshot.active_id, "")
        self.assertEqual(snapshot.pending_ids, ())
        self.assertEqual(snapshot.member_ids, ())
        self.assertEqual(self.resolve_calls, [])
        snapshot = self._confirm_single()
        self.assertEqual(snapshot.active_id, HOME.id)

        snapshot = self._toggle(OTHER_HOME.id)
        scopes.append(snapshot.capture_scope)
        self.assertEqual(self.group.starts, [((HOME.id, OTHER_HOME.id), "speakers", 300, 1000)])
        self.assertEqual(snapshot.pending_ids, (HOME.id, OTHER_HOME.id))
        self.assertEqual(snapshot.member_ids, ())
        snapshot = self._confirm_group()
        self.assertEqual(snapshot.member_ids, (HOME.id, OTHER_HOME.id))
        self.assertEqual(snapshot.pending_ids, ())

        snapshot = self._toggle(OTHER_HOME.id)
        scopes.append(snapshot.capture_scope)
        self.assertEqual(self.single.starts, [(HOME.id, "speakers"), (HOME.id, "speakers")])
        self.assertEqual(len(self.group.starts), 1)
        self.assertEqual(snapshot.pending_id, HOME.id)
        self.assertEqual(snapshot.member_ids, ())
        self.assertEqual(snapshot.pending_ids, ())
        self.assertEqual(snapshot.group_id, "")
        self._confirm_single()

        snapshot = self._toggle(HOME.id)
        scopes.append(snapshot.capture_scope)
        self.assertEqual(scopes, ["all_system", "music_process_tree", "all_system", ""])
        self.assertFalse(self.single.running)
        self.assertFalse(self.group.running)
        self.assertEqual(snapshot.state, "ready")
        self.assertEqual(snapshot.active_id, "speakers")
        self.assertEqual(snapshot.cast_source_id, "")
        self.assertEqual(snapshot.pending_id, "")
        self.assertEqual(snapshot.cast_latency_seconds, 0)
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.volume_calls, [])

    def test_one_receiver_can_cast_without_active_music_but_two_wait_for_music(self):
        self.source = None
        snapshot = self._toggle(HOME.id)
        self.assertEqual(snapshot.state, "connecting")
        self.assertEqual(snapshot.capture_scope, "all_system")
        self.assertEqual(self.single.starts, [(HOME.id, "speakers")])
        self.assertEqual(self.resolve_calls, [])
        self._confirm_single()

        snapshot = self._toggle(OTHER_HOME.id)
        self.assertEqual(snapshot.state, "waiting_source")
        self.assertEqual(snapshot.capture_scope, "music_process_tree")
        self.assertEqual(snapshot.pending_ids, (HOME.id, OTHER_HOME.id))
        self.assertEqual(snapshot.member_ids, ())
        self.assertEqual(self.resolve_calls, ["cloudmusic.exe"])
        self.assertEqual(self.group.starts, [])
        self.assertFalse(self.single.running)
        self.assertFalse(self.group.running)
        self.assertEqual(len(self.single.starts), 1)

    def test_single_sender_waits_for_unreturned_route_writes_and_can_retry_after_recovery(self):
        self.router.pending = {"stage": "effective_recovery_required"}
        snapshot = self._toggle(HOME.id)
        self.assertEqual(snapshot.state, "error")
        self.assertIn("上次输出恢复仍未完成", snapshot.status_message)
        self.assertEqual(self.single.starts, [])
        self.assertEqual(self.group.starts, [])
        self.assertIn(("recover",), self.router.calls)
        self.assertEqual(self.router.pending, {"stage": "effective_recovery_required"})
        self.assertEqual(snapshot.pending_id, "")
        self.assertEqual(snapshot.capture_scope, "")
        self.assertEqual(self.backend.mute_calls, [])
        self.assertEqual(self.backend.selected, [])

        self.router.pending = None
        snapshot = self._toggle(HOME.id)
        self.assertEqual(snapshot.state, "connecting")
        self.assertEqual(snapshot.capture_scope, "all_system")
        self.assertEqual(self.single.starts, [(HOME.id, "speakers")])
        self._confirm_single()

    def test_waiting_group_shrinks_to_all_sound_receiver_without_music_source(self):
        self.source = None
        self._toggle(HOME.id)
        self._toggle(OTHER_HOME.id)
        snapshot = self._toggle(OTHER_HOME.id)
        self.assertEqual(snapshot.state, "connecting")
        self.assertEqual(snapshot.capture_scope, "all_system")
        self.assertEqual(snapshot.pending_id, HOME.id)
        self.assertEqual(snapshot.pending_ids, ())
        self.assertEqual(snapshot.member_ids, ())
        self.assertEqual(snapshot.group_id, "")
        self.assertEqual(self.single.starts, [(HOME.id, "speakers"), (HOME.id, "speakers")])
        self.assertEqual(self.group.starts, [])
        self._confirm_single()

    def test_two_device_music_silence_and_resume_preserve_sync_group_status(self):
        self._toggle(HOME.id)
        self._confirm_single()
        self._toggle(OTHER_HOME.id)
        before = self._confirm_group()
        self.assertEqual(before.status_message, "正在同步播放到 2 个设备")
        self.assertEqual(before.member_ids, (HOME.id, OTHER_HOME.id))
        starts = (tuple(self.single.starts), tuple(self.group.starts))
        stops = (self.single.stops, self.group.stops)
        source = self.source
        sampled_at = self.controller._capture_signal_received_at

        silent = self.group.ready_events()[1]
        silent["_received_at"] = sampled_at + 3.5
        silent["capture_signal"].update(nonzero_samples=100, last_nonzero_age_ms=3500,
            meaningful_samples=80, last_meaningful_age_ms=3500, peak=0.0)
        self.group.events = [silent]
        with patch("api.audio_route_controller.time.monotonic", return_value=sampled_at + 3.5):
            self.controller._step()
        quiet = self.controller.snapshot()
        self.assertEqual(quiet.status_message, "已连接播放设备，暂未检测到音乐软件声音")

        resumed = self.group.ready_events()[1]
        resumed["_received_at"] = sampled_at + 4.0
        resumed["capture_signal"].update(nonzero_samples=200, last_nonzero_age_ms=0,
            meaningful_samples=160, last_meaningful_age_ms=0, peak=.3)
        self.group.events = [resumed]
        with patch("api.audio_route_controller.time.monotonic", return_value=sampled_at + 4.0):
            self.controller._step()
        recovered = self.controller.snapshot()
        self.assertEqual(recovered.status_message, "正在同步播放到 2 个设备")
        for snapshot in (quiet, recovered):
            with self.subTest(status=snapshot.status_message):
                self.assertEqual(snapshot.state, "casting")
                self.assertEqual(snapshot.capture_scope, "music_process_tree")
                self.assertEqual(snapshot.music_source_app_id, "cloudmusic.exe")
                self.assertEqual(snapshot.member_ids, before.member_ids)
                self.assertEqual(snapshot.group_id, before.group_id)
                self.assertEqual(snapshot.cast_source_id, before.cast_source_id)
                self.assertEqual(snapshot.pending_ids, ())
        self.assertEqual(self.source, source)
        self.assertEqual((tuple(self.single.starts), tuple(self.group.starts)), starts)
        self.assertEqual((self.single.stops, self.group.stops), stops)
        self.assertTrue(self.group.running)
        self.assertFalse(self.single.running)
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.mute_calls, [])
        self.assertEqual(self.backend.volume_calls, [])

    def test_current_local_default_does_not_implicitly_join_first_receiver(self):
        # Explicit single-device selection remains available; a second checked
        # Windows output now intentionally creates a music-only local group.
        self.controller.select("headphones")
        self.controller._step()
        self.assertEqual(self.backend.selected, ["headphones"])
        snapshot = self._toggle(HOME.id)
        self.assertEqual(snapshot.capture_scope, "all_system")
        self.assertEqual(snapshot.active_id, "")
        self.assertEqual(snapshot.member_ids, ())
        self.assertEqual(snapshot.pending_ids, ())
        self.assertEqual(self.single.starts, [(HOME.id, "headphones")])
        self.assertEqual(self.group.starts, [])
        snapshot = self._confirm_single()
        self.assertEqual(snapshot.active_id, HOME.id)
        self.assertEqual(self.backend.selected, ["headphones"])

    def test_same_receiver_checkbox_cancels_connecting_and_casting_without_second_sender(self):
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed):
                self._toggle(HOME.id)
                if confirmed:
                    self._confirm_single()
                starts = tuple(self.single.starts)
                snapshot = self._toggle(HOME.id)
                self.assertEqual(tuple(self.single.starts), starts)
                self.assertEqual(self.group.starts, [])
                self.assertFalse(self.single.running)
                self.assertFalse(self.group.running)
                self.assertEqual(snapshot.state, "ready")
                self.assertEqual(snapshot.active_id, "speakers")
                self.assertEqual(snapshot.pending_id, "")
                self.assertEqual(snapshot.pending_ids, ())
                self.assertEqual(snapshot.member_ids, ())
                self.assertEqual(snapshot.capture_scope, "")
                self.assertEqual(snapshot.cast_latency_seconds, 0)

    def test_two_queued_same_receiver_checkboxes_cancel_the_single_sender(self):
        self.assertTrue(self.controller.toggle(HOME.id))
        self.assertTrue(self.controller.toggle(HOME.id))
        self.controller._step()
        self.assertEqual(self.single.starts, [(HOME.id, "speakers")])
        self.assertEqual(self.group.starts, [])
        self.assertFalse(self.single.running)
        self.assertFalse(self.group.running)
        self.assertEqual(self.controller.snapshot().state, "ready")
        self.assertEqual(self.controller.snapshot().pending_id, "")
        self.assertEqual(self.controller.snapshot().capture_scope, "")

    def test_group_to_single_restores_owned_mute_and_music_route_before_sender_start(self):
        self.router.allow = True
        self.source = replace(self.source, sessions=(replace(self.source.sessions[0], endpoint_id="headphones"),))
        self._toggle(HOME.id)
        self._confirm_single()
        self._toggle("headphones")
        self._confirm_group()
        self.assertEqual(self.group.starts, [((HOME.id, "headphones"), "speakers", 300, 1000)])
        self.assertEqual({session.endpoint_id for session in self.source.sessions}, {"speakers"})
        self.backend.mutes["speakers"] = True
        self.group.needs_local_output_restore = True
        order = []
        original_stop = self.group.stop
        original_mute = self.backend.set_endpoint_mute
        original_restore = self.router.guards[-1].restore
        original_start = self.single.start
        self.group.stop = lambda: (order.append("group_stop"), original_stop())[-1]
        self.backend.set_endpoint_mute = lambda identity, value: (order.append("mute_restore"), original_mute(identity, value))[-1]
        self.router.guards[-1].restore = lambda: (order.append("app_restore"), original_restore())[-1]
        self.single.start = lambda device, source: (order.append("single_start"), original_start(device, source))[-1]

        snapshot = self._toggle("headphones")
        self.assertEqual(order, ["group_stop", "mute_restore", "app_restore", "single_start"])
        self.assertEqual({session.endpoint_id for session in self.source.sessions}, {"headphones"})
        self.assertTrue(self.router.guards[-1].restored)
        self.assertIsNone(self.controller._app_route_guard)
        self.assertEqual(self.backend.mute_calls, [("speakers", False)])
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.volume_calls, [])
        self.assertEqual(snapshot.capture_scope, "all_system")
        self.assertEqual(snapshot.pending_id, HOME.id)
        self.assertEqual(snapshot.pending_ids, ())
        self.assertEqual(snapshot.member_ids, ())
        self.assertEqual(snapshot.group_id, "")
        self.assertEqual(len(self.single.starts), 2)
        self.assertEqual(len(self.group.starts), 1)

    def test_removing_remote_from_public_bluetooth_group_restores_normal_local_output(self):
        self._toggle(HOME.id)
        self._confirm_single()
        self._toggle("headphones")
        self._confirm_group()
        snapshot = self._toggle(HOME.id)
        self.assertFalse(self.group.running)
        self.assertFalse(self.single.running)
        self.assertEqual(self.backend.selected, ["headphones"])
        self.assertEqual(snapshot.state, "ready")
        self.assertEqual(snapshot.active_id, "headphones")
        self.assertEqual(snapshot.capture_scope, "")
        self.assertEqual(snapshot.pending_id, "")
        self.assertEqual(snapshot.pending_ids, ())
        self.assertEqual(snapshot.member_ids, ())
        self.assertEqual(snapshot.group_id, "")


class AudioRouteDurableRecoveryTests(unittest.TestCase):
    """Exercise the real route transaction with a memory-only Windows ABI seam."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.journal = Path(self.directory.name) / "app-route-recovery.json"
        self.backend, self.discovery = _Backend(), _Discovery()
        self.backend.outputs += (AudioOutputDevice("spare", "备用扬声器", "speaker"),)
        self.backend.mutes["spare"], self.backend.volumes["spare"] = False, .4
        session = AudioSessionEvidence("speakers", 30, "music-session", "music-instance")
        self.source = MusicAudioSource("cloudmusic.exe", 30, 100, r"C:\Music\cloudmusic.exe",
                                       (30,), (session.session_identifier,), (session,), 1)
        self.last_source = self.source
        self.identity = ProcessIdentity(30, 100, self.source.process_path)
        self.routes, self.writes, self.closed_handles = {(30, 0): "", (30, 1): ""}, [], []
        self.timeline = []

        def set_route(pid, role, value):
            saved = json.loads(self.journal.read_text(encoding="utf-8"))
            self.assertTrue(any(entry["intent"] and entry["role"] == role for entry in saved["roles"]))
            self.writes.append((pid, role, value))
            self.timeline.append(("route", value))
            self.routes[(pid, role)] = value
            if value.startswith(_PREFIX) and value.endswith(_RENDER_SUFFIX):
                endpoint = value[len(_PREFIX):-len(_RENDER_SUFFIX)]
                current = self.source or self.last_source
                self.last_source = replace(current, sessions=tuple(
                    replace(entry, endpoint_id=endpoint) for entry in current.sessions))
                if self.source is not None:
                    self.source = self.last_source

        self.adapter = SimpleNamespace(
            process_identity=lambda pid: self.identity if pid == 30 else None,
            hold_process=lambda identity: (identity.process_id, identity.process_created_at_100ns),
            close_handle=self.closed_handles.append,
            get_route=lambda pid, role: self.routes[(pid, role)], set_route=set_route,
            active_owners=lambda: tuple((session.endpoint_id, session.process_id, True, False)
                                       for session in self.source.sessions) if self.source is not None else (),
            session_owners=lambda: tuple((session.endpoint_id, session.process_id, True, False,
                                         1 if self.source is not None else 0)
                                        for session in (self.source or self.last_source).sessions))

    def _router(self):
        router = WindowsAppAudioRoutes(devices=self.backend, adapter=self.adapter,
            resolver=lambda _app: self.source, journal_path=self.journal)
        self.addCleanup(self._close_router, router)
        return router

    @staticmethod
    def _close_router(router):
        try:
            router.close(timeout=0)
        except RuntimeError:
            pass

    def _leave_crashed_parent_journal(self):
        previous = self._router()
        guard = previous.prepare(self.source, "spare", "headphones")
        guard.apply()
        guard.wait_migrated(timeout=0)
        self.assertEqual(self.source.sessions[0].endpoint_id, "spare")
        # Model the OS releasing a dead parent's handles/lease. No graceful
        # restore/Set runs, so its durable owned policy survives unchanged.
        guard._close_handles()
        guard._closed = True
        previous._active = None
        previous._release_journal()
        self.writes.clear()
        self.timeline.clear()

    def _controller(self, router):
        self.single = _Sender()
        original_single_start = self.single.start

        def start_single(device, endpoint):
            self.timeline.append(("single_native", endpoint))
            original_single_start(device, endpoint)

        self.single.start = start_single
        self.group = _GroupSender()
        original_start = self.group.start_group

        def start(devices, source, endpoint):
            self.timeline.append(("native", endpoint))
            original_start(devices, source, endpoint)

        self.group.start_group = start
        controller = AudioRouteController(self.backend, self.discovery, self.single,
            group_sender=self.group, source_resolver=lambda _app: self.source,
            app_router=router, start_worker=False)
        self.addCleanup(controller.close)
        controller._refresh_devices()
        controller.set_music_source("cloudmusic.exe")
        controller._step()
        return controller

    def test_crash_recovery_runs_before_distinct_source_and_local_sink_start(self):
        self._leave_crashed_parent_journal()
        controller = self._controller(self._router())
        controller.toggle(HOME.id)
        controller.toggle("headphones")
        controller._step()
        self.assertEqual(self.group.starts, [((HOME.id, "headphones"), "speakers", 30, 100)])
        self.assertEqual(self.source.sessions[0].endpoint_id, "speakers")
        self.assertEqual(self.routes, {(30, 0): "", (30, 1): ""})
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.timeline[-1], ("native", "speakers"))
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.mute_calls, [])
        controller.stop_cast()
        controller._step()
        self.assertEqual(self.source.sessions[0].endpoint_id, "speakers")

    def test_invalid_durable_recovery_blocks_even_direct_music_capture(self):
        self._leave_crashed_parent_journal()
        saved = json.loads(self.journal.read_text(encoding="utf-8"))
        saved["roles"][0]["owned_route"] = _PREFIX + "unrelated-output" + _RENDER_SUFFIX
        self.journal.write_text(json.dumps(saved), encoding="utf-8")
        before = self.journal.read_text(encoding="utf-8")
        controller = self._controller(self._router())
        controller._begin_group((HOME.id,))
        controller._step()
        self.assertEqual(controller.snapshot().state, "error")
        self.assertIn("上次输出尚未恢复", controller.snapshot().status_message)
        self.assertEqual(self.group.starts, [])
        self.assertEqual(self.writes, [])
        self.assertEqual(self.backend.mute_calls, [])
        self.assertEqual(self.journal.read_text(encoding="utf-8"), before)

    def test_single_sender_starts_only_after_crashed_music_route_is_restored(self):
        self._leave_crashed_parent_journal()
        controller = self._controller(self._router())
        self.assertTrue(controller.toggle(HOME.id))
        controller._step()
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.source.sessions[0].endpoint_id, "speakers")
        self.assertEqual(self.routes, {(30, 0): "", (30, 1): ""})
        self.assertTrue(self.writes)
        self.assertEqual(self.timeline[-1], ("single_native", "speakers"))
        self.assertTrue(any(event[0] == "route" for event in self.timeline[:-1]))
        self.assertEqual(self.single.starts, [(HOME.id, "speakers")])
        self.assertEqual(self.group.starts, [])
        self.assertEqual(controller.snapshot().state, "connecting")
        self.assertEqual(controller.snapshot().capture_scope, "all_system")
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.volume_calls, [])
        self.assertEqual(self.backend.mute_calls, [])

    def test_invalid_durable_recovery_blocks_single_until_valid_record_is_restored(self):
        self._leave_crashed_parent_journal()
        valid = self.journal.read_text(encoding="utf-8")
        saved = json.loads(valid)
        saved["roles"][0]["owned_route"] = _PREFIX + "unrelated-output" + _RENDER_SUFFIX
        invalid = json.dumps(saved)
        self.journal.write_text(invalid, encoding="utf-8")
        controller = self._controller(self._router())
        self.assertTrue(controller.toggle(HOME.id))
        controller._step()
        self.assertEqual(controller.snapshot().state, "error")
        self.assertIn("owned endpoint", controller.snapshot().status_message)
        self.assertEqual(controller.snapshot().capture_scope, "")
        self.assertEqual(self.single.starts, [])
        self.assertEqual(self.group.starts, [])
        self.assertFalse(self.single.running)
        self.assertEqual(self.writes, [])
        self.assertEqual(self.journal.read_text(encoding="utf-8"), invalid)
        self.assertEqual(self.source.sessions[0].endpoint_id, "spare")
        self.assertEqual(self.backend.mute_calls, [])
        self.assertEqual(self.backend.selected, [])

        self.journal.write_text(valid, encoding="utf-8")
        self.assertTrue(controller.toggle(HOME.id))
        controller._step()
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.source.sessions[0].endpoint_id, "speakers")
        self.assertEqual(self.single.starts, [(HOME.id, "speakers")])
        self.assertEqual(self.group.starts, [])
        self.assertEqual(self.timeline[-1], ("single_native", "speakers"))
        self.assertEqual(controller.snapshot().capture_scope, "all_system")
        self.single.events = [{"event": "streaming"}]
        controller._step()
        self.assertEqual(controller.snapshot().state, "casting")

    def test_single_start_recovery_error_preserves_owned_journal_and_can_retry(self):
        self._leave_crashed_parent_journal()
        before = self.journal.read_text(encoding="utf-8")
        router = self._router()
        controller = self._controller(router)
        with patch.object(router, "recover_pending", side_effect=OSError("recovery adapter unavailable")):
            self.assertTrue(controller.toggle(HOME.id))
            controller._step()
        self.assertEqual(controller.snapshot().state, "error")
        self.assertIn("recovery adapter unavailable", controller.snapshot().status_message)
        self.assertEqual(self.single.starts, [])
        self.assertEqual(self.group.starts, [])
        self.assertEqual(self.journal.read_text(encoding="utf-8"), before)
        self.assertEqual(self.source.sessions[0].endpoint_id, "spare")
        self.assertEqual(self.writes, [])
        self.assertEqual(self.backend.mute_calls, [])
        self.assertEqual(self.backend.selected, [])

        self.assertTrue(controller.toggle(HOME.id))
        controller._step()
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.single.starts, [(HOME.id, "speakers")])
        self.assertEqual(self.source.sessions[0].endpoint_id, "speakers")
        self.assertEqual(self.timeline[-1], ("single_native", "speakers"))
        self.assertEqual(controller.snapshot().capture_scope, "all_system")

    def test_stop_queued_during_recovery_never_starts_helper_after_restore(self):
        self._leave_crashed_parent_journal()
        router = self._router()
        controller = self._controller(router)
        original_recover = router.recover_pending

        def recover():
            result = original_recover()
            controller.stop_cast()
            return result

        router.recover_pending = recover
        controller._begin_group((HOME.id,))
        controller._step()
        self.assertEqual(self.group.starts, [])
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.source.sessions[0].endpoint_id, "speakers")
        controller._step()
        self.assertEqual(controller.snapshot().state, "ready")
        self.assertEqual(controller.snapshot().pending_ids, ())

    def _pause_and_stop_real_guard(self):
        self.source = replace(self.source, sessions=tuple(
            replace(session, endpoint_id="headphones") for session in self.source.sessions))
        self.last_source = self.source
        router = self._router()
        controller = self._controller(router)
        controller.toggle(HOME.id)
        controller.toggle("headphones")
        controller._step()
        self.assertEqual(self.group.starts[-1][0], (HOME.id, "headphones"))
        self.assertNotEqual(self.group.starts[-1][1], "headphones")
        self.group.events = self.group.ready_events()
        controller._step()
        guard = controller._app_route_guard
        self.assertIsNotNone(guard)
        self.last_source, self.source = self.source, None
        controller.stop_cast()
        controller._step()
        self.assertEqual(controller.snapshot().state, "ready")
        self.assertIn(controller.APP_ROUTE_RESUME_MESSAGE, controller.snapshot().status_message)
        self.assertEqual(guard.restoration_state, "policy_restored_no_active_stream")
        self.assertIsNone(router._active)
        self.assertIsNone(router._lease)
        self.assertEqual(guard._handles, {})
        self.assertEqual(self.routes, {(30, 0): "", (30, 1): ""})
        self.assertEqual(router.pending_recovery()["stage"], "resume_verification_pending")
        return controller, router

    def test_real_paused_guard_stop_preserves_resume_observation_then_verifies_without_new_cast(self):
        controller, router = self._pause_and_stop_real_guard()
        starts, writes = tuple(self.group.starts), tuple(self.writes)
        self.source = self.last_source
        self.assertEqual(self.source.sessions[0].endpoint_id, "headphones")
        controller._next_route_recovery_at = 0
        controller._step()
        self.assertIsNone(router.pending_recovery())
        self.assertEqual(tuple(self.writes), writes)
        self.assertEqual(tuple(self.group.starts), starts)
        self.assertEqual(controller.snapshot().status_message, "音乐软件输出恢复检查已结束")
        self.assertFalse(controller._resume_recovery_pending)

    def test_real_resume_on_temporary_endpoint_repairs_owned_music_route_without_reconnecting(self):
        controller, router = self._pause_and_stop_real_guard()
        starts = tuple(self.group.starts)
        self.source = replace(self.last_source, sessions=tuple(
            replace(session, endpoint_id="spare") for session in self.last_source.sessions))
        writes_before = len(self.writes)
        controller._next_route_recovery_at = 0
        controller._step()
        self.assertIsNone(router.pending_recovery())
        self.assertEqual(self.source.sessions[0].endpoint_id, "headphones")
        self.assertGreater(len(self.writes), writes_before)
        self.assertEqual(self.routes, {(30, 0): "", (30, 1): ""})
        self.assertEqual(tuple(self.group.starts), starts)
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.volume_calls, [])

    def test_paused_single_cast_keeps_resume_observation_and_repairs_music_on_resume(self):
        controller, router = self._pause_and_stop_real_guard()
        before = self.journal.read_text(encoding="utf-8")
        writes = tuple(self.writes)
        previous_single_starts = list(self.single.starts)
        self.assertTrue(controller.toggle(HOME.id))
        controller._step()
        self.assertEqual(self.single.starts, previous_single_starts + [(HOME.id, "speakers")])
        self.assertTrue(self.single.running)
        self.assertEqual(self.journal.read_text(encoding="utf-8"), before)
        self.assertEqual(tuple(self.writes), writes)
        self.assertIsNone(router._lease)
        self.assertTrue(controller._resume_recovery_pending)
        self.single.events = [{"event": "streaming"}]
        controller._step()
        casting = controller.snapshot()
        self.assertEqual(casting.state, "casting")
        self.assertEqual(casting.capture_scope, "all_system")
        starts = (tuple(self.single.starts), tuple(self.group.starts))
        stops = (self.single.stops, self.group.stops)

        self.source = replace(self.last_source, sessions=tuple(
            replace(session, endpoint_id="spare") for session in self.last_source.sessions))
        controller._next_route_recovery_at = 0
        controller._step()
        self.assertIsNone(router.pending_recovery())
        self.assertFalse(controller._resume_recovery_pending)
        self.assertEqual(self.source.sessions[0].endpoint_id, "headphones")
        self.assertEqual(self.routes, {(30, 0): "", (30, 1): ""})
        self.assertGreater(len(self.writes), len(writes))
        self.assertEqual((tuple(self.single.starts), tuple(self.group.starts)), starts)
        self.assertEqual((self.single.stops, self.group.stops), stops)
        self.assertTrue(self.single.running)
        self.assertFalse(self.group.running)
        recovered = controller.snapshot()
        self.assertEqual(recovered.state, "casting")
        self.assertEqual(recovered.status_message, casting.status_message)
        self.assertEqual(recovered.capture_scope, "all_system")
        self.assertEqual(recovered.active_id, HOME.id)
        self.assertEqual(recovered.cast_source_id, "speakers")
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.volume_calls, [])
        self.assertEqual(self.backend.mute_calls, [])

    def test_single_cast_resume_recovery_failure_stops_cast_and_keeps_retry_record(self):
        controller, router = self._pause_and_stop_real_guard()
        self.assertTrue(controller.toggle(HOME.id))
        controller._step()
        self.single.events = [{"event": "streaming"}]
        controller._step()
        self.source = replace(self.last_source, sessions=tuple(
            replace(session, endpoint_id="spare") for session in self.last_source.sessions))
        before = self.journal.read_text(encoding="utf-8")
        starts = (tuple(self.single.starts), tuple(self.group.starts))
        self.backend.mutes["speakers"] = True
        self.single.needs_local_output_restore = True
        controller._next_route_recovery_at = 0
        with patch.object(router, "recover_pending", side_effect=OSError("resume adapter unavailable")):
            controller._step()
        failed = controller.snapshot()
        self.assertEqual(failed.state, "error")
        self.assertIn("原输出恢复待确认", failed.status_message)
        self.assertIn("resume adapter unavailable", failed.status_message)
        self.assertEqual(failed.capture_scope, "")
        self.assertEqual(failed.pending_id, "")
        self.assertEqual(failed.active_id, "speakers")
        self.assertFalse(self.single.running)
        self.assertFalse(self.group.running)
        self.assertEqual(self.backend.mute_calls, [("speakers", False)])
        self.assertEqual(self.journal.read_text(encoding="utf-8"), before)
        self.assertTrue(controller._resume_recovery_pending)
        self.assertEqual(self.source.sessions[0].endpoint_id, "spare")

        controller._next_route_recovery_at = 0
        controller._step()
        self.assertIsNone(router.pending_recovery())
        self.assertFalse(controller._resume_recovery_pending)
        self.assertEqual(self.source.sessions[0].endpoint_id, "headphones")
        self.assertEqual((tuple(self.single.starts), tuple(self.group.starts)), starts)
        self.assertFalse(self.single.running)
        self.assertEqual(controller.snapshot().state, "ready")
        self.assertEqual(controller.snapshot().status_message, "音乐软件输出恢复检查已结束")
        self.assertEqual(self.backend.selected, [])
        self.assertEqual(self.backend.volume_calls, [])

    def test_new_group_after_paused_stop_waits_then_resolves_fresh_restored_stream(self):
        controller, router = self._pause_and_stop_real_guard()
        starts = tuple(self.group.starts)
        controller._begin_group((HOME.id,))
        controller._step()
        self.assertEqual(controller.snapshot().state, "waiting_source")
        self.assertEqual(controller.snapshot().pending_ids, (HOME.id,))
        self.assertEqual(tuple(self.group.starts), starts)
        self.source = self.last_source
        controller._next_source_resolve_at = 0
        controller._step()
        self.assertEqual(self.group.starts[-1], ((HOME.id,), "headphones", 30, 100))
        self.assertIsNone(router.pending_recovery())
        self.assertFalse(controller._resume_recovery_pending)

    def test_real_pure_pause_observation_is_retired_only_after_confirmed_new_local_choice(self):
        controller, router = self._pause_and_stop_real_guard()
        writes = tuple(self.writes)
        controller.select("spare")
        controller._step()
        self.assertIsNone(router.pending_recovery())
        self.assertEqual(tuple(self.writes), writes)
        self.assertEqual(self.backend.selected, ["spare"])
        self.assertEqual(controller.snapshot().active_id, "spare")
        self.assertEqual(controller.snapshot().state, "ready")
        self.assertFalse(controller._resume_recovery_pending)
        self.assertIsNone(router._lease)


class AirPlayDiscoveryTests(unittest.TestCase):
    @staticmethod
    def _info(service_type, label="书房 HomePod", *, port=7000, properties=None, addresses=None):
        return SimpleNamespace(name=label + "." + service_type, port=port,
            properties=properties if properties is not None else {b"deviceid": b"AA:BB:CC:DD:EE:FF", b"model": b"AudioAccessory5,1"},
            parsed_addresses=lambda: addresses if addresses is not None else ["fe80::1", "192.0.2.10"])

    def test_receiver_parses_homepod_unicode_identity_and_selects_ipv4(self):
        device = receiver_from_service(self._info(SERVICE_TYPES[0]), SERVICE_TYPES[0])
        self.assertEqual(device, HOME)

    def test_normal_ipv4_is_preferred_over_link_local_regardless_of_order(self):
        for service_type in SERVICE_TYPES:
            for addresses in (["169.254.107.134", "192.168.9.118"],
                              ["192.168.9.118", "169.254.107.134"],
                              ["169.254.107.134", "169.254.107.135", "192.168.9.118"]):
                with self.subTest(service_type=service_type, addresses=addresses):
                    info = self._info(service_type, addresses=addresses)
                    device = receiver_from_service(info, service_type)
                    self.assertEqual(device.host, "192.168.9.118")

    def test_first_normal_ipv4_keeps_service_address_order(self):
        info = self._info(SERVICE_TYPES[0],
                          addresses=["169.254.107.134", "192.168.9.118", "10.0.0.8"])
        self.assertEqual(receiver_from_service(info, SERVICE_TYPES[0]).host, "192.168.9.118")

    def test_link_local_only_receiver_is_still_offered(self):
        info = self._info(SERVICE_TYPES[0], addresses=["fe80::1", "169.254.107.134"])
        self.assertEqual(receiver_from_service(info, SERVICE_TYPES[0]).host, "169.254.107.134")

    def test_malformed_ipv4_candidates_do_not_hide_valid_address(self):
        info = self._info(SERVICE_TYPES[0],
                          addresses=["192.168.9.999", "192.168.9", "192.168.009.118",
                                     "not-an-address", "192.168.9.118"])
        self.assertEqual(receiver_from_service(info, SERVICE_TYPES[0]).host, "192.168.9.118")

    def test_loopback_and_reserved_ipv4_are_rejected(self):
        for address in ("127.0.0.1", "127.0.0.2", "240.0.0.1", "255.255.255.255"):
            with self.subTest(address=address):
                self.assertIsNone(receiver_from_service(
                    self._info(SERVICE_TYPES[0], addresses=[address]), SERVICE_TYPES[0]))

    def test_device_kind_requires_explicit_model_and_discovery_never_invents_volume(self):
        for model, expected in (("AudioAccessory5,1", "homepod-mini"), ("AudioAccessory1,1", "homepod"),
                                ("HomePod", "homepod"), ("", "airplay"), ("Speaker", "airplay")):
            info = self._info(SERVICE_TYPES[0], label="HomePod mini", properties={b"model": model.encode()})
            device = receiver_from_service(info, SERVICE_TYPES[0])
            self.assertEqual(device.kind, expected)
            self.assertIsNone(device.volume)

    def test_raop_mac_prefix_and_airplay_deviceid_merge_once_prefer_airplay_control_port(self):
        discovery = AirPlayDiscovery()
        self.addCleanup(discovery.close)
        raop = self._info(SERVICE_TYPES[1], "AABBCCDDEEFF@书房 HomePod", port=5000,
                          properties={b"am": b"AudioAccessory5,1"})
        airplay = self._info(SERVICE_TYPES[0])
        infos = {SERVICE_TYPES[1]: raop, SERVICE_TYPES[0]: airplay}
        zc = SimpleNamespace(get_service_info=lambda type_, _name, **_k: infos[type_])
        discovery.add_service(zc, SERVICE_TYPES[0], airplay.name)
        discovery.add_service(zc, SERVICE_TYPES[1], raop.name)
        self.assertEqual(discovery.devices(), (HOME,))
        discovery.remove_service(zc, SERVICE_TYPES[0], airplay.name)
        self.assertEqual(discovery.devices()[0].port, 5000)
        discovery.remove_service(zc, SERVICE_TYPES[1], raop.name)
        self.assertEqual(discovery.devices(), ())

    def test_ipv6_multicast_and_unspecified_receivers_are_not_offered_to_ipv4_sender(self):
        for addresses in (["fe80::1"], ["224.0.0.251"], ["0.0.0.0"], ["not-an-address"]):
            with self.subTest(addresses=addresses):
                self.assertIsNone(receiver_from_service(self._info(SERVICE_TYPES[0], addresses=addresses), SERVICE_TYPES[0]))

    def test_out_of_range_control_ports_are_rejected(self):
        for port in (0, 65536):
            self.assertIsNone(receiver_from_service(self._info(SERVICE_TYPES[0], port=port), SERVICE_TYPES[0]))

    def test_malformed_advertisement_does_not_break_callback_or_create_receiver(self):
        discovery = AirPlayDiscovery()
        self.addCleanup(discovery.close)
        bad = self._info(SERVICE_TYPES[0], port="garbage")
        zc = SimpleNamespace(get_service_info=lambda *_a, **_k: bad)
        discovery.update_service(zc, SERVICE_TYPES[0], bad.name)
        self.assertEqual(discovery.devices(), ())

    def test_callback_finishing_after_close_cannot_resurrect_receiver(self):
        discovery = AirPlayDiscovery()
        entered, release = threading.Event(), threading.Event()
        info = self._info(SERVICE_TYPES[0])

        def slow_info(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=2)
            return info

        thread = threading.Thread(target=discovery.update_service,
                                  args=(SimpleNamespace(get_service_info=slow_info), SERVICE_TYPES[0], info.name))
        thread.start()
        self.assertTrue(entered.wait(timeout=1))
        discovery.close()
        release.set()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(discovery.devices(), ())

    def test_missing_zeroconf_is_reported_without_faking_receivers(self):
        discovery = AirPlayDiscovery()
        self.addCleanup(discovery.close)
        with patch.dict("sys.modules", {"zeroconf": None}):
            discovery.start()
        self.assertEqual(discovery.state, "unavailable")
        self.assertEqual(discovery.devices(), ())
        self.assertTrue(discovery.error)

    def test_multi_service_browser_uses_the_supported_zeroconf_list_api(self):
        lifecycle = []

        def zeroconf(**kwargs):
            lifecycle.append(("start", kwargs))
            return SimpleNamespace(close=lambda: lifecycle.append("zc.close"))

        def browser(zc, service_types, listener):
            # python-zeroconf 0.151.5 accepts str or list[str]; a tuple is
            # treated as a single name and fails during service-name parsing.
            if not isinstance(service_types, list):
                raise TypeError("multiple service types must be a list")
            self.assertEqual(service_types, list(SERVICE_TYPES))
            self.assertIsNotNone(listener)
            return SimpleNamespace(cancel=lambda: lifecycle.append("browser.cancel"))

        module = SimpleNamespace(IPVersion=SimpleNamespace(V4Only="ipv4"),
                                 Zeroconf=zeroconf, ServiceBrowser=browser)
        discovery = AirPlayDiscovery()
        with patch.dict("sys.modules", {"zeroconf": module}):
            discovery.start()
            self.assertEqual(discovery.state, "browsing", discovery.error)
            discovery.start()
        discovery.close()
        self.assertEqual(lifecycle, [("start", {"ip_version": "ipv4"}), "browser.cancel", "zc.close"])


if __name__ == "__main__":
    unittest.main()
