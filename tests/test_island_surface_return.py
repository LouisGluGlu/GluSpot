"""A completed page-to-island morph must not replay hidden shell springs."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF
from PySide6.QtGui import QCloseEvent, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.providers import EmptyIslandDataProvider, empty_visual_data
from local.island_stack_visual import IslandStackVisual, SpringRect
from local.music_lyric_motion import LogicalLineSpringChain


class IslandSurfaceReturnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.island = IslandStackVisual(EmptyIslandDataProvider(empty_visual_data()))
        self.island.timer.stop()
        self.island._force_super_topmost = lambda: None
        self.island._center_on_screen = lambda: None
        self.island._refresh_top_notch_position = lambda: False
        self.island._effective_lyric_offset_seconds = lambda: 0.0
        self.island.music_playing = True
        self.island.music_auto_detect = False
        self.island.media_is_playing = False
        self.island.media_track_id = "return-song"
        self.island.track_title = "Return song"
        self.island.artist = "GluSpot fixture"
        self.island.media_position_seconds = 5.0
        self.island.media_position_observed_seconds = 5.0
        self.island.media_position_sampled_at = 100.0
        self.island.media_position_sync_at = 100.0
        self.island.media_duration_seconds = 60.0
        self.island.media_position_source = "local-playback"
        self.island.lyrics = ["A previous row", "ÀÉgj 映照着世界", "Following row"]
        self.island.timed_lyrics = [(0.0, self.island.lyrics[0]), (4.0, self.island.lyrics[1]),
                                    (30.0, self.island.lyrics[2])]
        self.island.timed_lyric_units = [(), (), ()]
        self.island.timed_lyric_timing_kinds = ["line"] * 3
        self.island.lyric_index = self.island.prev_lyric_index = 1
        self.island.location_mode = "free"
        self.island.finder_mode = False
        self.island.expanded = False
        self.island.scene_suppressed = False
        self.island.scene_transition_mode = ""
        self.island.music_ambience_style = "pure_black"
        self.island._music_surface_active = True
        self.island._island_visible_before_music_surface = True
        self.island.music_fullscreen_overlay = SimpleNamespace(hide=Mock(), isVisible=lambda: False)

    def tearDown(self):
        self.island._keep_minimum_surface = False
        self.island.music_fullscreen_overlay = None
        self.island.close()
        self.island.deleteLater()
        self.app.processEvents()

    def _main(self):
        return next(visual for visual in self.island._build_visuals() if visual.z_index == 10)

    def _seed_hidden_motion(self):
        target = self._main()
        old = QRectF(target.rect)
        # A page can open from the expanded island, then return after its
        # hidden owner has collapsed. Its last painted shell is still tall.
        old.setHeight(target.rect.height() * 3.0)
        old.setWidth(target.rect.width() * .65)
        motion = SpringRect(old)
        motion.opacity = .4
        motion.vx, motion.vy, motion.vw, motion.vh = 120.0, -90.0, 220.0, 0.0
        self.island.motions[target.key] = motion
        return target.key

    def _restore(self):
        with patch("local.island_stack_visual.time.monotonic", return_value=100.0), \
                patch.object(self.island, "show"), patch.object(self.island, "raise_"), \
                patch.object(self.island, "activateWindow"):
            self.island._restore_island_from_music_surface()

    def test_returned_compact_ink_keeps_one_font_size_through_subsequent_frames(self):
        for style in ("wave_stream", "gelv_songscatter"):
            for placement in ("free", "top", "finder", "tasker"):
                with self.subTest(style=style, placement=placement):
                    self.island.lyric_flow_style = style
                    self.island.location_mode = placement
                    self.island.finder_mode = placement == "finder"
                    key = self._seed_hidden_motion()
                    self._restore()
                    target = self._main()
                    motion = self.island.motions[key]
                    sizes = []
                    image = QImage(1200, 400, QImage.Format.Format_ARGB32_Premultiplied)
                    with patch.object(self.island, "_paint_live_lyric_text",
                                      side_effect=lambda *args, **kwargs: sizes.append(args[3])):
                        with QPainter(image) as painter:
                            for frame in range(180):
                                now = 100.0 + frame / 120.0
                                motion.step(target.rect, 1.0, 1.0 / 120.0)
                                with patch("local.island_stack_visual.time.monotonic", return_value=now):
                                    self.island._paint_music_compact_base(painter, motion.rect)
                    self.assertEqual(len(sizes), 180)
                    self.assertEqual(len(set(sizes)), 1, sizes)
                    self.assertEqual(motion.rect, target.rect)
                    self.assertEqual((motion.vx, motion.vy, motion.vw, motion.vh), (0.0, 0.0, 0.0, 0.0))

    def test_hidden_bilingual_row_is_measured_before_destination_is_settled(self):
        self.island.enable_dual_line_lyrics = True
        self.island.lyrics[1] += "\n新的译文"
        self.island.timed_lyrics[1] = (4.0, self.island.lyrics[1])
        self.island.lyric_index = self.island.prev_lyric_index = 0
        key = self._seed_hidden_motion()
        self._restore()
        self.assertEqual(self.island.lyric_index, 1)
        target = self._main()
        self.assertGreater(target.rect.height(), self.island._u(70.0))
        self.assertEqual(self.island.motions[key].rect, target.rect)

    def test_return_discards_stale_hover_without_restarting_reading_or_row_motion(self):
        self.island.hovered_key = self.island.hover_armed_key = "music"
        self.island.hover_started_at = 99.9
        self.island.hover_bounce_until = self.island.stack_bounce_until = 101.0
        self.island.left_stack_at = 99.9
        self.island.pointer_pos = QPointF(300.0, 50.0)
        phase_before = self.island._lyric_phase(100.0)
        source_clock = self.island._island_lyric_source_clock
        rows = LogicalLineSpringChain()
        rows.frame("same-song", (0.0, 30.0, 60.0), 1, 100.0)
        self.island._wave_line_spring = rows
        row_pose = (tuple(rows.positions), tuple(rows.velocities))
        self._seed_hidden_motion()
        self._restore()
        self.assertEqual(self.island.hovered_key, "")
        self.assertEqual(self.island.hover_armed_key, "")
        self.assertEqual((self.island.hover_started_at, self.island.hover_bounce_until,
                          self.island.stack_bounce_until, self.island.left_stack_at), (0.0,) * 4)
        self.assertEqual(self.island.pointer_pos, QPointF(-1.0, -1.0))
        self.assertIs(self.island._island_lyric_source_clock, source_clock)
        self.assertIs(self.island._wave_line_spring, rows)
        self.assertEqual((tuple(rows.positions), tuple(rows.velocities)), row_pose)
        self.assertEqual(self.island._lyric_phase(100.0)["progress"], phase_before["progress"])
        self.assertEqual(self.island.media_position_seconds, 5.0)
        self.assertFalse(self.island.media_is_playing)

    def test_return_does_not_settle_or_show_a_tray_hidden_island(self):
        self.island._tray_hidden = True
        with patch.object(self.island, "_settle_island_return_motion") as settle, \
                patch.object(self.island, "show") as show:
            self.island._restore_island_from_music_surface()
        settle.assert_not_called()
        show.assert_not_called()

    def test_return_restores_minimum_even_when_island_was_hidden_before_music_page(self):
        self.island._island_visible_before_music_surface = False
        with patch.object(self.island, "_settle_island_return_motion") as settle, \
                patch.object(self.island, "show") as show:
            self.island._restore_island_from_music_surface()
        settle.assert_called_once()
        show.assert_called_once()

    def test_managed_close_keeps_minimum_island_without_retiring_it(self):
        self.island._keep_minimum_surface = True
        event = QCloseEvent()
        with patch.object(self.island, "ensure_minimum_surface") as restore, \
                patch.object(self.island, "_close_solid_album_palette_worker") as shutdown:
            self.island.closeEvent(event)
        self.assertFalse(event.isAccepted())
        restore.assert_called_once()
        shutdown.assert_not_called()

    def test_minimized_page_does_not_block_minimum_or_topmost_but_keeps_navigation_ownership(self):
        page = SimpleNamespace(isVisible=lambda: True, isMinimized=lambda: True)
        self.island.music_fullscreen_overlay = page
        self.island._music_library_active = True
        self.island._music_library_windows = (page,)
        with patch.object(self.island, "show") as show, patch.object(self.island, "raise_"):
            self.island.ensure_minimum_surface()
        show.assert_called_once()
        self.assertTrue(self.island._music_surface_active)
        self.assertTrue(self.island._music_library_active)
        page.isMinimized = lambda: False
        with patch.object(self.island, "show") as show, patch.object(self.island, "hide") as hide:
            self.island._sync_minimum_surface()
        hide.assert_called_once()
        show.assert_not_called()

    def test_hidden_and_quitting_flags_prevent_minimum_resurrection(self):
        for flag in ("_tray_hidden", "_application_quitting"):
            with self.subTest(flag=flag):
                setattr(self.island, flag, True)
                with patch.object(self.island, "show") as show, patch.object(self.island, "raise_"):
                    self.island.ensure_minimum_surface()
                    self.island._sync_minimum_surface()
                    self.island._restore_island_from_music_surface()
                show.assert_not_called()
                setattr(self.island, flag, False)

    def test_system_offset_never_reanchors_owned_playback_with_default_system_adapter(self):
        self.island.media_track_id = "owned:netease:offset-song"
        self.island.media_lyric_offset_adapter = "system"
        self.island.media_app_lyric_offset_seconds = 0.0
        with patch.object(self.island, "_reanchor_lyric_offset") as reanchor:
            self.assertFalse(self.island.set_active_music_app_lyric_offset("system", 2.5))
        self.assertEqual(self.island.media_app_lyric_offset_seconds, 0.0)
        reanchor.assert_not_called()
        self.island.media_track_id = "external-system-song"
        with patch.object(self.island, "_reanchor_lyric_offset") as reanchor:
            self.assertTrue(self.island.set_active_music_app_lyric_offset("system", 2.5))
        self.assertEqual(self.island.media_app_lyric_offset_seconds, 2.5)
        reanchor.assert_called_once()

    def test_player_minimize_restore_events_resync_minimum_after_qt_state_settles(self):
        from local.music_surface_window import MusicSurfaceWindow
        page = MusicSurfaceWindow(self.island)
        self.island.music_fullscreen_overlay = page
        try:
            with patch.object(self.island, "_sync_minimum_surface") as sync:
                page.show()
                self.app.processEvents()
                sync.reset_mock()
                page.showMinimized()
                self.app.processEvents()
                self.assertTrue(page.isMinimized())
                sync.assert_called()
                self.assertFalse(self.island._navigation_surface_blocks_island())
                sync.reset_mock()
                page.showNormal()
                self.app.processEvents()
                self.assertFalse(page.isMinimized())
                sync.assert_called()
                self.assertTrue(self.island._navigation_surface_blocks_island())
                self.assertTrue(self.island._music_surface_active)
        finally:
            page.shutdown()
            page.close()
            page.deleteLater()
            self.island.music_fullscreen_overlay = None

    def test_own_song_page_never_starts_external_fullscreen_scene_transition(self):
        self.island.music_fullscreen_overlay = SimpleNamespace(isVisible=lambda: True, winId=lambda: 222)
        user32 = SimpleNamespace(GetForegroundWindow=lambda: 222, GetWindowRect=Mock())
        with patch("local.island_stack_visual.sys.platform", "win32"), \
                patch("local.island_stack_visual.ctypes.windll", SimpleNamespace(user32=user32)), \
                patch.object(self.island, "winId", return_value=111):
            self.assertEqual(self.island._detect_scene_suppression(), (False, ""))
        user32.GetWindowRect.assert_not_called()

    def test_unrelated_fullscreen_app_retains_scene_suppression(self):
        def window_rect(_hwnd, output):
            output._obj.left, output._obj.top = 0, 0
            output._obj.right, output._obj.bottom = 1280, 720
            return True

        self.island.music_fullscreen_overlay = SimpleNamespace(isVisible=lambda: True, winId=lambda: 222)
        user32 = SimpleNamespace(GetForegroundWindow=lambda: 333, GetWindowRect=window_rect)
        screen = SimpleNamespace(geometry=lambda: QRect(0, 0, 1280, 720))
        with patch("local.island_stack_visual.sys.platform", "win32"), \
                patch("local.island_stack_visual.ctypes.windll", SimpleNamespace(user32=user32)), \
                patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=screen), \
                patch.object(self.island, "winId", return_value=111), \
                patch.object(self.island, "_window_title", return_value="External fullscreen"), \
                patch.object(self.island, "_window_class", return_value="ExternalWindow"), \
                patch.object(self.island, "_window_process_name", return_value="external.exe"):
            self.assertEqual(self.island._detect_scene_suppression(), (True, "fullscreen"))


if __name__ == "__main__":
    unittest.main()
