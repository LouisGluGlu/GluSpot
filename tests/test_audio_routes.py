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


HOME = AudioRouteDevice("airplay:AABBCCDDEEFF", "书房 HomePod", "homepod", "airplay",
                       host="192.0.2.10", port=7000, device_id="AABBCCDDEEFF")
OTHER_HOME = replace(HOME, id="airplay:112233445566", name="客厅 HomePod", host="192.0.2.11")


class _Backend:
    def __init__(self):
        self.outputs = (AudioOutputDevice("speakers", "电脑扬声器", "speaker", False, True),
                        AudioOutputDevice("headphones", "蓝牙耳机", "headphones", True))
        self.selected = []
        self.error = ""
        self.closed = False

    def list_outputs(self):
        if self.error:
            raise OSError(self.error)
        return self.outputs

    def set_default(self, identity):
        self.selected.append(identity)
        self.outputs = tuple(replace(d, is_default=d.id == identity) for d in self.outputs)

    def close(self):
        self.closed = True


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

    def start(self, device, source):
        if self.start_error:
            raise RuntimeError(self.start_error)
        self.starts.append((device.id, source))
        self.events = []
        self.running = True

    def stop(self):
        self.stops += 1
        self.running = False
        self.events = []
        if self.stop_error:
            raise OSError(self.stop_error)

    def poll_events(self):
        events, self.events = tuple(self.events), []
        return events


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

    def test_streaming_event_confirms_target_and_applies_calibrated_delay(self):
        self.controller.set_latency(2.25)
        self.controller._step()
        snapshot = self._cast()
        self.assertEqual(snapshot.active_id, HOME.id)
        self.assertEqual(snapshot.state, "casting")
        self.assertEqual(snapshot.pending_id, "")
        self.assertEqual(snapshot.cast_latency_seconds, 2.25)

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

    def test_latency_handles_nonfinite_values_and_updates_live_cast_only(self):
        for value, expected in ((float("nan"), 1.5), (float("inf"), 1.5), (None, 1.5), (-1, 0), (99, 5)):
            self.controller.set_latency(value)
            self.controller._step()
            self.assertEqual(self.controller._latency, expected)
            self.assertEqual(self.controller.snapshot().cast_latency_seconds, 0.0)
        self._cast()
        self.controller.set_latency(1.75)
        self.controller._step()
        self.assertEqual(self.controller.snapshot().cast_latency_seconds, 1.75)

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
