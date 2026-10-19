"""Selection enters its page before I/O; a live origin never restarts audio."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from PySide6.QtWidgets import QApplication
from tests import test_music_library_controller as controller_tests
from tests import test_music_loading_handoff as surface_tests
from api.online_music import OnlineSong, StreamInfo


class InstantPlaybackTests(unittest.TestCase):
    setUpClass = classmethod(lambda cls: setattr(cls, "app", QApplication.instance() or QApplication([])))
    setUp = controller_tests.MusicLibraryControllerTests.setUp
    tearDown = controller_tests.MusicLibraryControllerTests.tearDown
    open = controller_tests.MusicLibraryControllerTests.open

    def test_catalog_click_enters_window_before_any_song_or_catalog_reply(self):
        self.open()
        self.settings.set("v2_library_last_playback_surface", "fullscreen")
        row = next(row for row in self.window.collections if row.kind == "playlist")
        self.controller.play_collection(row, "default", QRect())
        self.assertEqual(self.player.status, "loading_catalog")
        self.assertIsNone(self.player.track)
        self.assertFalse(self.window.isVisible())
        calls = [call for call in self.island.calls if call[0] == "surface"]
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][1]["fullscreen"])
        self.assertEqual(calls[0][1]["loading_selection"][0], row.title)
        self.player.play_queue((self.song,))
        self.assertEqual(len([c for c in self.island.calls if c[0] == "surface"]), 1)

    def test_same_daily_origin_resumes_paused_position_without_catalog_request(self):
        self.open()
        row = self.window.collections[0]
        self.controller.play_collection(row, "default", QRect())
        self.player.play_queue((self.song,))
        self.player.track = replace(self.player.track, position_seconds=41, is_playing=False)
        self.assertTrue(self.controller.return_from_music_surface())
        before = tuple(self.player.calls)
        self.controller.play_collection(row, "default", QRect())
        self.assertEqual(tuple(self.player.calls), before)
        self.assertEqual(self.player.track.position_seconds, 41)
        self.assertFalse(self.player.track.is_playing)
        self.assertFalse(self.window.isVisible())

    def test_replaced_queue_invalidates_same_collection_identity(self):
        self.open()
        row = self.window.collections[0]
        self.controller.play_collection(row, "default", QRect())
        self.player.play_queue((self.song,))
        self.player.play_queue((OnlineSong("netease", "elsewhere", "Elsewhere"),))
        self.controller.return_from_music_surface()
        self.controller.play_collection(row, "default", QRect())
        self.assertEqual(self.player.calls[-1], ("daily", "netease"))

    def test_library_return_preserves_dragged_geometry_and_cancels_initial_load(self):
        self.open()
        expected = QRect(84, 97, 660, 550)
        self.window.setGeometry(expected)
        self.controller.play_collection(self.window.collections[0], "default", QRect())
        self.assertTrue(self.controller.return_from_music_surface())
        self.app.processEvents()
        self.assertEqual(self.window.geometry(), expected)
        self.assertIsNone(self.controller._opening)
        self.assertTrue(self.window.isVisible())

    def test_cancel_pending_other_platform_returns_its_library_not_retained_audio_source(self):
        self.player.play_queue((self.song,))
        self.open("qq")
        self.window.set_collections((controller_tests.LibraryCollection("qq:daily", "daily", "Daily", ""),))
        self.controller.play_collection(self.window.collections[0], "default", QRect())
        self.assertTrue(self.controller.return_from_music_surface())
        self.assertEqual(self.window._account.source_id, "qq")
        self.assertEqual(self.controller._source, "qq")


class OriginTokenTests(unittest.TestCase):
    setUpClass = classmethod(lambda cls: setattr(cls, "app", QApplication.instance() or QApplication([])))
    setUp = controller_tests.OwnedControllerHandoffTests.setUp
    tearDown = controller_tests.OwnedControllerHandoffTests.tearDown

    def test_retained_queue_next_cancels_pending_without_claiming_playlist_origin(self):
        self.player.play_queue((OnlineSong("netease", "old", "Old"), self.song))
        token, _ = self.play_tasks.latest("stream")
        self.play_tasks.completed.emit(token, StreamInfo("netease", "old", "https://example.test/old.mp3", True), None)
        self.window.search.clear()
        row = controller_tests.LibraryCollection("playlist:netease:new-list", "playlist", "New list", "", "new-list")
        self.window.set_collections((row,))
        self.controller.play_collection(row, "default", QRect())
        before = self.player.playback_origin_token()
        self.player.send_playback_command("next")
        token, _ = self.play_tasks.latest("stream")
        self.play_tasks.completed.emit(token, StreamInfo("netease", "new", "https://example.test/new.mp3", True), None)
        self.assertEqual(self.player.playback_origin_token(), before)
        self.assertIsNone(self.controller._opening)
        self.assertIsNone(self.controller._active_collection)
        self.assertFalse(self.controller._opening_ready)

    def test_origin_survives_enqueue_and_next_but_not_queue_replacement_or_stop(self):
        self.player.play_queue((self.song, OnlineSong("netease", "second", "Second")))
        token = self.player.playback_origin_token()
        click_token = self.player.playback_queue_token()
        self.player.enqueue_next(OnlineSong("netease", "extra", "Extra"))
        self.assertEqual(self.player.playback_origin_token(), token)
        self.assertNotEqual(self.player.playback_queue_token(), click_token)
        self.player.send_playback_command("next")
        self.assertEqual(self.player.playback_origin_token(), token)
        self.player.play_queue((self.song,))
        self.assertNotEqual(self.player.playback_origin_token(), token)
        self.player.stop()
        self.assertIsNone(self.player.playback_origin_token())


class PendingSurfaceTests(unittest.TestCase):
    setUpClass = classmethod(lambda cls: setattr(cls, "app", QApplication.instance() or QApplication([])))
    setUp = surface_tests.MusicLoadingHandoffTests.setUp
    tearDown = surface_tests.MusicLoadingHandoffTests.tearDown
    render = surface_tests.MusicLoadingHandoffTests.render

    def test_pending_surface_paints_skeleton_and_keeps_library_back_available_without_track(self):
        self.owner.data_provider = SimpleNamespace(_owned_active=lambda: False)
        self.owner._music_library_back_callback = Mock()
        self.window.set_library_loading("Pending playlist", QImage())
        self.assertTrue(self.window._library_back_available())
        frame = self.render(100.5)
        self.assertFalse(frame.isNull())
        self.assertTrue(self.window.return_to_library())
        self.owner._music_library_back_callback.assert_called_once()
        self.window.set_library_loading()
        self.assertFalse(self.window._library_back_available())


if __name__ == "__main__":
    unittest.main()
