"""Pure Windows groups reuse music capture, restoration and helper lifecycle."""
from __future__ import annotations

from dataclasses import replace
import unittest
from types import SimpleNamespace

from api.airplay_group_process import AirPlayGroupProcess
from api.airplay_process import AirPlayProcess
from api.audio_routes import AudioRouteDevice
from api.audio_route_controller import AudioRouteController
from api.windows_audio_devices import AudioOutputDevice
from api.windows_audio_sessions import AudioSessionEvidence, MusicAudioSource
from tests.test_audio_routes import HOME, _Backend, _Discovery, _Sender, _GroupSender, _AppRouter


class WindowsOnlyGroupControllerTests(unittest.TestCase):
    def setUp(self):
        self.backend, self.discovery = _Backend(), _Discovery()
        self.single, self.group = _Sender(), _GroupSender()
        self.backend.outputs = (replace(self.backend.outputs[0], is_bluetooth=True),
                                self.backend.outputs[1], AudioOutputDevice("spare", "USB source", "speaker"))
        self.backend.mutes["spare"], self.backend.volumes["spare"] = False, .4
        session = AudioSessionEvidence("speakers", 310, "session", "instance")
        self.source = MusicAudioSource("cloudmusic.exe", 300, 1000, r"C:\Music\cloudmusic.exe",
                                       (310,), ("session",), (session,), 100.0)
        self.router = _AppRouter(lambda: self.source, lambda source: setattr(self, "source", source))
        self.router.allow = True
        self.controller = AudioRouteController(self.backend, self.discovery, self.single,
            group_sender=self.group, source_resolver=lambda _app: self.source,
            app_router=self.router, start_worker=False)
        self.addCleanup(self.controller.close)
        self.controller._refresh_devices()
        self.controller.set_music_source("cloudmusic.exe")
        self.controller._step()

    def _connect(self):
        self.assertTrue(self.controller.toggle("headphones"))
        self.controller._step()
        self.group.events = self.group.ready_events()
        self.controller._step()
        return self.controller.snapshot()

    def test_second_bluetooth_checkbox_reuses_one_verified_music_capture_group(self):
        self.controller.toggle("headphones")
        self.controller._step()
        waiting = self.controller.snapshot()
        self.assertEqual(waiting.state, "connecting")
        self.assertEqual(waiting.pending_ids, ("speakers", "headphones"))
        self.assertEqual(waiting.member_ids, ())
        self.assertEqual(waiting.cast_source_id, "spare")
        self.assertEqual(waiting.capture_scope, "music_process_tree")
        self.assertEqual(self.group.starts, [(("speakers", "headphones"), "spare", 300, 1000)])
        self.assertEqual(self.single.starts, [])
        self.assertEqual(self.backend.selected, [])
        self.assertEqual({session.endpoint_id for session in self.source.sessions}, {"spare"})
        self.group.events = self.group.ready_events()
        self.controller._step()
        self.assertEqual(self.controller.snapshot().member_ids, ("speakers", "headphones"))
        starts = len(self.group.starts)
        self.controller._refresh_devices()
        self.controller._step()
        self.assertEqual(len(self.group.starts), starts) # Existing running representative fences retries.

    def test_stale_or_silent_capture_does_not_confirm_windows_group(self):
        self.controller.toggle("headphones")
        self.controller._step()
        self.group.events = [{"event": "waiting_audio"}, {"event": "streaming"}]
        self.controller._step()
        self.assertEqual(self.controller.snapshot().member_ids, ())
        silence = self.group.ready_events()[1]
        silence["capture_signal"]["meaningful_samples"] = 0
        self.group.events = [silence]
        self.controller._step()
        self.assertEqual(self.controller.snapshot().member_ids, ())
        self.group.events = self.group.ready_events()
        self.controller._step()
        self.assertEqual(self.controller.snapshot().member_ids, ("speakers", "headphones"))

    def test_owned_source_mute_and_player_route_restore_before_single_output(self):
        self._connect()
        guard = self.router.guards[-1]
        self.backend.mutes["spare"] = True
        self.group.needs_local_output_restore = True
        self.group.source_evidence = {"endpoint_id": "spare", "original_muted": False, "phase": "muted"}
        order = []
        stop, mute, restore, select = self.group.stop, self.backend.set_endpoint_mute, guard.restore, self.backend.set_default
        self.group.stop = lambda: (order.append("stop_all_renderers"), stop())[-1]
        self.backend.set_endpoint_mute = lambda device, value: (order.append("restore_source_mute"), mute(device, value))[-1]
        guard.restore = lambda: (order.append("restore_player_route"), restore())[-1]
        self.backend.set_default = lambda device: (order.append("select_remaining_output"), select(device))[-1]
        self.controller.toggle("headphones")
        self.controller._step()
        self.assertEqual(order, ["stop_all_renderers", "restore_source_mute", "restore_player_route", "select_remaining_output"])
        self.assertFalse(self.backend.mutes["spare"])
        self.assertEqual(self.controller.snapshot().active_id, "speakers")
        self.assertEqual(self.controller.snapshot().member_ids, ())
        self.assertEqual(self.controller.snapshot().capture_scope, "")
        self.assertEqual(self.single.starts, [])
        self.assertEqual(self.backend.volume_calls, [])

    def test_helper_error_and_disconnected_member_stop_all_and_restore_route(self):
        for failure in ("helper", "member"):
            with self.subTest(failure=failure):
                self._connect()
                guard = self.router.guards[-1]
                if failure == "helper":
                    self.group.events = [{"event": "error", "message": "Renderer failed"}]
                    self.controller._step()
                else:
                    self.backend.outputs = tuple(device for device in self.backend.outputs if device.id != "headphones")
                    self.controller._refresh_devices()
                self.assertFalse(self.group.running)
                self.assertTrue(guard.restored)
                self.assertEqual(self.controller.snapshot().state, "error")
                self.assertEqual(self.controller.snapshot().member_ids, ())
                self.assertEqual({session.endpoint_id for session in self.source.sessions}, {"speakers"})

    def test_external_default_change_restores_local_group_with_transport_neutral_notice(self):
        self._connect()
        guard = self.router.guards[-1]
        self.backend.outputs = tuple(replace(device, is_default=device.id == "headphones")
                                     for device in self.backend.outputs)
        self.controller._refresh_devices()
        snapshot = self.controller.snapshot()
        self.assertFalse(self.group.running)
        self.assertTrue(guard.restored)
        self.assertEqual(snapshot.state, "error")
        self.assertEqual(snapshot.member_ids, ())
        self.assertEqual(snapshot.status_message, "电脑输出已改变，同步播放已停止；请重新选择设备")
        self.assertNotIn("HomePod", snapshot.status_message)
        self.assertEqual(self.backend.selected, [])

    def test_unnamed_helper_stop_restores_local_group_with_transport_neutral_notice(self):
        self._connect()
        guard = self.router.guards[-1]
        self.group.events = [{"event": "stopped", "message": ""}]
        self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertFalse(self.group.running)
        self.assertTrue(guard.restored)
        self.assertEqual(snapshot.state, "error")
        self.assertEqual(snapshot.member_ids, ())
        self.assertEqual(snapshot.status_message, "同步播放已中断")
        self.assertNotIn("HomePod", snapshot.status_message)
        self.assertEqual(self.backend.selected, [])

    def test_removing_last_airplay_from_three_output_group_keeps_two_windows_outputs(self):
        self.controller._begin_group((HOME.id, "speakers", "headphones"))
        self.group.events = self.group.ready_events()
        self.controller._step()
        self.controller.toggle(HOME.id)
        self.controller._step()
        self.assertEqual(self.group.starts[-1][0], ("speakers", "headphones"))
        self.assertEqual(self.backend.selected, [])
        self.group.events = self.group.ready_events()
        self.controller._step()
        self.assertEqual(self.controller.snapshot().member_ids, ("speakers", "headphones"))
        self.assertEqual(self.controller.snapshot().capture_scope, "music_process_tree")

    def test_no_safe_nonbluetooth_spare_fails_without_broadening_or_selecting(self):
        self.backend.outputs = tuple(device for device in self.backend.outputs if device.id != "spare")
        self.controller._refresh_devices()
        self.controller.toggle("headphones")
        self.controller._step()
        self.assertEqual(self.controller.snapshot().state, "error")
        self.assertIn("空闲电脑输出", self.controller.snapshot().status_message)
        self.assertEqual(self.group.starts, [])
        self.assertEqual(self.single.starts, [])
        self.assertEqual(self.backend.selected, [])

    def test_latency_evidence_continues_for_windows_only_representative(self):
        self._connect()
        self.group.estimate = {"seconds": 1.9}
        self.controller._update_latency()
        self.assertEqual(self.controller.snapshot().cast_latency_estimate_seconds, 1.9)
        self.controller.stop_cast()
        self.controller._step()
        self.assertFalse(self.group.running)
        self.assertEqual(self.controller.snapshot().capture_scope, "")
        self.assertEqual(self.controller.snapshot().cast_latency_seconds, 0)


