"""The release entry owns the same responsive chrome as narrow-live."""
import itertools
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from api.providers import EmptyIslandDataProvider
from local.island_stack_visual import IslandStackVisual
from local.music_surface_layout import CONTENT_LAYOUTS
from _demos.narrow3.layout import NarrowLayout
from _demos.narrow3.narrow_window import NarrowStudyWindow
from tests.test_song_collection_surface import Collection


class ReleaseChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        available = patch.object(NarrowStudyWindow, "_available_geometry",
                                 return_value=QRect(0, 0, 1600, 1000))
        available.start()
        self.addCleanup(available.stop)
        self.settings = {"v2_owned_playback_enabled": True,
                         "v2_music_content_layout": "playlist"}
        self.provider = EmptyIslandDataProvider()
        self.provider.get_owned_playback_mode = lambda: "shuffle"
        self.provider.get_owned_playback_modes = lambda: ("sequential", "shuffle", "repeat_one")
        self.provider.set_owned_playback_mode = Mock(return_value=True)
        self.provider.is_owned_personal_fm = lambda: True
        self.provider.get_owned_personal_dj_enabled = lambda: True
        self.provider.is_owned_personal_dj_speaking = lambda: False
        self.owner = IslandStackVisual(data_provider=self.provider)
        self.owner.timer.stop()
        self.owner.settings_getter = lambda key, default=None: self.settings.get(key, default)
        self.owner.settings_setter = lambda key, value: self.settings.update({key: value})
        self.owner._music_library_back_callback = Mock()
        self.owner._music_login_callback = Mock()
        self.owner._music_login_required = lambda: False
        self.owner.media_track_id = "owned:netease:1"
        self.owner._song_collection_controller = Collection()
        self.owner._music_page_geometry = QRect(40, 20, 433, 550)
        self.owner._show_music_fullscreen(fullscreen=False, origin_geometry=QRect())
        self.window = self.owner.music_fullscreen_overlay
        self.app.processEvents()
        self.stop_timers()
        self.reveal()

    def tearDown(self):
        self.owner._application_quitting = True
        self.window.shutdown()
        self.owner.close()
        self.app.processEvents()

    def stop_timers(self):
        self.window._timer.stop()
        self.window.study_timer.stop()
        self.window._audio_output_status_timer.stop()

    def reveal(self):
        self.window.visibility.progress = 1.
        fade = self.window.visibility.opacity
        fade.start = fade.target = 1.
        fade.duration = 0.
        self.window._reset_control_idle()

    def test_release_entry_preserves_layout_and_syncs_real_capabilities_before_first_tick(self):
        self.assertIsInstance(self.window, NarrowStudyWindow)
        self.assertIs(self.window.owner, self.owner)
        self.assertEqual(self.window.size().toTuple(), (433, 550))
        self.assertEqual(self.window.mode, "playlist")
        self.assertEqual(self.settings["v2_music_content_layout"], "playlist")
        self.assertFalse(self.window.hints)
        self.assertEqual(self.window.cover_images, [])
        self.assertTrue(self.window.dj_available)
        self.assertTrue(self.window.dj_enabled)
        self.assertEqual(self.window._owned_playback_mode, "shuffle")
        self.assertEqual(self.window.loop_mode, 1)
        self.assertTrue(self.window._song_collection_state().available)
        self.provider.is_owned_personal_fm = lambda: False
        self.provider.get_owned_playback_mode = lambda: None
        self.window.tick()
        self.assertFalse(self.window.dj_available)
        self.assertFalse(self.window.dj_enabled)
        self.assertIsNone(self.window._owned_playback_mode)

    def test_all_four_modes_use_bottom_centered_transport_at_433_by_550(self):
        for mode in CONTENT_LAYOUTS:
            with self.subTest(mode=mode):
                self.window.set_content_layout(mode, animate=False)
                self.reveal()
                self.assertTrue(self.window.narrow)
                groups = self.window.glass_groups()
                transport = groups[2]
                self.assertEqual(set(transport), {"media:previous", "media:toggle_play_pause", "media:next"})
                pause = transport["media:toggle_play_pause"]
                self.assertAlmostEqual(pause.center().x(), 433 / 2)
                self.assertEqual({rect.center().y() for rect in transport.values()}, {456.})
                for first, second in itertools.combinations(transport.values(), 2):
                    self.assertFalse(first.intersects(second))
                for rect in transport.values():
                    self.assertGreaterEqual(rect.width(), 44.)
                    self.assertTrue(QRectF(self.window.rect()).contains(rect))
                    self.assertGreater(rect.top(), self.window.height() / 2)
                _content, cover, _column = self.window._content_column_geometry()
                if cover.isValid():
                    self.assertTrue(all(not cover.intersects(rect) for rect in transport.values()))
                self.assertEqual(groups[1]["library"].left(), 16.)
                self.assertEqual(set(groups[0]) - {"collection", "personal_dj"},
                                 {"audio_output", "content_layout", "playback_mode", "translation", "return"})
                image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                with patch.object(self.window, "_paint_media_controls", wraps=self.window._paint_media_controls) as legacy:
                    self.window.render(image)
                self.assertTrue(all(call.kwargs["paint_ink"] is False for call in legacy.call_args_list))
                with patch.object(self.window, "_paint_transport_icon") as icons:
                    self.window._controls_key = None
                    self.window.refresh_controls(time.monotonic())
                self.assertEqual({call.args[2] for call in icons.call_args_list},
                                 {"previous", "toggle_play_pause", "next"})

    def test_narrow_dj_disclosure_and_wide_chrome_follow_the_shared_geometry(self):
        self.assertTrue(self.window.dj_available)
        self.assertTrue(NarrowLayout(433, 550, dj_available=True).dj.isValid())
        self.assertIn("personal_dj", self.window.glass_groups()[0])
        self.window.resize(360, 550)
        self.window._finish_width_chrome()
        self.window._sync_narrow_mode()
        self.reveal()
        self.assertFalse(NarrowLayout(360, 550, dj_available=True).dj.isValid())
        self.assertNotIn("personal_dj", self.window.glass_groups()[0])
        self.window.toggle_wide(True)
        self.reveal()
        self.assertFalse(self.window.narrow)
        groups = self.window.glass_groups()
        self.assertTrue(any("library" in group for group in groups))
        self.assertTrue(any("personal_dj" in group for group in groups))
        collection = self.window._song_collection_geometry(QRectF(self.window.rect()))
        self.assertGreater(collection.center().x(), self.window.width() / 2)
        self.assertGreater(collection.top(), self.window.height() / 2)

    def test_focus_return_and_reopen_reuse_the_same_release_surface(self):
        with patch.object(self.window, "raise_") as raise_window, \
             patch.object(self.window, "activateWindow") as activate:
            self.owner._show_music_fullscreen(fullscreen=False)
        raise_window.assert_called_once()
        activate.assert_called_once()
        with patch.object(self.owner, "_current_main_target_global_rect", return_value=QRectF()):
            self.window.return_to_island()
        self.assertFalse(self.owner._music_surface_active)
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.window.study_timer.isActive())
        self.owner._show_music_fullscreen(fullscreen=False, origin_geometry=QRect())
        self.assertIs(self.owner.music_fullscreen_overlay, self.window)
        self.assertTrue(self.owner._music_surface_active)
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.study_timer.isActive())
        self.stop_timers()
        with patch.object(self.window, "return_to_island") as restore:
            self.window.close()
        restore.assert_called_once()

    def test_escape_uses_current_login_gate(self):
        self.owner._music_login_required = lambda: True
        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.owner._music_login_callback.assert_called_once()
        self.owner._music_library_back_callback.assert_not_called()

    def test_valid_origin_entry_and_return_use_shared_snapshots_with_narrow_content(self):
        self.window.set_content_layout("lyrics", animate=False)
        self.owner._restore_island_from_music_surface()
        origin = QRect(80, 50, 350, 400)
        snapshot = QImage(origin.size(), QImage.Format.Format_RGB32)
        snapshot.fill(Qt.GlobalColor.darkBlue)
        self.owner._show_music_fullscreen(fullscreen=False, origin_geometry=origin,
                                          origin_snapshot=snapshot)
        self.window._mode_animation.stop()
        self.assertTrue(self.window._island_entering)
        self.assertIs(self.window._pending_fullscreen, False)
        self.assertFalse(self.window._geometry_transition_snapshot.isNull())
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        with patch.object(self.window, "paint_narrow") as narrow_paint, \
             patch.object(self.window, "_paint_lyric_column") as lyrics, \
             patch.object(self.window, "_begin_live_press") as narrow_press:
            self.window.render(image)
            QTest.mouseClick(self.window, Qt.MouseButton.LeftButton,
                             pos=self.window.rect().center())
        narrow_paint.assert_not_called()
        lyrics.assert_not_called()
        narrow_press.assert_not_called()
        self.window.setGeometry(self.window._mode_animation.endValue())
        self.window._finish_mode_transition()
        self.assertTrue(self.window.narrow)
        self.assertFalse(self.window._island_entering)
        self.assertEqual(self.window.size().toTuple(), (433, 550))
        self.window.return_to_island()
        self.window._mode_animation.stop()
        self.assertTrue(self.window._island_returning)
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        with patch.object(self.window, "paint_narrow") as narrow_paint, \
             patch.object(self.window, "_paint_lyric_column") as lyrics:
            self.window.render(image)
        narrow_paint.assert_not_called()
        lyrics.assert_not_called()
        self.window._finish_mode_transition()
        self.assertFalse(self.owner._music_surface_active)
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.window.study_timer.isActive())


if __name__ == "__main__":
    unittest.main()
