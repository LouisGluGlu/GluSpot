from __future__ import annotations

import threading
import time
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch

from api.audio_route_controller import AudioRouteController
from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from api.providers import EmptyIslandDataProvider, WindowsMediaProvider


class _Controller:
    _normalize_latency = staticmethod(AudioRouteController._normalize_latency)

    def __init__(self, **options):
        self.options = options
        self.calls = []
        self.closed = False
        self.result = AudioRouteSnapshot(
            devices=(AudioRouteDevice("speakers", "扬声器", is_default=True),),
            active_id="speakers", state="ready", revision=1,
        )

    def snapshot(self):
        self.calls.append(("snapshot",))
        return self.result

    def refresh(self):
        self.calls.append(("refresh",))
        return not self.closed

    def select(self, identity):
        self.calls.append(("select", identity))
        return not self.closed

    def toggle(self, identity):
        self.calls.append(("toggle", identity))
        return not self.closed

    def set_music_source(self, source_app_id):
        self.calls.append(("source", source_app_id))
        return not self.closed

    def set_volume(self, identity, volume):
        self.calls.append(("set_volume", identity, volume))
        return not self.closed

    def stop_cast(self):
        self.calls.append(("stop_cast",))
        return not self.closed

    def add_homepod(self, host, port):
        self.calls.append(("add_homepod", host, port))
        return not self.closed

    def set_latency(self, value):
        self.calls.append(("set_latency", value))
        return not self.closed

    def set_auto_latency(self, enabled):
        self.calls.append(("set_auto_latency", enabled))
        return not self.closed

    def close(self):
        self.closed = True


