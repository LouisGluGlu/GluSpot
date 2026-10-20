"""Frozen navigation pages stop invisible work and release ownership once."""
from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QAbstractAnimation, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import delete, isValid

from api.online_music import AccountSnapshot, OnlineSong
from local.music_library_window import LibraryCollection, MusicLibraryWindow
from local.music_navigation_transition import MusicNavigationTransition, capture_navigation_frame
from tests.test_music_library_background import _track


class _Page(QWidget):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.resumed = 0
        self.image = QImage(32, 32, QImage.Format.Format_RGB32)
        self.image.fill(QColor("#456789"))
        self.setGeometry(20, 30, 360, 420)
        self.show()

    def freeze_navigation_cover(self):
        return QRect(self.mapToGlobal(QPoint(20, 20)), self.image.size()), self.image, 0.

    def resume_navigation_cover(self):
        self.resumed += 1

    def navigation_background_frame(self, excluded_cover_rect=None):
        return self.image


class NavigationAnimationLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.source, self.target = _Page(), _Page()
        self.transition = MusicNavigationTransition()
        self.transition.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)

    def tearDown(self):
        self.transition.cancel()
        for widget in (self.transition, self.source, self.target):
            if isValid(widget):
                widget.close()
                widget.deleteLater()
        self.app.processEvents()

    def start(self):
        frame = capture_navigation_frame(self.source, separate_cover=True, freeze_cover=True)
        self.assertTrue(self.transition.start(frame, self.target))
        self.assertEqual(self.transition._animation.duration(), 560)
        return frame

    def assert_released(self):
        self.assertEqual(self.transition._animation.state(), QAbstractAnimation.State.Stopped)
        self.assertFalse(self.transition.isVisible())
        self.assertIsNone(self.transition._destination)
        self.assertIsNone(self.transition._origin_frame)
        self.assertIsNone(self.transition._target_frame)
        self.assertTrue(self.transition._origin.isNull())
        self.assertTrue(self.transition._target.isNull())
        self.assertTrue(self.transition._scene_canvas.isNull())

    def test_hiding_overlay_cancels_and_restores_target_exactly_once(self):
        self.target.setWindowOpacity(.72)
        self.target.setEnabled(False)
        opacity = self.target.windowOpacity()
        self.start()
        self.transition.hide()
        self.assert_released()
        self.assertAlmostEqual(self.target.windowOpacity(), opacity)
        self.assertFalse(self.target.isEnabled())
        self.assertEqual((self.source.resumed, self.target.resumed), (1, 1))
        self.transition.cancel()
        self.assertEqual((self.source.resumed, self.target.resumed), (1, 1))

    def test_closing_overlay_restores_enabled_target_and_stops_animation(self):
        self.start()
        self.transition.close()
        self.assert_released()
        self.assertTrue(self.target.isEnabled())
        self.assertEqual(self.target.windowOpacity(), 1.)
        self.assertEqual((self.source.resumed, self.target.resumed), (1, 1))

    def test_destroyed_destination_releases_surviving_source(self):
        self.start()
        delete(self.target)
        self.assert_released()
        self.assertEqual(self.source.resumed, 1)

    def test_previous_destination_destruction_cannot_cancel_a_replacement(self):
        self.start()
        previous = self.target
        self.target = _Page()
        self.start()
        self.assertEqual(previous.windowOpacity(), 1.)
        delete(previous)
        self.assertTrue(self.transition.isVisible())
        self.assertIs(self.transition._destination, self.target)
        self.transition.cancel()
        self.assertEqual(self.source.resumed, 2)
        self.assertEqual(self.target.resumed, 1)

    def test_plain_page_handoff_still_freezes_and_releases_its_target(self):
        frame = capture_navigation_frame(self.source)
        self.assertTrue(self.transition.start(frame, self.target))
        self.assertFalse(self.transition._cover_transition)
        self.assertEqual(self.transition._animation.duration(), 260)
        self.assertIsNotNone(self.transition._target_frame.resume)
        self.transition.cancel()
        self.assertEqual((self.source.resumed, self.target.resumed), (0, 1))


class LibraryNavigationClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MusicLibraryWindow()
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.window.set_account(AccountSnapshot("netease", True, "fixture", "测试账号"))
        self.window.set_background_context({"v2_music_flow_intensity": .74, "v2_animation_fps": 60}.get)
        with patch("local.music_library_window.time.monotonic", return_value=100.):
            self.window.show()
            self.window.set_now_playing(_track())
        self.window._header_motion.setCurrentTime(self.window.HEADER_ANIMATION_MS)
        self.window.set_loading(True)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_frozen_page_stops_all_background_cover_and_skeleton_clocks(self):
        timers = (self.window._background_timer, self.window._album_rotation_timer,
                  self.window._artwork_skeleton_timer)
        self.assertTrue(all(timer.isActive() for timer in timers))
        before = self.window._background_seconds
        with patch("local.music_library_window.time.monotonic", return_value=100.):
            self.window.freeze_navigation_cover()
        self.assertTrue(all(not timer.isActive() for timer in timers))
        with patch("local.music_library_window.time.monotonic", return_value=500.), \
                patch.object(self.window, "_update_background") as render:
            self.window._advance_background()
            self.window._pulse_artwork_skeleton()
            self.assertEqual(self.window._background_seconds, before)
            render.assert_not_called()
        with patch("local.music_library_window.time.monotonic", return_value=600.):
            self.window.resume_navigation_cover()
        self.assertTrue(all(timer.isActive() for timer in timers))
        with patch("local.music_library_window.time.monotonic", return_value=600.02):
            self.window._advance_background()
        self.assertLessEqual(self.window._background_seconds - before, .0200001)

    def test_failed_capture_restores_existing_library_clocks(self):
        with patch.object(self.window, "navigation_background_frame", side_effect=RuntimeError("capture unavailable")):
            self.assertIsNone(capture_navigation_frame(self.window, separate_cover=True, freeze_cover=True))
        self.assertFalse(self.window._navigation_cover_frozen)
        self.assertTrue(self.window._background_timer.isActive())
        self.assertTrue(self.window._album_rotation_timer.isActive())
        self.assertTrue(self.window._artwork_skeleton_timer.isActive())

    def test_plain_snapshot_freezes_library_without_separating_or_hiding_cover(self):
        frame = capture_navigation_frame(self.window, freeze_cover=True)
        self.assertIsNotNone(frame)
        self.assertFalse(frame.has_cover)
        self.assertTrue(self.window.now_playing_button.isVisible())
        self.assertTrue(self.window._navigation_cover_frozen)
        self.assertFalse(self.window._background_timer.isActive())
        self.assertFalse(self.window._artwork_skeleton_timer.isActive())
        frame.release()
        self.assertTrue(self.window._background_timer.isActive())
        self.assertTrue(self.window._artwork_skeleton_timer.isActive())

    def test_failed_plain_snapshot_restores_library_clocks(self):
        with patch.object(self.window, "grab", side_effect=RuntimeError("capture unavailable")):
            self.assertIsNone(capture_navigation_frame(self.window, freeze_cover=True))
        self.assertFalse(self.window._navigation_cover_frozen)
        self.assertTrue(self.window._background_timer.isActive())
        self.assertTrue(self.window._artwork_skeleton_timer.isActive())

    def test_song_change_during_freeze_keeps_existing_color_transition_renderer(self):
        renderer = self.window._background_renderer
        self.window.freeze_navigation_cover()
        self.window.set_now_playing(_track("next", primary=(40, 190, 70), secondary=(160, 50, 190)))
        self.assertIs(self.window._background_renderer, renderer)
        self.assertFalse(self.window._background_timer.isActive())
        self.assertNotEqual(renderer._painted_colors, renderer._target_colors)
        self.window.resume_navigation_cover()
        self.assertTrue(self.window._background_timer.isActive())
        self.assertIs(self.window._background_renderer, renderer)

    def test_releasing_hidden_or_paused_frame_does_not_start_invisible_clocks(self):
        frame = capture_navigation_frame(self.window, separate_cover=True, freeze_cover=True)
        self.window.hide()
        frame.release()
        self.assertFalse(self.window._background_timer.isActive())
        self.assertFalse(self.window._album_rotation_timer.isActive())
        self.assertFalse(self.window._artwork_skeleton_timer.isActive())
        self.window.set_now_playing(replace(_track(), is_playing=False))
        self.window.show()
        self.window.freeze_navigation_cover()
        self.window.resume_navigation_cover()
        self.assertFalse(self.window._background_timer.isActive())
        self.assertFalse(self.window._album_rotation_timer.isActive())
        self.assertTrue(self.window._artwork_skeleton_timer.isActive())

    def test_current_cover_iteration_avoids_a_full_qobject_tree_scan(self):
        self.window.set_loading(False)
        song = OnlineSong("netease", "1", "测试歌曲", "测试歌手")
        item = LibraryCollection("song-1", "song", song.title, "https://example.invalid/cover.jpg", song=song)
        self.window.search.setText("测试")
        self.window._search_timer.stop()
        self.window.set_search_results((item,))
        cover = self.window._rows[0].cover
        with patch.object(self.window, "findChildren", side_effect=AssertionError("full QObject traversal")):
            self.assertEqual(tuple(self.window._collection_covers()), (cover,))
            self.assertIn(cover, self.window._missing_artwork_widgets())
            image = self.window.navigation_background_frame()
        self.assertFalse(image.isNull())


if __name__ == "__main__":
    unittest.main()
