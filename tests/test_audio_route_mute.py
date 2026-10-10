"""System mute controls preserve capture ownership and confirmed volume facts."""
from dataclasses import replace
import unittest

from api.audio_route_controller import AudioRouteController
from api.windows_audio_devices import AudioOutputDevice
from api.windows_audio_sessions import AudioSessionEvidence, MusicAudioSource
from tests import test_audio_routes as routes


class SystemOutputMuteTests(unittest.TestCase):
    def setUp(self):
        self.backend, self.sender = routes._Backend(), routes._Sender()
        self.controller = AudioRouteController(self.backend, routes._Discovery(), self.sender,
            app_router=routes._AppRouter(lambda: None, lambda source: None), start_worker=False)
        self.addCleanup(self.controller.close)
        self.controller._refresh_devices()

    def device(self, identity="speakers"):
        return next(d for d in self.controller.snapshot().devices if d.id == identity)

    def toggle(self, identity="speakers"):
        self.assertTrue(self.controller.toggle_mute(identity))
        self.controller._step()

    def cast(self):
        self.controller.select(routes.HOME.id)
        self.controller._step()
        self.sender.events = [{"event": "streaming"}]
        self.controller._step()

    def test_selected_windows_mute_and_restore_prior_confirmed_volume(self):
        self.toggle()
        self.assertEqual((self.device().volume, self.device().volume_muted), (0, False))
        self.toggle()
        self.assertEqual(self.backend.volume_calls, [("speakers", 0), ("speakers", .6)])
        self.assertEqual(self.backend.mute_calls, [])
        self.assertEqual(self.backend.selected, [])

    def test_selected_zero_without_history_restores_thirty_percent(self):
        self.backend.outputs += (AudioOutputDevice("zero", "Zero", "speaker"),)
        self.backend.volumes["zero"], self.backend.mutes["zero"] = 0, False
        self.controller._refresh_devices()
        self.controller.select("zero")
        self.controller._step()
        self.toggle("zero")
        self.assertEqual(self.backend.volume_calls, [("zero", .3)])

    def test_positive_restore_and_wheel_unmute_only_selected_windows_endpoint(self):
        self.backend.mutes["speakers"] = True
        self.controller._refresh_devices()
        self.toggle()
        self.assertEqual(self.backend.mute_calls, [("speakers", False)])
        self.backend.mutes["speakers"] = True
        self.controller._refresh_devices()
        self.assertTrue(self.controller.set_volume("speakers", .7))
        self.controller._step()
        self.assertEqual(self.backend.mute_calls[-1], ("speakers", False))
        self.assertEqual((self.device().volume, self.device().volume_muted), (.7, False))

    def test_unselected_named_volume_keeps_hardware_mute(self):
        self.backend.mutes["headphones"] = True
        self.controller._refresh_devices()
        self.assertFalse(self.controller.toggle_mute("headphones"))
        self.assertTrue(self.controller.set_volume("headphones", .8))
        self.controller._step()
        self.assertEqual(self.backend.mute_calls, [])
        self.assertTrue(self.device("headphones").volume_muted)

    def test_unknown_selected_homepod_toggles_without_optimistic_volume(self):
        self.cast()
        self.toggle(routes.HOME.id)
        self.toggle(routes.HOME.id)
        self.assertEqual(self.sender.volume_calls, [0, .3])
        self.assertIsNone(self.device(routes.HOME.id).volume)
        self.assertFalse(self.controller.toggle_mute(routes.OTHER_HOME.id))
        self.sender.volume_state.update(volume=.72, known=True, source="receiver_ack", pending=False)
        self.controller._step()
        self.toggle(routes.HOME.id)
        self.toggle(routes.HOME.id)
        self.assertEqual(self.sender.volume_calls[-2:], [0, .72])

    def test_capture_source_mute_and_sender_lifecycle_survive_remote_toggle(self):
        self.cast()
        self.backend.mutes["speakers"] = True
        self.controller._refresh_devices()
        starts, stops = tuple(self.sender.starts), self.sender.stops
        self.assertFalse(self.controller.toggle_mute("speakers"))
        self.toggle(routes.HOME.id)
        self.assertTrue(self.backend.mutes["speakers"])
        self.assertEqual(self.backend.mute_calls, [])
        self.assertEqual((tuple(self.sender.starts), self.sender.stops), (starts, stops))

    def test_rapid_toggles_coalesce_and_preserve_prior_real_scalar(self):
        for _ in range(2):
            self.assertTrue(self.controller.toggle_mute("speakers"))
        self.controller._step()
        self.assertEqual(self.backend.volume_calls, [("speakers", .6)])
        for _ in range(3):
            self.assertTrue(self.controller.toggle_mute("speakers"))
        self.controller._step()
        self.assertEqual(self.backend.volume_calls[-1], ("speakers", 0))

    def test_transition_fences_windows_mute_and_explicit_volume_jobs(self):
        for selected_only in (False, True):
            generation = self.controller._generation
            self.controller._end_cast()
            self.controller._set_volume("speakers", 0, generation, selected_only)
        self.assertEqual(self.backend.volume_calls, [])

    def test_pending_replacement_is_not_a_confirmed_mute_target(self):
        self.controller._publish(pending_id="headphones", state="switching")
        self.assertFalse(self.controller.toggle_mute("speakers"))
        self.assertFalse(self.controller.toggle_mute("headphones"))

    def test_rejected_volume_does_not_pollute_restore_history(self):
        self.backend.volume_error = "denied"
        self.controller.set_volume("speakers", .9)
        self.controller._step()
        self.backend.volume_error = ""
        self.toggle()
        self.toggle()
        self.assertEqual(self.backend.volume_calls[-1], ("speakers", .6))

    def test_failed_remote_mute_retires_intent_without_stopping_cast(self):
        self.cast()
        self.sender.volume_accept = False
        self.toggle(routes.HOME.id)
        self.sender.volume_accept = True
        self.toggle(routes.HOME.id)
        self.assertEqual(self.sender.volume_calls, [0, 0])
        self.assertEqual(self.controller.snapshot().state, "casting")

    def test_unconfirmed_unmute_reports_failure_and_does_not_publish_requested_value(self):
        self.backend.mutes["speakers"] = True
        self.controller._refresh_devices()
        self.backend.set_endpoint_mute = lambda identity, muted: None
        self.toggle()
        self.assertIn("无法确认", self.controller.snapshot().status_message)
        self.assertTrue(self.device().volume_muted)

    def test_unconfirmed_positive_scalar_does_not_unmute_selected_endpoint(self):
        self.backend.mutes["speakers"] = True
        self.controller._refresh_devices()
        self.backend.set_endpoint_volume = lambda identity, value: None
        self.controller.set_volume("speakers", .7)
        self.controller._step()
        self.assertEqual(self.backend.mute_calls, [])
        self.assertTrue(self.backend.mutes["speakers"])
        self.assertEqual(self.device().volume, .6)
        self.assertIn("无法确认", self.controller.snapshot().status_message)


