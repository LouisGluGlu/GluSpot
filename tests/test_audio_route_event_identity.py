"""Device notices use confirmed endpoint IDs even when names collide."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import unittest

from api.audio_route_controller import AudioRouteController
from api.audio_routes import AudioRouteSnapshot
from api.windows_audio_devices import AudioOutputDevice
from api.windows_audio_sessions import AudioSessionEvidence, MusicAudioSource
from tests.test_audio_routes import HOME, OTHER_HOME, _AppRouter, _Backend, _Discovery, _GroupSender, _Sender


class AudioRouteEventIdentityTests(unittest.TestCase):
    def setUp(self):
        self.backend, self.discovery = _Backend(), _Discovery()
        self.single, self.group = _Sender(), _GroupSender()
        session = AudioSessionEvidence("speakers", 310, "session", "instance")
        self.source = MusicAudioSource("cloudmusic.exe", 300, 1000, r"C:\Music\cloudmusic.exe",
                                       (310,), ("session",), (session,), 100.0)
        router = _AppRouter(lambda: self.source, lambda source: setattr(self, "source", source))
        self.controller = AudioRouteController(self.backend, self.discovery, self.single,
            group_sender=self.group, source_resolver=lambda _app: self.source,
            app_router=router, start_worker=False)
        self.addCleanup(self.controller.close)
        self.controller._refresh_devices()

    def _stream_single(self, identity):
        self.assertTrue(self.controller.select(identity))
        self.controller._step()
        self.single.events = [{"event": "streaming"}]
        self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot.state, "casting")
        return snapshot

    def test_identity_default_is_empty_and_snapshot_remains_immutable(self):
        self.assertEqual(AudioRouteSnapshot().event_device_id, "")
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot.event_serial, 0)
        self.assertEqual(snapshot.event_device_id, "")
        with self.assertRaises(FrozenInstanceError):
            snapshot.event_device_id = "other"

    def test_arrivals_with_duplicate_names_announce_the_actual_endpoint(self):
        previous_serial = self.controller.snapshot().event_serial
        for identity in ("bluetooth-a", "bluetooth-b"):
            self.backend.outputs += (AudioOutputDevice(identity, "耳机", "headphones", True),)
            self.controller._refresh_devices()
            snapshot = self.controller.snapshot()
            self.assertEqual(snapshot.event_name, "耳机")
            self.assertEqual(snapshot.event_device_id, identity)
            self.assertEqual(snapshot.event_serial, previous_serial + 1)
            self.assertIn("蓝牙", snapshot.event_message)
            previous_serial = snapshot.event_serial
            self.controller._refresh_devices()
            self.assertEqual(self.controller.snapshot().event_serial, previous_serial)

    def test_local_switch_with_duplicate_names_announces_confirmed_target(self):
        self.backend.outputs = tuple(replace(device, name="耳机") for device in self.backend.outputs)
        self.controller._refresh_devices()
        for identity in ("headphones", "speakers"):
            self.assertTrue(self.controller.select(identity))
            self.controller._step()
            snapshot = self.controller.snapshot()
            self.assertEqual(snapshot.state, "ready")
            self.assertEqual(snapshot.active_id, identity)
            self.assertEqual(snapshot.event_name, "耳机")
            self.assertEqual(snapshot.event_message, "已切换音频输出")
            self.assertEqual(snapshot.event_device_id, identity)

    def test_single_airplay_confirmation_with_duplicate_names_keeps_receiver_id(self):
        self.discovery.receivers = tuple(replace(device, name="HomePod") for device in self.discovery.receivers)
        self.controller._refresh_devices()
        first = self._stream_single(HOME.id)
        self.assertEqual(first.event_name, "HomePod")
        self.assertEqual(first.event_device_id, HOME.id)
        second = self._stream_single(OTHER_HOME.id)
        self.assertEqual(second.event_name, "HomePod")
        self.assertEqual(second.event_device_id, OTHER_HOME.id)
        self.assertEqual(second.event_serial, first.event_serial + 1)

    def test_group_confirmation_clears_prior_single_device_event_identity(self):
        first = self._stream_single(HOME.id)
        self.assertEqual(first.event_device_id, HOME.id)
        self.controller.set_music_source("cloudmusic.exe")
        self.controller._step()
        self.controller._begin_group((HOME.id, OTHER_HOME.id))
        self.group.events = self.group.ready_events()
        self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot.state, "casting")
        self.assertEqual(snapshot.member_ids, (HOME.id, OTHER_HOME.id))
        self.assertEqual(snapshot.event_name, "同步播放")
        self.assertEqual(snapshot.event_message, "同步播放设备已连接")
        self.assertEqual(snapshot.event_device_id, "")
        self.assertEqual(snapshot.event_serial, first.event_serial + 1)


if __name__ == "__main__":
    unittest.main()
