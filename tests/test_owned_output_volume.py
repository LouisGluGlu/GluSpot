"""User volume commands change selected outputs without restarting PCM."""
from dataclasses import replace
import unittest

from api.owned_audio_outputs import OwnedAudioOutputController
from tests.test_owned_audio_outputs import Bridge, Backend, Discovery, Sender


class ReadbackBackend(Backend):
    def __init__(self):
        super().__init__()
        self.mutes = []
        self.accept = True
    def set_endpoint_volume(self, endpoint, volume):
        self.writes.append((endpoint, volume))
        if self.accept:
            self.devices = tuple(replace(d, volume=volume) if d.id == endpoint else d for d in self.devices)
        return self.accept
    def set_endpoint_mute(self, endpoint, muted):
        self.mutes.append((endpoint, muted))
        self.devices = tuple(replace(d, volume_muted=muted) if d.id == endpoint else d for d in self.devices)
        return True


class ReadbackSender(Sender):
    def __init__(self):
        super().__init__()
        self.readback = {"known": False}
    def receiver_volume_state(self, identity): return self.readback


class OwnedVolumeTests(unittest.TestCase):
    def setUp(self):
        self.backend, self.bridge, self.sender = ReadbackBackend(), Bridge(), ReadbackSender()
        self.controller = OwnedAudioOutputController(self.bridge, backend=self.backend,
            discovery=Discovery(), sender=self.sender, start_worker=False)
        self.controller._inventory()
    def tearDown(self): self.controller.close()
    def toggle(self, identity="one"):
        self.assertTrue(self.controller.toggle_mute(identity))
        self.controller._step()
    def test_mute_restores_last_real_volume_and_zero_starts_at_thirty_percent(self):
        self.toggle()
        self.assertEqual(self.controller.snapshot().devices[0].volume, 0)
        self.toggle()
        self.assertEqual(self.backend.writes, [("one", 0), ("one", .25)])
        self.backend.devices += (replace(self.backend.devices[1], id="zero", volume=0),)
        self.controller._inventory()
        self.controller.select("zero")
        self.controller._step()
        self.toggle("zero")
        self.assertEqual(self.backend.writes[-1], ("zero", .3))
    def test_rapid_toggle_coalesces_desired_volume_without_saving_unconfirmed_value(self):
        self.assertTrue(self.controller.toggle_mute("one"))
        self.assertTrue(self.controller.toggle_mute("one"))
        self.controller._step()
        self.assertEqual(self.backend.writes, [("one", .25)])
        self.assertFalse(self.controller.toggle_mute("two"))
    def test_hardware_muted_selected_restore_and_positive_wheel_unmute(self):
        self.backend.devices = tuple(replace(d, volume_muted=True) if d.id == "one" else d for d in self.backend.devices)
        self.controller._inventory()
        self.toggle()
        self.assertEqual(self.backend.mutes, [("one", False)])
        self.backend.devices = tuple(replace(d, volume_muted=True) if d.id == "one" else d for d in self.backend.devices)
        self.controller._inventory()
        self.controller.set_volume("one", .65)
        self.controller._step()
        self.assertEqual(self.backend.writes[-1], ("one", .65))
        self.assertEqual(len(self.backend.mutes), 2)
    def test_unselected_volume_never_unmutes_endpoint(self):
        self.backend.devices = tuple(replace(d, volume_muted=True) if d.id == "two" else d for d in self.backend.devices)
        self.controller._inventory()
        self.controller.set_volume("two", .5)
        self.controller._step()
        self.assertEqual(self.backend.mutes, [])
    def test_ignored_scalar_write_never_releases_selected_hardware_mute(self):
        self.backend.devices = tuple(replace(d, volume_muted=True) if d.id == "one" else d for d in self.backend.devices)
        self.controller._inventory()
        self.backend.set_endpoint_volume = lambda *_: None
        self.controller.set_volume("one", .8)
        self.controller._step()
        self.assertEqual(self.backend.mutes, [])
        self.assertIn("失败", self.controller.snapshot().status_message)
    def test_unknown_remote_toggles_with_ack_delay_without_publishing_requested_values(self):
        self.controller._choose(("airplay:one",))
        self.sender.calls.clear()
        self.toggle("airplay:one")
        self.toggle("airplay:one")
        self.assertEqual(self.sender.calls, [("volume", "airplay:one", 0), ("volume", "airplay:one", .3)])
        self.assertIsNone(next(d.volume for d in self.controller.snapshot().devices if d.id == "airplay:one"))
        self.sender.readback = {"known": True, "source": "receiver_ack", "volume": .72}
        self.controller._inventory()
        self.toggle("airplay:one")
        self.toggle("airplay:one")
        self.assertEqual(self.sender.calls[-1], ("volume", "airplay:one", .72))
    def test_old_song_or_removed_member_mute_cannot_apply(self):
        self.controller.toggle_mute("one")
        self.bridge.token = ("different", 2)
        self.controller._step()
        self.assertEqual(self.backend.writes, [])
        self.controller.toggle_mute("one")
        self.controller.select("two")
        # Execute a later solo before the old muted-row command.
        commands = self.controller._commands
        commands[0], commands[1] = commands[1], commands[0]
        self.controller._step()
        self.assertEqual(self.backend.writes, [])
    def test_failed_write_does_not_become_a_restoration_fact(self):
        self.backend.accept = False
        self.controller.set_volume("one", .9)
        self.controller._step()
        self.assertIn("失败", self.controller.snapshot().status_message)
        self.backend.accept = True
        self.toggle()
        self.toggle()
        self.assertEqual(self.backend.writes[-1], ("one", .25))


if __name__ == "__main__":
    unittest.main()
