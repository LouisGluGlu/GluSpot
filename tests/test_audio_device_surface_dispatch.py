import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from local.app import LocalIslandApp


class AudioDeviceSurfaceDispatchTests(unittest.TestCase):
    def _controller(self, snapshot, *, visible=False, minimized=False, active=False):
        surface = SimpleNamespace(
            isVisible=Mock(return_value=visible),
            isMinimized=Mock(return_value=minimized),
            on_audio_device_event=Mock(),
        )
        controller = LocalIslandApp.__new__(LocalIslandApp)
        controller.data_provider = SimpleNamespace(get_audio_route_snapshot=Mock(return_value=snapshot))
        controller.window = SimpleNamespace(
            music_fullscreen_overlay=surface,
            _music_surface_active=active,
            screen=Mock(return_value=object()),
        )
        controller.audio_device_notice = SimpleNamespace(close=Mock(), present=Mock())
        controller._audio_device_event_serial = 0
        return controller, surface

    @staticmethod
    def _event(**changes):
        device = AudioRouteDevice("headset", "AirPods Pro", kind="airpods-pro", is_bluetooth=True)
        return replace(AudioRouteSnapshot(devices=(device,), event_serial=1,
                                          event_name=device.name, event_message="蓝牙设备已接入"), **changes)

    def test_visible_window_and_fullscreen_receive_event_without_legacy_notice(self):
        for fullscreen in (False, True):
            with self.subTest(fullscreen=fullscreen):
                snapshot = self._event()
                controller, surface = self._controller(snapshot, visible=True, active=True)
                surface._fullscreen = fullscreen
                LocalIslandApp._poll_audio_device_notice(controller)
                surface.on_audio_device_event.assert_called_once_with(snapshot, snapshot.devices[0])
                controller.audio_device_notice.close.assert_called_once_with()
                controller.audio_device_notice.present.assert_not_called()
                controller.window.screen.assert_not_called()

    def test_existing_island_notice_closes_on_surface_entry_without_new_event(self):
        snapshot = self._event()
        controller, surface = self._controller(snapshot)
        LocalIslandApp._poll_audio_device_notice(controller)
        controller.audio_device_notice.present.assert_called_once()
        surface.isVisible.return_value = True
        controller.window._music_surface_active = True
        LocalIslandApp._poll_audio_device_notice(controller)
        controller.audio_device_notice.close.assert_called_once_with()
        surface.on_audio_device_event.assert_not_called()
        self.assertEqual(controller.audio_device_notice.present.call_count, 1)

    def test_visible_surface_receives_event_during_enter_and_return_transitions(self):
        for transition in ("_island_entering", "_island_returning"):
            with self.subTest(transition=transition):
                snapshot = self._event()
                controller, surface = self._controller(snapshot, visible=True, active=True)
                setattr(surface, transition, True)
                LocalIslandApp._poll_audio_device_notice(controller)
                surface.on_audio_device_event.assert_called_once_with(snapshot, snapshot.devices[0])
                controller.audio_device_notice.present.assert_not_called()

    def test_inactive_visible_surface_is_not_replaced_by_a_toast(self):
        snapshot = self._event()
        controller, surface = self._controller(snapshot, visible=True)
        surface.isActiveWindow = Mock(return_value=False)
        surface.raise_ = Mock()
        surface.activateWindow = Mock()
        LocalIslandApp._poll_audio_device_notice(controller)
        surface.on_audio_device_event.assert_called_once_with(snapshot, snapshot.devices[0])
        surface.raise_.assert_not_called()
        surface.activateWindow.assert_not_called()
        controller.audio_device_notice.present.assert_not_called()

    def test_minimized_event_is_consumed_without_toast_or_later_replay(self):
        snapshot = self._event()
        controller, surface = self._controller(snapshot, visible=True, minimized=True, active=True)
        LocalIslandApp._poll_audio_device_notice(controller)
        surface.on_audio_device_event.assert_not_called()
        controller.audio_device_notice.present.assert_not_called()
        surface.isMinimized.return_value = False
        LocalIslandApp._poll_audio_device_notice(controller)
        surface.on_audio_device_event.assert_not_called()
        self.assertEqual(controller._audio_device_event_serial, snapshot.event_serial)

    def test_temporarily_hidden_active_surface_does_not_open_an_island_notice(self):
        snapshot = self._event()
        controller, surface = self._controller(snapshot, active=True)
        LocalIslandApp._poll_audio_device_notice(controller)
        controller.audio_device_notice.close.assert_called_once_with()
        controller.audio_device_notice.present.assert_not_called()
        surface.on_audio_device_event.assert_not_called()

    def test_island_preserves_existing_notice_with_hidden_or_absent_surface(self):
        for absent in (False, True):
            with self.subTest(absent=absent):
                snapshot = self._event()
                controller, surface = self._controller(snapshot)
                if absent:
                    controller.window.music_fullscreen_overlay = None
                LocalIslandApp._poll_audio_device_notice(controller)
                controller.audio_device_notice.present.assert_called_once_with(
                    snapshot.event_name, snapshot.event_message, "airpods-pro", controller.window.screen.return_value)
                surface.on_audio_device_event.assert_not_called()
                controller.audio_device_notice.close.assert_not_called()

    def test_event_is_not_replayed_when_returning_to_island(self):
        snapshot = self._event()
        controller, surface = self._controller(snapshot, visible=True, active=True)
        LocalIslandApp._poll_audio_device_notice(controller)
        surface.isVisible.return_value = False
        controller.window._music_surface_active = False
        LocalIslandApp._poll_audio_device_notice(controller)
        surface.on_audio_device_event.assert_called_once()
        controller.audio_device_notice.present.assert_not_called()

    def test_duplicate_display_names_never_choose_a_device_by_order(self):
        devices = (AudioRouteDevice("one", "Headphones", kind="headphones"),
                   AudioRouteDevice("two", "Headphones", kind="airpods-pro"))
        snapshot = self._event(devices=devices, event_name="Headphones")
        controller, surface = self._controller(snapshot, visible=True)
        LocalIslandApp._poll_audio_device_notice(controller)
        surface.on_audio_device_event.assert_called_once_with(snapshot, None)

    def test_exact_event_identity_selects_device_with_duplicate_display_name(self):
        devices = (AudioRouteDevice("one", "Headphones", kind="headphones"),
                   AudioRouteDevice("two", "Headphones", kind="airpods-pro"))
        # SimpleNamespace also verifies compatibility with provider snapshots
        # while the optional exact event identity is adopted by the contract.
        values = vars(self._event(devices=devices, event_name="Headphones")).copy()
        snapshot = SimpleNamespace(**dict(values, event_device_id="two"))
        controller, surface = self._controller(snapshot, visible=True)
        LocalIslandApp._poll_audio_device_notice(controller)
        surface.on_audio_device_event.assert_called_once_with(snapshot, devices[1])

    def test_missing_explicit_identity_does_not_fall_back_to_another_same_name_device(self):
        values = vars(self._event()).copy()
        snapshot = SimpleNamespace(**dict(values, event_device_id="disappeared"))
        controller, surface = self._controller(snapshot, visible=True)
        LocalIslandApp._poll_audio_device_notice(controller)
        surface.on_audio_device_event.assert_called_once_with(snapshot, None)

    def test_group_event_forwards_snapshot_without_inventing_single_device(self):
        snapshot = self._event(event_name="同步播放", event_message="同步播放设备已连接",
                               state="casting", member_ids=("headset", "homepod"))
        controller, surface = self._controller(snapshot, visible=True)
        LocalIslandApp._poll_audio_device_notice(controller)
        surface.on_audio_device_event.assert_called_once_with(snapshot, None)


if __name__ == "__main__":
    unittest.main()
