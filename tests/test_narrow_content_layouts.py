"""Four shared content modes keep their rendered actions in the narrow view."""
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from api.models import IslandVisualData
from api.online_music import OnlineSong
from api.providers import EmptyIslandDataProvider
from local.island_stack_visual import IslandStackVisual
from local.music_surface_layout import CONTENT_LAYOUTS
from _demos.narrow3.narrow_window import NarrowStudyWindow
from tests.test_narrow_v4 import lyric_track


class NarrowContentLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        track = lyric_track(timed_lyrics=lyric_track().timed_lyrics[:4])
        self.provider = EmptyIslandDataProvider(IslandVisualData(track, (), ()))
        self.songs = tuple(OnlineSong("netease", str(i), f"Song {i}") for i in range(12))
        self.current = 2
        self.provider.get_owned_playback_queue = lambda: (37, self.current, self.songs)
        self.provider.prefetch_owned_queue_artwork = Mock()
        self.owner = IslandStackVisual(data_provider=self.provider)
        self.owner.timer.stop()
        self.saved = {}
        self.owner.settings_setter = lambda key, value: self.saved.update({key: value})
        self.window = NarrowStudyWindow(self.owner, "B", "left", live=True)
        self.window.hints = False
        self.owner._apply_visual_data(self.provider.get_visual_data())
        for _ in range(100):
            if self.window.source_lines():
                break
            QTest.qWait(10)
        self.assertTrue(self.window.source_lines())
        self.owner.album_art = QImage(128, 128, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(Qt.GlobalColor.darkBlue)
        self.window.show()
        self.app.processEvents()
        self.window._timer.stop()
        self.window.study_timer.stop()
        self.window._audio_output_status_timer.stop()
        self.reveal()

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.owner._application_quitting = True
        self.owner.close()
        self.app.processEvents()

    def reveal(self):
        self.window.visibility.progress = 1.
        fade = self.window.visibility.opacity
        fade.start = fade.target = 1.
        fade.duration = 0
        self.window._reset_control_idle()

    def render(self):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.window.render(image)
        return bytes(image.constBits())

    def test_narrow_button_and_tab_cycle_all_modes_and_resize_keeps_selection(self):
        with patch.object(self.window, "_send_media_command") as media, \
             patch.object(self.window, "_seek_to_lyric") as seek:
            for keyboard in (False, True):
                self.window.set_content_layout("lyrics", animate=False)
                for expected in ("playlist", "cover", "cover_lyrics", "lyrics"):
                    self.reveal()
                    if keyboard:
                        QTest.keyClick(self.window, Qt.Key.Key_Tab)
                    else:
                        point = self.window.glass_groups()[0]["content_layout"].center()
                        QTest.mouseClick(self.window, Qt.MouseButton.LeftButton,
                                         Qt.KeyboardModifier.NoModifier, point.toPoint())
                    self.assertEqual(self.window.mode, expected)
                    self.assertEqual(self.window._effective_content_layout(), expected)
                    self.assertEqual(self.saved["v2_music_content_layout"], expected)
            for layout in CONTENT_LAYOUTS:
                self.window.set_content_layout(layout, animate=False)
                self.saved.clear()
                for wide in (True, False):
                    self.window.toggle_wide(wide)
                    self.assertEqual(self.window.mode, layout)
                self.assertEqual(self.saved, {})
            media.assert_not_called()
            seek.assert_not_called()

    def test_each_narrow_mode_renders_its_own_content_and_only_visible_hits(self):
        for width in (360, 483):
            self.window.resize(width, 640)
            rasters = []
            for layout in CONTENT_LAYOUTS:
                with self.subTest(width=width, layout=layout):
                    self.window.set_content_layout(layout, animate=False)
                    self.reveal()
                    rasters.append(self.render())
                    if layout in {"lyrics", "cover_lyrics"}:
                        self.assertTrue(self.window._lyric_hit_rects)
                        self.assertEqual(self.window._queue_hit_rects, {})
                        point = next(iter(self.window._lyric_hit_rects.values())).center()
                        action = self.window._live_action_at(point)
                        self.assertIsInstance(action, tuple)
                        self.assertEqual(action[0], "lyric")
                    else:
                        self.assertEqual(self.window._lyric_hit_rects, {})
                    if layout == "playlist":
                        self.assertTrue(self.window._queue_hit_rects)
                        index, rect = next(iter(self.window._queue_hit_rects.items()))
                        point = rect.center()
                        self.assertEqual(self.window._live_action_at(point), ("queue", index, 37))
                        with patch.object(self.window, "_select_playback_queue") as select:
                            QTest.mouseClick(self.window, Qt.MouseButton.LeftButton,
                                             Qt.KeyboardModifier.NoModifier, point.toPoint())
                            select.assert_called_once_with(index, 37)
                    else:
                        self.assertEqual(self.window._queue_hit_rects, {})
            self.assertEqual(len(set(rasters)), 4)

    def test_narrow_queue_prefetches_visible_artwork_and_animates_current_row(self):
        self.window.set_content_layout("playlist", animate=False)
        self.render()
        self.window._flush_queue_artwork_hint()
        call = self.provider.prefetch_owned_queue_artwork.call_args
        self.assertEqual(call.kwargs, {"expected_queue_token": 37})
        self.assertTrue(call.args[0])
        self.assertTrue(set(call.args[0]).issubset(self.window._queue_hit_rects))
        self.assertLess(len(call.args[0]), len(self.songs))
        self.current += 1
        self.render()
        self.assertTrue(self.window._queue_focus_motion.state())

    def test_paused_shared_modes_honor_async_updates_on_the_next_tick(self):
        for layout in CONTENT_LAYOUTS:
            with self.subTest(layout=layout):
                self.window.set_content_layout(layout, animate=False)
                self.window.study_timer.start(60000)
                with patch("_demos.narrow3.narrow_window.time.monotonic", return_value=1000.):
                    self.window.tick()
                    self.window.tick()
                    before = self.window.diagnostic_snapshot()["commits"]
                    self.window.update(QRect(40, 100, 20, 20))
                    self.window.tick()
                self.assertEqual(self.window.diagnostic_snapshot()["commits"], before + 1)
                self.window.study_timer.stop()

    def test_narrow_temporary_cover_uses_effective_mode_and_new_pending_track_restores(self):
        self.window.set_content_layout("cover_lyrics", animate=False)
        original_settings = dict(self.saved)
        empty = lyric_track(timed_lyrics=(), lyrics=("暂无歌词",))
        self.owner._apply_visual_data(IslandVisualData(empty, (), ()))
        for _ in range(100):
            if self.window._display_lyrics() == (["暂无歌词"], []):
                break
            QTest.qWait(10)
        self.assertEqual(self.window._display_lyrics(), (["暂无歌词"], []))
        with patch.object(self.window, "_lyric_loading_holds_result", return_value=False):
            self.window._sync_no_lyrics_content_layout(100.)
        self.assertEqual(self.window.mode, "cover")
        self.assertEqual(self.window._content_layout, "cover_lyrics")
        with patch.object(self.window, "_paint_content_layout_images", wraps=self.window._paint_content_layout_images) as transition:
            self.render()
            transition.assert_called()
        self.assertEqual(self.window._lyric_hit_rects, {})
        self.owner.media_track_id = "next-pending"
        with patch.object(self.window, "_lyrics_lookup_pending", return_value=True):
            self.window._sync_no_lyrics_content_layout(101.)
        self.assertEqual(self.window.mode, "cover_lyrics")
        self.assertEqual(self.saved, original_settings)

    def test_narrow_lyric_header_is_the_cover_transition_source(self):
        from _demos.narrow3.layout import NarrowLayout
        self.window.set_content_layout("lyrics", animate=False)
        self.reveal()
        self.assertEqual(self.window._content_layout_cover_pose(), NarrowLayout(self.window.width(), self.window.height()).cover)
        with patch.object(self.window, "paint_header", wraps=self.window.paint_header) as header:
            snapshot = self.window._capture_content_layout()
        self.assertFalse(snapshot.isNull())
        header.assert_called()

    def test_wide_layout_transition_keeps_wide_chrome(self):
        self.window.toggle_wide(True)
        self.window._finish_width_chrome()
        self.assertFalse(self.window.narrow)
        self.window.set_content_layout("cover", animate=True)
        self.assertFalse(self.window._layout_snapshot.isNull())
        with patch.object(self.window, "_paint_shared_narrow_content", side_effect=AssertionError("narrow chrome in wide transition")):
            self.render()

    def test_visible_live_view_uses_one_clock_and_preserves_island_fps_setting(self):
        self.owner.music_fullscreen_overlay = self.window
        self.owner.animation_fps = self.owner.lyric_animation_fps = 165
        with patch.object(self.owner, "_display_refresh_rate_fps_cap", return_value=165):
            self.assertEqual(self.owner._timer_interval_ms(), 33)
            self.window.hide()
            self.window.show()
            self.assertTrue(self.window._timer.isActive())
            self.assertFalse(self.window.study_timer.isActive())
            with patch.object(self.window, "tick") as tick:
                self.window._on_animation_tick()
                tick.assert_called_once_with()
            self.window.hide()
            self.assertEqual(self.owner._timer_interval_ms(), 6)
        self.assertEqual(self.owner.animation_fps, 165)

    def test_hidden_chrome_does_not_rasterize_a_backdrop(self):
        self.window.visibility.progress = 0.
        self.window.visibility.opacity.target = 0.
        with patch.object(self.window, "_surface_glass_backdrop", side_effect=AssertionError("hidden backdrop")):
            self.window._refresh_chrome_backdrop(100.)


if __name__ == "__main__":
    unittest.main()