class AudioRouteProviderTests(unittest.TestCase):
    def _provider(self, saved=None, *, writable=True):
        saved = {} if saved is None else saved
        provider = object.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: saved.get(key, default)
        provider._audio_route_controller = None
        provider._audio_route_init_lock = threading.Lock()
        if writable:
            provider.settings_setter = lambda key, value: saved.__setitem__(key, value)
        self.addCleanup(provider.close_audio_outputs)
        return provider, saved

    def test_snapshot_read_and_stop_before_start_are_passive(self):
        provider, saved = self._provider()
        with patch("api.audio_route_controller.AudioRouteController") as factory:
            snapshot = provider.get_audio_route_snapshot()
            self.assertEqual(snapshot, AudioRouteSnapshot())
            self.assertFalse(provider.stop_audio_cast())
            provider.close_audio_outputs()
        factory.assert_not_called()
        self.assertEqual(saved, {})

    def test_group_toggle_forwards_exact_selected_session_identity_and_reuses_controller(self):
        provider, saved = self._provider({"v2_airplay_sender_id": "02:11:22:33:44:55"})
        data = EmptyIslandDataProvider().get_visual_data()
        provider._last_data = replace(data, track=replace(data.track,
            source_app="网易云音乐", source_app_id="D:\\Apps\\CloudMusic\\cloudmusic.exe"))
        with patch("api.audio_route_controller.AudioRouteController", side_effect=_Controller) as factory:
            self.assertTrue(provider.toggle_audio_output("homepod-a"))
            self.assertTrue(provider.toggle_audio_output("bluetooth-b"))
        factory.assert_called_once()
        controller = provider._audio_route_controller
        self.assertEqual(controller.calls, [("source", "D:\\Apps\\CloudMusic\\cloudmusic.exe"),
                                            ("toggle", "homepod-a"), ("toggle", "bluetooth-b")])
        self.assertFalse(any("网易云音乐" in call for call in controller.calls))

    def test_initial_source_forward_cannot_replace_a_newer_concurrent_media_delivery(self):
        provider, _saved = self._provider({"v2_airplay_sender_id": "02:11:22:33:44:55"})
        empty = EmptyIslandDataProvider().get_visual_data()
        first = replace(empty, track=replace(empty.track, source_app_id="first.exe"))
        newer = replace(empty, track=replace(empty.track, source_app_id="second.exe"))
        provider._last_data = first
        provider._advance_delayed_media_sample = lambda data, _now: data
        provider._stabilize_incoming_position = lambda data, _now: data
        provider._apply_recent_local_seek_to_visual_data = lambda data, _now: data
        provider._refresh_future = Future()
        provider._refresh_future.set_result(newer)
        entered, release, newer_published = threading.Event(), threading.Event(), threading.Event()
        controller = _Controller()

        def forward(identity):
            if identity == "first.exe":
                entered.set()
                if not release.wait(timeout=2):
                    raise RuntimeError("initial source forwarding timed out")
            controller.calls.append(("source", identity))

        controller.set_music_source = forward
        stable = provider._stabilize_incoming_position

        def publish_newer(data, now):
            newer_published.set()
            return stable(data, now)

        provider._stabilize_incoming_position = publish_newer
        with patch("api.audio_route_controller.AudioRouteController", return_value=controller):
            with ThreadPoolExecutor(max_workers=2) as executor:
                started = executor.submit(provider.start_audio_outputs)
                self.assertTrue(entered.wait(timeout=1))
                consumed = executor.submit(provider._consume_current_refresh_future, 100)
                self.assertTrue(newer_published.wait(timeout=1))
                deadline = time.monotonic() + 1
                while provider._last_data is not newer and time.monotonic() < deadline:
                    time.sleep(.001)
                self.assertIs(provider._last_data, newer)
                self.assertFalse(consumed.done())  # It waits behind initial forwarding.
                release.set()
                self.assertIs(started.result(timeout=2), controller)
                consumed.result(timeout=2)
        self.assertEqual(controller.calls, [("source", "first.exe"), ("source", "second.exe")])
        self.assertIs(provider._last_data, newer)

    def test_first_start_generates_one_persistent_local_sender_id(self):
        provider, saved = self._provider()
        with patch("api.providers.secrets.token_bytes", return_value=bytes.fromhex("1122334455")) as random, \
             patch("api.audio_route_controller.AudioRouteController", side_effect=_Controller) as factory:
            controller = provider.start_audio_outputs()
            again = provider.start_audio_outputs()
        self.assertIs(controller, again)
        self.assertEqual(controller.options, {"source_id": "02:11:22:33:44:55", "latency_seconds": 1.5,
                                               "auto_latency_enabled": True})
        self.assertEqual(saved, {"v2_airplay_sender_id": "02:11:22:33:44:55"})
        random.assert_called_once_with(5)
        factory.assert_called_once()
        self.assertEqual(controller.calls, [])

    def test_previously_saved_identity_is_reused_but_manual_delay_is_migrated_to_automatic(self):
        provider, saved = self._provider({"v2_airplay_sender_id": "02:aa:BB:01:02:03",
                                           "v2_homepod_lyric_delay_seconds": 2.3})
        with patch("api.providers.secrets.token_bytes") as random, \
             patch("api.audio_route_controller.AudioRouteController", side_effect=_Controller):
            controller = provider.start_audio_outputs()
        random.assert_not_called()
        self.assertEqual(controller.options["source_id"], "02:aa:BB:01:02:03")
        self.assertEqual(controller.options["latency_seconds"], 1.5)
        self.assertTrue(controller.options["auto_latency_enabled"])
        self.assertEqual(saved["v2_airplay_sender_id"], "02:aa:BB:01:02:03")

    def test_invalid_settings_identity_is_replaced_before_helper_can_use_it(self):
        for invalid in (None, "", "not-a-MAC", "02:01:02:03:04:05 --host evil", "02:GG:00:00:00:00"):
            with self.subTest(invalid=invalid):
                provider, saved = self._provider({"v2_airplay_sender_id": invalid})
                with patch("api.providers.secrets.token_bytes", return_value=b"\x06\x07\x08\x09\x0a"), \
                     patch("api.audio_route_controller.AudioRouteController", side_effect=_Controller):
                    controller = provider.start_audio_outputs()
                self.assertEqual(controller.options["source_id"], "02:06:07:08:09:0A")
                self.assertEqual(saved["v2_airplay_sender_id"], "02:06:07:08:09:0A")

    def test_read_only_settings_still_get_a_process_local_identity(self):
        provider, saved = self._provider(writable=False)
        with patch("api.providers.secrets.token_bytes", return_value=b"\x01" * 5), \
             patch("api.audio_route_controller.AudioRouteController", side_effect=_Controller):
            controller = provider.start_audio_outputs()
        self.assertEqual(controller.options["source_id"], "02:01:01:01:01:01")
        self.assertEqual(saved, {})

    def test_concurrent_lazy_starts_create_one_controller_and_one_identity(self):
        provider, saved = self._provider()
        barrier = threading.Barrier(8)

        def start():
            barrier.wait(timeout=2)
            return provider.start_audio_outputs()

        with patch("api.providers.secrets.token_bytes", return_value=b"\x12" * 5) as random, \
             patch("api.audio_route_controller.AudioRouteController", side_effect=_Controller) as factory:
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(lambda _i: start(), range(8)))
        self.assertTrue(all(controller is results[0] for controller in results))
        factory.assert_called_once()
        random.assert_called_once()

    def test_ui_snapshot_keeps_controller_snapshot_immutable(self):
        provider, _saved = self._provider()
        controller = provider._audio_route_controller = _Controller()
        result = provider.get_audio_route_snapshot()
        self.assertIs(result, controller.result)
        with self.assertRaises(FrozenInstanceError):
            result.active_id = "airplay:test"
        self.assertIsInstance(result.devices, tuple)

    def test_output_commands_delegate_through_existing_provider_boundary(self):
        provider, _saved = self._provider()
        controller = provider._audio_route_controller = _Controller()
        self.assertTrue(provider.refresh_audio_outputs())
        self.assertTrue(provider.select_audio_output(123))
        self.assertTrue(provider.add_homepod("192.0.2.4", 7001))
        self.assertTrue(provider.stop_audio_cast())
        self.assertTrue(provider.set_audio_output_volume("headphones", 0.72))
        self.assertEqual(controller.calls, [("refresh",), ("select", "123"),
                                            ("add_homepod", "192.0.2.4", 7001), ("stop_cast",),
                                            ("set_volume", "headphones", 0.72)])

    def test_legacy_manual_delay_is_rejected_without_starting_or_saving_a_worker(self):
        provider, saved = self._provider()
        with patch("api.audio_route_controller.AudioRouteController") as factory:
            for raw in (2.5, -1, 99, float("nan"), None):
                self.assertFalse(provider.set_audio_output_latency(raw))
            self.assertFalse(provider.set_audio_output_auto_latency(False))
        factory.assert_not_called()
        self.assertEqual(saved, {})

    def test_legacy_automatic_false_is_ignored_and_automatic_true_is_idempotent(self):
        provider, saved = self._provider({"v2_homepod_lyric_delay_seconds": 2.3,
                                         "v2_homepod_auto_lyric_delay": False})
        with patch("api.audio_route_controller.AudioRouteController", side_effect=_Controller):
            controller = provider.start_audio_outputs()
        self.assertTrue(controller.options["auto_latency_enabled"])
        self.assertEqual(controller.options["latency_seconds"], 1.5)
        self.assertTrue(provider.set_audio_output_auto_latency(True))
        self.assertEqual(controller.calls[-1], ("set_auto_latency", True))
        self.assertFalse(saved["v2_homepod_auto_lyric_delay"])

    def test_disabling_automation_cannot_save_current_compensation_as_manual(self):
        provider, saved = self._provider()
        controller = provider._audio_route_controller = _Controller()
        controller.result = replace(controller.result, state="casting", cast_latency_seconds=1.525)
        self.assertFalse(provider.set_audio_output_auto_latency(False))
        self.assertEqual(saved, {})
        self.assertEqual(controller.calls, [])

    def test_shutdown_without_start_and_repeated_shutdown_do_not_create_controller(self):
        provider, _saved = self._provider()
        with patch("api.audio_route_controller.AudioRouteController") as factory:
            provider.close_audio_outputs()
            provider.close_audio_outputs()
        factory.assert_not_called()
        controller = provider._audio_route_controller = _Controller()
        provider.close_audio_outputs()
        provider.close_audio_outputs()
        self.assertTrue(controller.closed)

    def test_closed_controller_rejects_new_actions_instead_of_resuming_cast(self):
        provider, _saved = self._provider()
        controller = provider._audio_route_controller = _Controller()
        provider.close_audio_outputs()
        self.assertIs(provider.start_audio_outputs(), controller)
        self.assertFalse(provider.refresh_audio_outputs())
        self.assertFalse(provider.select_audio_output("speakers"))
        self.assertFalse(provider.stop_audio_cast())
        self.assertFalse(provider.set_audio_output_volume("speakers", 0.3))

    def test_shutdown_before_first_start_prevents_late_controller_creation(self):
        provider, saved = self._provider()
        provider.close_audio_outputs()
        with patch("api.audio_route_controller.AudioRouteController") as factory:
            with self.assertRaisesRegex(RuntimeError, "closed"):
                provider.start_audio_outputs()
        factory.assert_not_called()
        self.assertEqual(saved, {})

    def test_shutdown_during_lazy_construction_does_not_leave_new_worker_alive(self):
        provider, _saved = self._provider({"v2_airplay_sender_id": "02:01:02:03:04:05"})
        entered, release, shutdown_started = threading.Event(), threading.Event(), threading.Event()

        def slow_constructor(**options):
            entered.set()
            if not release.wait(timeout=2):
                raise RuntimeError("test constructor timed out")
            return _Controller(**options)

        def close():
            shutdown_started.set()
            provider.close_audio_outputs()

        with patch("api.audio_route_controller.AudioRouteController", side_effect=slow_constructor):
            with ThreadPoolExecutor(max_workers=2) as executor:
                start_result = executor.submit(provider.start_audio_outputs)
                self.assertTrue(entered.wait(timeout=1))
                close_result = executor.submit(close)
                self.assertTrue(shutdown_started.wait(timeout=1))
                release.set()
                controller = start_result.result(timeout=2)
                close_result.result(timeout=2)
        self.assertTrue(controller.closed)

    def test_empty_provider_retains_unavailable_device_contract(self):
        provider = EmptyIslandDataProvider()
        self.assertEqual(provider.get_audio_route_snapshot().state, "unavailable")
        self.assertEqual(provider.get_audio_route_snapshot().devices, ())
        self.assertFalse(provider.refresh_audio_outputs())
        self.assertFalse(provider.select_audio_output("speakers"))
        self.assertFalse(provider.stop_audio_cast())
        self.assertFalse(provider.set_audio_output_volume("speakers", 0.3))


if __name__ == "__main__":
    unittest.main()
