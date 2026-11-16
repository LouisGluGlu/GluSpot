"""Library presentation follows the same selected snapshot as the live island."""
from dataclasses import replace
import unittest
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication

from api.models import IslandVisualData
from api.online_music import OnlineSong
from api.providers import EmptyIslandDataProvider, empty_visual_data
from local.island_stack_visual import IslandStackVisual
from local.music_library_controller import MusicLibraryController
from local.music_library_window import MusicLibraryWindow
from tests.test_music_library_controller import Entry, Player, Service, track
from tests.test_playback_entry_controller import FakeSettings, FakeTasks


class LibraryCurrentTrackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = FakeSettings(completed=True, owned=True, system=False)
        self.player = Player()
        self.player.track = track(OnlineSong("netease", "old-owned", "Old owned track"))
        self.player.send_playback_command = Mock()
        self.current = replace(self.player.track, title="Selected system track",
                               track_id="system-current", lyrics=("Current lyric",))
        self.provider = EmptyIslandDataProvider(IslandVisualData(self.current, (), ()))
        self.island = IslandStackVisual(data_provider=self.provider)
        self.island.timer.stop()
        self.island._send_playback_command_async = Mock()
        self.window = MusicLibraryWindow()
        self.controller = MusicLibraryController(
            self.settings, Service(), self.player, self.island, Entry(),
            window=self.window, tasks=FakeTasks(), art_tasks=FakeTasks(),
        )

    def tearDown(self):
        self.controller.close()
        self.window.deleteLater()
        self.island.close()
        self.island.deleteLater()
        self.app.processEvents()

    def publish(self, current):
        self.island._apply_visual_data(IslandVisualData(current, (), ()))

    def assert_shared(self, current):
        self.assertIs(self.window._now_playing, current)
        child = self.window.now_playing_island
        self.assertEqual(child.media_track_id, self.island.media_track_id)
        self.assertEqual(child.track_title, self.island.track_title)
        self.assertEqual(child.lyrics, self.island.lyrics)

    def test_created_library_uses_selected_snapshot_instead_of_inactive_owned_track(self):
        self.assert_shared(self.current)
        self.assertNotEqual(self.window._now_playing.track_id, self.player.track.track_id)

    def test_rapid_switches_ignore_delayed_owned_notifications_and_keep_enrichment(self):
        for index in range(4):
            current = replace(self.current, track_id=f"system-{index}", title=f"Track {index}",
                              lyrics=(f"Lyric {index}",))
            self.publish(current)
            self.assert_shared(current)
            # These signals can be an old source's delayed artwork/position
            # result. Only the selected provider's applied frame may fan out.
            self.player.track = replace(self.player.track, position_seconds=index)
            self.player.changed.emit()
            self.assert_shared(current)
        enriched = replace(current, lyrics=("Late current lyric",))
        self.publish(enriched)
        self.assert_shared(enriched)

    def test_hidden_library_receives_switch_and_resumes_current_child_store(self):
        self.window.show()
        self.app.processEvents()
        self.window.hide()
        child = self.window.now_playing_island
        self.assertTrue(child._lyric_presentation_store.closed)
        current = replace(self.current, track_id="hidden-switch", title="Hidden switch")
        self.publish(current)
        self.assert_shared(current)
        self.window.show()
        self.app.processEvents()
        self.assertFalse(child._lyric_presentation_store.closed)
        self.assert_shared(current)

    def test_no_session_clears_library_but_paused_track_stays_current(self):
        self.island._apply_visual_data(empty_visual_data())
        self.assertIsNone(self.window._now_playing)
        paused = replace(self.current, is_playing=False, position_seconds=12)
        self.publish(paused)
        self.assert_shared(paused)

    def test_embedded_controls_use_selected_island_command_route(self):
        self.window.now_playing_island.commandRequested.emit("next")
        self.island._send_playback_command_async.assert_called_once_with("next")
        self.player.send_playback_command.assert_not_called()

    def test_opening_selected_system_track_does_not_reapply_old_owned_data(self):
        self.settings.values["v2_owned_playback_enabled"] = False
        self.controller._navigation.origin_frame = Mock(return_value=None)
        self.controller._present_music_surface = Mock(return_value=True)
        self.assertTrue(self.controller.open_now_playing())
        self.assert_shared(self.current)
        self.controller._present_music_surface.assert_called_once_with("window", None)

    def test_closed_controller_releases_shared_track_subscription(self):
        self.controller.close()
        current = replace(self.current, track_id="after-close", title="After close")
        self.publish(current)
        self.assertIs(self.window._now_playing, self.current)


if __name__ == "__main__":
    unittest.main()