class SystemGroupOutputMuteTests(unittest.TestCase):
    def setUp(self):
        self.backend, self.sender = routes._Backend(), routes._GroupSender()
        session = AudioSessionEvidence("speakers", 310, "session", "instance")
        source = MusicAudioSource("cloudmusic.exe", 300, 1000, r"C:\Music\cloudmusic.exe",
                                 (310,), ("session",), (session,), 100.0)
        self.controller = AudioRouteController(self.backend, routes._Discovery(), routes._Sender(),
            group_sender=self.sender, source_resolver=lambda app: source,
            app_router=routes._AppRouter(lambda: source, lambda changed: None), start_worker=False)
        self.addCleanup(self.controller.close)
        self.controller._refresh_devices()
        self.controller.set_music_source("cloudmusic.exe")
        self.controller._step()
        self.controller._begin_group((routes.HOME.id, "headphones"))
        self.sender.events = self.sender.ready_events()
        self.controller._step()

    def test_group_member_restore_unmutes_selected_sink_and_preserves_capture(self):
        self.backend.mutes.update(speakers=True, headphones=True)
        self.controller._refresh_devices()
        self.assertTrue(self.controller.toggle_mute("headphones"))
        self.controller._step()
        self.assertEqual(self.backend.mute_calls, [("headphones", False)])
        self.assertTrue(self.backend.mutes["speakers"])
        self.assertFalse(self.controller.toggle_mute("speakers"))

    def test_unknown_remote_group_member_has_independent_intent_and_readback(self):
        for _ in range(2):
            self.assertTrue(self.controller.toggle_mute(routes.HOME.id))
            self.controller._step()
        self.assertEqual(self.sender.volume_calls, [(routes.HOME.id, 0), (routes.HOME.id, .3)])
        home = next(d for d in self.controller.snapshot().devices if d.id == routes.HOME.id)
        self.assertIsNone(home.volume)
        self.assertEqual(self.backend.volume_calls, [])


if __name__ == "__main__":
    unittest.main()
