from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
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

    def test_previously_saved_identity_and_latency_are_reused_without_randomness(self):
        provider, saved = self._provider({"v2_airplay_sender_id": "02:aa:BB:01:02:03",
                                           "v2_homepod_lyric_delay_seconds": 2.3})
        with patch("api.providers.secrets.token_bytes") as random, \
             patch("api.audio_route_controller.AudioRouteController", side_effect=_Controller):
            controller = provider.start_audio_outputs()
        random.assert_not_called()
        self.assertEqual(controller.options["source_id"], "02:aa:BB:01:02:03")
        self.assertEqual(controller.options["latency_seconds"], 2.3)
        self.assertFalse(controller.options["auto_latency_enabled"])
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
        self.assertEqual(controller.calls, [("refresh",), ("select", "123"),
                                            ("add_homepod", "192.0.2.4", 7001), ("stop_cast",)])

    def test_latency_is_normalized_and_saved_before_delegating(self):
        provider, saved = self._provider()
        controller = provider._audio_route_controller = _Controller()
        for raw, expected in ((2.5, 2.5), (-1, 0), (99, 5), (float("nan"), 1.5), (None, 1.5)):
            with self.subTest(raw=raw):
                self.assertTrue(provider.set_audio_output_latency(raw))
                self.assertEqual(saved["v2_homepod_lyric_delay_seconds"], expected)
                self.assertFalse(saved["v2_homepod_auto_lyric_delay"])
                self.assertEqual(controller.calls[-1], ("set_latency", expected))

    def test_auto_latency_preference_is_saved_and_reused(self):
        provider, saved = self._provider({"v2_homepod_lyric_delay_seconds": 2.3})
        with patch("api.audio_route_controller.AudioRouteController", side_effect=_Controller):
            controller = provider.start_audio_outputs()
        self.assertFalse(controller.options["auto_latency_enabled"])
        self.assertTrue(provider.set_audio_output_auto_latency(True))
        self.assertTrue(saved["v2_homepod_auto_lyric_delay"])
        self.assertEqual(controller.calls[-1], ("set_auto_latency", True))

    def test_disabling_automation_saves_current_compensation_for_next_launch(self):
        provider, saved = self._provider()
        controller = provider._audio_route_controller = _Controller()
        controller.result = replace(controller.result, state="casting", cast_latency_seconds=1.525)
        self.assertTrue(provider.set_audio_output_auto_latency(False))
        self.assertFalse(saved["v2_homepod_auto_lyric_delay"])
        self.assertEqual(saved["v2_homepod_lyric_delay_seconds"], 1.525)

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


if __name__ == "__main__":
    unittest.main()
