from __future__ import annotations

import threading
import unittest
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest.mock import patch

from api.airplay_discovery import AirPlayDiscovery, SERVICE_TYPES, receiver_from_service
from api.audio_route_controller import AudioRouteController
from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from api.windows_audio_devices import AudioOutputDevice


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


class AudioRouteControllerTests(unittest.TestCase):
    def setUp(self):
        self.backend, self.discovery, self.sender = _Backend(), _Discovery(), _Sender()
        self.controller = AudioRouteController(self.backend, self.discovery, self.sender, start_worker=False)
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
        self.assertEqual(silent.active_id, "speakers")
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
                                         start_worker=False, latency_seconds=3.2, auto_latency_enabled=False)
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


class AirPlayDiscoveryTests(unittest.TestCase):
    @staticmethod
    def _info(service_type, label="书房 HomePod", *, port=7000, properties=None, addresses=None):
        return SimpleNamespace(name=label + "." + service_type, port=port,
            properties=properties if properties is not None else {b"deviceid": b"AA:BB:CC:DD:EE:FF", b"model": b"AudioAccessory5,1"},
            parsed_addresses=lambda: addresses if addresses is not None else ["fe80::1", "192.0.2.10"])

    def test_receiver_parses_homepod_unicode_identity_and_selects_ipv4(self):
        device = receiver_from_service(self._info(SERVICE_TYPES[0]), SERVICE_TYPES[0])
        self.assertEqual(device, HOME)

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
