"""t257: the live window's island control preserves its originating layout."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from api.providers import EmptyIslandDataProvider
from local.island_stack_visual import IslandStackVisual
from _demos.narrow3.narrow_window import NarrowStudyWindow


class MusicIslandRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = IslandStackVisual(data_provider=EmptyIslandDataProvider())
        self.owner.timer.stop()
        self.owner.settings_getter = lambda _key, default=None: default
        self.owner._music_login_required = lambda: False
        self.window = NarrowStudyWindow(self.owner, "B", "left", live=True)
        self.owner.music_fullscreen_overlay = self.window
        self.work_area = patch.object(
            self.window, "_available_geometry", return_value=QRect(0, 0, 1920, 1080),
        )
        self.work_area.start()
        self.addCleanup(self.work_area.stop)
        self.owner.show()
        self.app.processEvents()
        self.stop_timers()

    def tearDown(self):
        self.owner._application_quitting = True
        self.window.shutdown()
        self.owner.close()
        self.app.processEvents()

    def stop_timers(self):
        self.owner.timer.stop()
        self.window._timer.stop()
        self.window.study_timer.stop()
        self.window._audio_output_status_timer.stop()

    def finish_handoff(self):
        target = self.window._mode_animation.endValue()
        self.window._mode_animation.stop()
        if isinstance(target, QRect) and target.isValid():
            self.window.setGeometry(target)
        self.window._finish_mode_transition()
        self.app.processEvents()
        self.stop_timers()

    def open_window(self, geometry):
        self.window._normal_geometry = QRect(geometry)
        self.owner._show_music_fullscreen(fullscreen=False)
        self.finish_handoff()
        self.assertEqual(self.window.geometry(), geometry)
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.owner.isVisible())
        self.assertTrue(self.owner._music_surface_active)

    def click_island_control(self):
        self.window.visibility.progress = 1.0
        opacity = self.window.visibility.opacity
        opacity.start = opacity.target = 1.0
        opacity.duration = 0.0
        self.window._sync_window_control_rects()
        if self.window.narrow:
            point = next(group["return"].center() for group in self.window.glass_groups()
                         if "return" in group)
        else:
            point = self.window._button_rects["return"].center()
        with patch.object(self.window, "_control_reveal_at", return_value=1.0):
            QTest.mouseClick(self.window, Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.NoModifier, point.toPoint())

    def assert_island_restored(self):
        self.assertTrue(self.owner.isVisible())
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.owner._music_surface_active)

    def assert_window_round_trip(self, geometry, *, narrow):
        self.open_window(geometry)
        self.assertEqual(self.window.narrow, narrow)
        for _round_trip in range(2):
            with patch.object(self.window, "toggle_wide", wraps=self.window.toggle_wide) as toggle:
                self.click_island_control()
            toggle.assert_not_called()
            self.assertTrue(self.window._island_returning)
            self.assertEqual(self.window._normal_geometry, geometry)
            self.assertTrue(self.owner._music_windowed_preference)
            self.finish_handoff()
            self.assert_island_restored()

            self.owner._show_music_fullscreen()
            self.finish_handoff()
            self.assertFalse(self.window._fullscreen)
            self.assertEqual(self.window.geometry(), geometry)
            self.assertEqual(self.window.narrow, narrow)
            self.assertTrue(self.window.isVisible())
            self.assertFalse(self.owner.isVisible())
            self.assertTrue(self.owner._music_surface_active)

    def test_narrow_island_button_restores_default_narrow_geometry(self):
        self.assert_window_round_trip(QRect(80, 80, 360, 640), narrow=True)

    def test_narrow_island_button_restores_manually_resized_narrow_geometry(self):
        self.assert_window_round_trip(QRect(123, 91, 483, 560), narrow=True)

    def test_wide_island_button_restores_wide_geometry(self):
        self.assert_window_round_trip(QRect(120, 90, 960, 540), narrow=False)

    def assert_resized_shared_geometry_round_trip(self, initial, resized):
        # MusicLibraryController._publish_shared_music_geometry publishes the
        # library's last normal rectangle before entering playback. The player
        # can then be resized without revisiting that controller or library.
        self.owner._music_page_geometry = QRect(initial)
        self.open_window(initial)
        self.window.setGeometry(resized)
        self.window._finish_width_chrome()
        self.assertEqual(self.window.geometry(), resized)
        self.window.return_to_island()
        self.assertTrue(self.window._island_returning)
        self.assertEqual(self.window._normal_geometry, resized)
        self.finish_handoff()
        self.assert_island_restored()
        self.owner._show_music_fullscreen()
        self.finish_handoff()
        self.assertEqual(self.window.geometry(), resized)
        self.assertEqual(self.window.narrow, resized.width() < 500)

    def test_library_wide_geometry_cannot_replace_last_narrow_player_on_restore(self):
        self.assert_resized_shared_geometry_round_trip(
            QRect(120, 90, 960, 540), QRect(123, 91, 483, 560),
        )

    def test_library_narrow_geometry_cannot_replace_last_wide_player_on_restore(self):
        self.assert_resized_shared_geometry_round_trip(
            QRect(80, 80, 360, 640), QRect(120, 90, 960, 540),
        )

    def test_fullscreen_return_retains_resized_normal_geometry_after_library(self):
        initial = QRect(120, 90, 960, 540)
        normal = QRect(123, 91, 483, 560)
        self.owner._music_page_geometry = QRect(initial)
        self.open_window(initial)
        self.window.setGeometry(normal)
        self.window._finish_width_chrome()
        self.window.toggle_presentation_mode()
        self.finish_handoff()
        self.assertTrue(self.window._fullscreen)
        self.assertEqual(self.window._normal_geometry, normal)
        self.window.return_to_island()
        self.finish_handoff()
        self.assert_island_restored()
        self.owner._show_music_fullscreen()
        self.finish_handoff()
        self.assertTrue(self.window._fullscreen)
        self.assertEqual(self.window._normal_geometry, normal)
        self.window.toggle_presentation_mode()
        self.finish_handoff()
        self.assertEqual(self.window.geometry(), normal)
        self.assertTrue(self.window.narrow)

    def test_fullscreen_escape_returns_to_island_and_reopens_fullscreen(self):
        normal = QRect(80, 80, 360, 640)
        self.open_window(normal)
        self.window.toggle_presentation_mode()
        self.finish_handoff()
        self.assertTrue(self.window._fullscreen)
        self.assertFalse(self.owner._music_windowed_preference)

        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.assertTrue(self.window._island_returning)
        self.assertEqual(self.window._normal_geometry, normal)
        self.finish_handoff()
        self.assert_island_restored()
        self.owner._show_music_fullscreen()
        self.finish_handoff()
        self.assertTrue(self.window._fullscreen)
        self.assertEqual(self.window._normal_geometry, normal)
        self.assertFalse(self.owner._music_windowed_preference)
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.owner.isVisible())

        self.window.toggle_presentation_mode()
        self.finish_handoff()
        self.assertFalse(self.window._fullscreen)
        self.assertEqual(self.window.geometry(), normal)
        self.assertTrue(self.window.narrow)


if __name__ == "__main__":
    unittest.main()
