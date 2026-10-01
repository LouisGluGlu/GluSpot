from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace

from PySide6.QtCore import QByteArray, QBuffer, QCoreApplication, QEvent, QIODevice, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from api.models import ColorToken, IslandVisualData, TimedLyricLine, TrackData
from api.providers import EmptyIslandDataProvider
from local.island_stack_visual import IslandStackVisual
from local.music_surface_window import MusicSurfaceWindow
from local.settings_window import SettingsWindow
from tests.test_mac_preferences_shell import FakeIslandWidget, FakeSettingsManager


class MusicCacheSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.island = FakeIslandWidget()
        self.settings = FakeSettingsManager()
        self.window = SettingsWindow(self.island, self.settings)

    def tearDown(self):
        self.window.close()
        self.island.close()

    def wait_until(self, condition):
        deadline = time.monotonic() + 3.0
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(condition())

    def test_clear_runs_off_gui_thread_and_notifies_on_gui_without_saving_settings(self):
        main_thread = threading.get_ident()
        entered, release = threading.Event(), threading.Event()
        worker_threads, notification_threads, calls = [], [], []
        values_before = dict(self.settings.values)

        def clear(kind):
            calls.append(kind)
            worker_threads.append(threading.get_ident())
            entered.set()
            release.wait(3.0)
            return {"deleted_files": 2, "deleted_bytes": 2097152, "errors": [], "refresh_requested": True}

        def notify(kind, report):
            notification_threads.append((kind, threading.get_ident()))

        self.island.data_provider = SimpleNamespace(clear_music_cache=clear)
        self.island.on_music_cache_cleared = notify
        try:
            self.window.clear_artwork_cache_button.click()
            self.wait_until(entered.is_set)
            self.assertFalse(self.window.clear_artwork_cache_button.isEnabled())
            self.assertFalse(self.window.clear_lyrics_cache_button.isEnabled())
            self.window._clear_music_cache("lyrics")
            responded = []
            QTimer.singleShot(0, lambda: responded.append(True))
            self.wait_until(lambda: bool(responded))
            self.assertEqual(calls, ["artwork"])
        finally:
            release.set()
            self.wait_until(lambda: self.window._music_cache_clear_job is None)
        self.assertNotEqual(worker_threads, [main_thread])
        self.assertEqual(notification_threads, [("artwork", main_thread)])
        self.assertEqual(self.settings.values, values_before)
        self.assertIn("2 个文件，2.0 MB", self.window.music_cache_status.text())
        self.assertTrue(self.window.clear_lyrics_cache_button.isEnabled())

    def test_lyrics_button_reports_partial_failure_and_reenables_both_buttons(self):
        calls = []

        def clear(kind):
            calls.append(kind)
            return {"deleted_files": 1, "deleted_bytes": 2048, "errors": ["文件被占用"], "refresh_requested": True}

        self.island.data_provider = SimpleNamespace(clear_music_cache=clear)
        self.window.clear_lyrics_cache_button.click()
        self.wait_until(lambda: self.window._music_cache_clear_job is None)
        self.assertEqual(calls, ["lyrics"])
        self.assertIn("部分缓存未能清理：文件被占用", self.window.music_cache_status.text())
        self.assertTrue(self.window.clear_artwork_cache_button.isEnabled())
        self.assertEqual(self.island.last_media_refresh_at, -999.0)

    def test_worker_exception_is_visible_and_does_not_claim_reload(self):
        def clear(kind):
            raise OSError("磁盘不可访问")

        self.island.data_provider = SimpleNamespace(clear_music_cache=clear)
        self.window.clear_artwork_cache_button.click()
        self.wait_until(lambda: self.window._music_cache_clear_job is None)
        self.assertIn("磁盘不可访问", self.window.music_cache_status.text())
        self.assertNotIn("正在重新获取", self.window.music_cache_status.text())
        self.assertEqual(self.island.last_media_refresh_at, 123.0)
        self.assertTrue(self.window.clear_artwork_cache_button.isEnabled())

    def test_unavailable_provider_does_not_start_cleanup(self):
        self.window.clear_artwork_cache_button.click()
        self.assertIsNone(self.window._music_cache_clear_job)
        self.assertIn("不支持缓存清理", self.window.music_cache_status.text())
        self.assertTrue(self.window.clear_artwork_cache_button.isEnabled())


class MusicCacheVisualHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = IslandStackVisual(EmptyIslandDataProvider())
        self.owner.timer.stop()
        self.surface = MusicSurfaceWindow(self.owner)
        self.surface._timer.stop()
        self.surface.resize(960, 540)
        self.owner.music_fullscreen_overlay = self.surface
        image = QImage(600, 600, QImage.Format.Format_RGB32)
        image.fill(QColor(100, 50, 70))
        encoded = QByteArray()
        buffer = QBuffer(encoded)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(image.save(buffer, "PNG"))
        buffer.close()
        self.data = IslandVisualData(
            track=TrackData(
                title="Cache Test", artist="Artist", lyrics=("First", "Second"),
                album_color=ColorToken(100, 50, 70), secondary_color=ColorToken(50, 25, 35),
                audio_frames=(), album_art_bytes=bytes(encoded), album_art_source="netease-original",
                album_art_lookup_state="ready", track_id="cache-test", is_playing=False,
                position_seconds=11.5, duration_seconds=100,
                timed_lyrics=(TimedLyricLine(0, "First"), TimedLyricLine(20, "Second")),
            ), messages=(), plugins=(),
        )
        self.owner._apply_visual_data(self.data)
        self.render()

    def tearDown(self):
        self.surface.shutdown()
        self.owner.music_fullscreen_overlay = None
        self.owner.close()

    def render(self):
        self.surface.render(QImage(self.surface.size(), QImage.Format.Format_RGB32))

    def test_clearing_artwork_keeps_clock_lyrics_and_decodes_identical_download_again(self):
        old_image_key = self.owner.album_art.cacheKey()
        clock = (self.owner.media_track_id, self.owner.media_position_seconds, self.owner.media_is_playing)
        rows = list(self.owner.timed_lyrics)
        self.owner.on_music_cache_cleared("artwork")
        self.assertEqual(self.owner.album_art.cacheKey(), old_image_key)
        self.assertEqual(self.owner.album_art_source, "thumbnail")
        self.assertTrue(self.surface._art_loading_waiting)
        self.assertTrue(self.surface._should_blur_preview(self.owner.album_art))
        self.assertEqual(clock, (self.owner.media_track_id, self.owner.media_position_seconds, self.owner.media_is_playing))
        self.assertEqual(rows, self.owner.timed_lyrics)
        self.owner._apply_visual_data(self.data)
        self.render()
        self.assertNotEqual(old_image_key, self.owner.album_art.cacheKey())
        self.assertFalse(self.surface._art_loading_waiting)
        self.assertFalse(self.surface._should_blur_preview(self.owner.album_art))
        self.assertEqual(clock, (self.owner.media_track_id, self.owner.media_position_seconds, self.owner.media_is_playing))
        self.assertEqual(rows, self.owner.timed_lyrics)

    def test_lyrics_clear_does_not_restart_cover_loading_or_move_playhead(self):
        old_image_key = self.owner.album_art.cacheKey()
        position = self.owner.media_position_seconds
        self.owner.on_music_cache_cleared("lyrics")
        self.assertEqual(position, self.owner.media_position_seconds)
        self.assertEqual(old_image_key, self.owner.album_art.cacheKey())
        self.assertFalse(self.surface._art_loading_waiting)
        self.assertEqual(self.owner.last_media_refresh_at, -999.0)

    def test_diagnostics_distinguish_reloaded_original_from_retained_preview(self):
        ready = self.owner.provider_diagnostics()["artwork_handoff"]
        self.assertEqual((ready["decoded_width"], ready["decoded_height"]), (600, 600))
        self.assertEqual(ready["source"], "netease-original")
        self.assertFalse(ready["surface_waiting"])
        self.owner.on_music_cache_cleared("artwork")
        pending = self.owner.provider_diagnostics()["artwork_handoff"]
        self.assertEqual(pending["display_cache_key"], ready["display_cache_key"])
        self.assertEqual(pending["source"], "thumbnail")
        self.assertTrue(pending["surface_waiting"])
        self.assertEqual(pending["surface_preview_key"], pending["display_cache_key"])
        self.assertEqual(pending["bytes_signature"], "")
        self.owner._apply_visual_data(self.data)
        self.render()
        reloaded = self.owner.provider_diagnostics()["artwork_handoff"]
        self.assertEqual(reloaded["bytes_signature"], ready["bytes_signature"])
        self.assertNotEqual(reloaded["display_cache_key"], ready["display_cache_key"])
        self.assertFalse(reloaded["surface_waiting"])

    def test_destroying_settings_during_cleanup_still_invalidates_live_music_view(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()

        def clear(kind):
            entered.set()
            release.wait(3.0)
            finished.set()
            return {"deleted_files": 0, "deleted_bytes": 0, "errors": [], "refresh_requested": True}

        self.owner.data_provider = SimpleNamespace(clear_music_cache=clear)
        window = SettingsWindow(self.owner, FakeSettingsManager())
        window.clear_artwork_cache_button.click()
        self.assertTrue(entered.wait(2.0))
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        release.set()
        self.assertTrue(finished.wait(2.0))
        deadline = time.monotonic() + 3.0
        while self.owner.album_art_signature and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertEqual(self.owner.album_art_signature, "")
        self.assertTrue(self.surface._art_loading_waiting)


if __name__ == "__main__":
    unittest.main()