class WindowsOnlyGroupProcessTests(unittest.TestCase):
    def setUp(self):
        self.source = MusicAudioSource("music.exe", 300, 1000, r"C:\Music\music.exe", (310,), ("s",),
                                      (AudioSessionEvidence("source", 310, "s", "i"),), 100.0)
        self.devices = tuple(AudioRouteDevice(f"bt-{index}", "Headphones", "headphones", is_bluetooth=True)
                             for index in range(8))

    def test_windows_only_two_to_eight_accept_same_capture_contract_and_collision_fences(self):
        for count in range(2, 9):
            receivers, locals_ = AirPlayGroupProcess._validate_group(self.devices[:count], self.source, "source")
            self.assertEqual(receivers, ())
            self.assertEqual(locals_, tuple(d.id for d in self.devices[:count]))
        invalid = [self.devices[:1], self.devices + (replace(self.devices[0], id="ninth"),),
                   (self.devices[0], replace(self.devices[1], id="SOURCE")),
                   (self.devices[0], replace(self.devices[1], id="BT-0")),
                   (self.devices[0], replace(self.devices[1], available=False))]
        for devices in invalid:
            with self.subTest(devices=devices), self.assertRaises(ValueError):
                AirPlayGroupProcess._validate_group(devices, self.source, "source")

    def test_named_local_evidence_supplies_honest_shared_clock_delay(self):
        sender = AirPlayGroupProcess()
        sender._local_endpoints = ("bt-0", "bt-1")
        sender._process = SimpleNamespace(poll=lambda: None)
        event = {"event": "stats", "timing": {"method": "windows_shared_pcm_clock", "valid": True,
                 "estimated_delay_ms": 1770, "presentation_buffer_ms": 1750, "input_queue_ms": 20},
                 "members": [], "local_renders": [dict(endpoint_id=device.id, ready=True, armed=True,
                  playing=True, meaningful_samples=10) for device in self.devices[:2]]}
        sender._remember_native_event(sender._mute_state, event)
        self.assertAlmostEqual(sender.latency_estimate()["seconds"], 1.77)
        self.assertEqual(sender.latency_estimate()["source"], "windows_shared_pcm_clock")
        self.assertEqual(sender.latency_estimate()["presentation_buffer_seconds"], 1.75)
        self.assertNotIn("protocol_buffer_seconds", sender.latency_estimate())
        self.assertEqual(sender.receiver_states(), {})
        wrong_method = dict(event, timing=dict(event["timing"], method="protocol_plus_input_queue",
                                               protocol_buffer_ms=1750))
        sender._remember_native_event(sender._mute_state, wrong_method)
        self.assertIsNone(sender.latency_estimate())
        event["local_renders"][1]["meaningful_samples"] = 0
        sender._remember_native_event(sender._mute_state, event)
        self.assertIsNone(sender.latency_estimate())
        sender._process = None
        single = AirPlayProcess()
        single._process = SimpleNamespace(poll=lambda: None)
        single._remember_native_event(single._mute_state, event)
        self.assertIsNone(single.latency_estimate()) # Single AirPlay cannot accept a local-group claim.
        single._process = None


if __name__ == "__main__":
    unittest.main()
