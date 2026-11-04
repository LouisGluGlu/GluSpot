"""Hidden owner clocks retain media state without presenting island frames."""
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QWidget

from api.models import ColorToken, IslandVisualData, TimedLyricLine, TrackData
from api.providers import EmptyIslandDataProvider
from local.island_stack_visual import IslandStackVisual


class IslandHiddenTimerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.provider = EmptyIslandDataProvider()
        self.owner = IslandStackVisual(self.provider)
        self.owner.timer.stop()
        self.owner.location_mode = "free"
        self.page = QWidget()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.owner.music_fullscreen_overlay = None
        self.owner._music_library_windows = ()
        self.page.close()
        self.page.deleteLater()
        self.owner._application_quitting = True
        self.owner.timer.stop()
        self.owner.close()
        self.owner.deleteLater()
        self.app.processEvents()

    def test_hidden_owner_uses_low_rate_even_without_a_navigation_page(self):
        self.owner.last_media_refresh_at = 0.
        self.assertEqual(self.owner._timer_interval_ms(), 100)

    def test_library_and_music_page_keep_provider_and_lyric_state_without_frames(self):
        rows = tuple(TimedLyricLine(float(i * 10), f"Line {i}") for i in range(3))
        track = TrackData("New song", "GluSpot", tuple(x.text for x in rows),
                          ColorToken(10, 90, 130), ColorToken(40, 80, 130), (),
                          track_id="t234-hidden-clock", duration_seconds=100.,
                          position_seconds=15., position_sampled_at=time.monotonic(),
                          timed_lyrics=rows, lyrics_source="fixture")
        self.provider.visual_data = IslandVisualData(track, (), ())
        self.page.show()
        self.app.processEvents()
        for navigation in ("library", "music"):
            with self.subTest(navigation=navigation):
                self.owner._music_library_windows = (self.page,) if navigation == "library" else ()
                self.owner.music_fullscreen_overlay = self.page if navigation == "music" else None
                self.owner.last_media_refresh_at = 0.
                with patch.object(self.owner, "_handle_tick_frame_execution") as frame, \
                        patch.object(self.owner, "_update_wave_values") as wave, \
                        patch.object(self.owner, "_handle_tick_follow_refresh") as follow, \
                        patch.object(self.owner, "_advance_music_cover_hover") as hover, \
                        patch.object(self.owner, "_advance_island_music_control_hover") as controls, \
                        patch.object(self.owner, "_handle_tick_overlay_clock_and_topmost") as topmost, \
                        patch.object(self.owner, "_update_scene_state") as scene, \
                        patch.object(self.owner, "_sync_personal_dj_island", wraps=self.owner._sync_personal_dj_island) as dj:
                    self.owner._tick()
                self.assertEqual(self.owner.media_track_id, "t234-hidden-clock")
                self.assertEqual(self.owner.lyric_index, 1)
                self.assertAlmostEqual(self.owner._estimated_media_position(time.monotonic()), 15., delta=.2)
                scene.assert_called_once()
                dj.assert_called_once()
                for visual_work in (frame, wave, follow, hover, controls, topmost):
                    visual_work.assert_not_called()
                self.assertEqual(self.owner.timer.interval(), 100)
                self.owner.timer.stop()

    def test_hidden_clock_wakes_at_existing_provider_deadlines(self):
        self.owner.last_media_refresh_at = 10.
        self.owner.media_refresh_interval = .65
        self.owner._media_strong_tracking_until = 0.
        self.assertEqual(self.owner._timer_interval_ms(now=10.1), 100)
        self.assertIn(self.owner._timer_interval_ms(now=10.6), (50, 51))
        self.owner._media_strong_tracking_until = 11.
        self.assertIn(self.owner._timer_interval_ms(now=10.1), (20, 21))
        self.assertEqual(self.owner._timer_interval_ms(now=12.), 100)

    def test_show_immediately_restores_selected_frame_cadence(self):
        self.owner.animation_fps = self.owner.lyric_animation_fps = 60
        self.owner.last_media_refresh_at = 0.
        self.owner.timer.start(100)
        with patch.object(self.owner, "_center_on_screen"), \
                patch.object(self.owner, "_refresh_top_notch_position"), \
                patch.object(self.owner, "_force_super_topmost"):
            self.owner.show()
            self.assertEqual(self.owner.timer.interval(), 17)
            self.owner.hide()
            self.assertEqual(self.owner.timer.interval(), 100)
            self.owner.show()
            self.assertEqual(self.owner.timer.interval(), 17)
        self.assertTrue(self.owner.timer.isActive())

    def test_minimized_owner_uses_low_rate_until_visible_again(self):
        with patch.object(self.owner, "_center_on_screen"), \
                patch.object(self.owner, "_refresh_top_notch_position"), \
                patch.object(self.owner, "_force_super_topmost"):
            self.owner.showMinimized()
            self.assertEqual(self.owner._timer_interval_ms(), 100)
            self.owner.showNormal()
            self.assertEqual(self.owner._timer_interval_ms(), 17)

    def test_hidden_taskbar_follow_can_restore_the_island(self):
        self.owner.taskbar_hidden_by_follow = True
        with patch.object(self.owner, "_handle_tick_runtime_refresh", return_value=False), \
                patch.object(self.owner, "_handle_music_lyric_tick", return_value=False), \
                patch.object(self.owner, "_handle_tick_follow_refresh", side_effect=lambda _now: self.owner._set_taskbar_follow_hidden(False)) as follow, \
                patch.object(self.owner, "_center_on_screen"), \
                patch.object(self.owner, "_refresh_top_notch_position"), \
                patch.object(self.owner, "_force_super_topmost"), \
                patch.object(self.owner, "_handle_tick_frame_execution") as frame:
            self.owner._tick()
        follow.assert_called_once()
        frame.assert_not_called()
        self.assertTrue(self.owner.isVisible())
        self.assertEqual(self.owner.timer.interval(), 17)

    def test_navigation_or_tray_ownership_prevents_native_follow_reshow(self):
        self.owner.taskbar_hidden_by_follow = True
        self.page.show()
        for navigation in (True, False):
            with self.subTest(navigation=navigation):
                self.owner._music_library_windows = (self.page,) if navigation else ()
                self.owner._tray_hidden = not navigation
                with patch.object(self.owner, "_handle_tick_runtime_refresh", return_value=False), \
                        patch.object(self.owner, "_handle_music_lyric_tick", return_value=False), \
                        patch.object(self.owner, "_handle_tick_follow_refresh") as follow:
                    self.owner._tick()
                follow.assert_not_called()
                self.assertFalse(self.owner.isVisible())

    def test_visible_suppressed_bar_keeps_its_transition_frame_path(self):
        with patch.object(self.owner, "_center_on_screen"), \
                patch.object(self.owner, "_refresh_top_notch_position"), \
                patch.object(self.owner, "_force_super_topmost"):
            self.owner.show()
        self.owner.scene_suppressed = True
        self.owner._bar_scene_transition = True
        with patch.object(self.owner, "_handle_tick_runtime_refresh", return_value=False), \
                patch.object(self.owner, "_handle_music_lyric_tick", return_value=False), \
                patch.object(self.owner, "_handle_tick_follow_refresh"), \
                patch.object(self.owner, "_handle_tick_overlay_clock_and_topmost"), \
                patch.object(self.owner, "_handle_scene_suppressed_tick") as transition:
            self.owner._tick()
        transition.assert_called_once()
        self.assertEqual(self.owner._timer_interval_ms(), 17)
