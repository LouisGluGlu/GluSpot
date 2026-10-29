"""t137 PC back probe: fixed top-left clicks must never widen the music view."""
import itertools
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from api.providers import EmptyIslandDataProvider
from local.island_stack_visual import IslandStackVisual
from _demos.narrow3.layout import NarrowLayout
from _demos.narrow3.narrow_window import NarrowStudyWindow


class NarrowBackPcRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = IslandStackVisual(data_provider=EmptyIslandDataProvider())
        self.owner.timer.stop()
        self.settings = {"v2_owned_playback_enabled": True}
        self.owner.settings_getter = lambda key, default=None: self.settings.get(key, default)
        self.owner._music_library_back_callback = Mock(name="library")
        self.owner._music_login_callback = Mock(name="login")
        self.owner._music_login_required = lambda: False
        self.window = NarrowStudyWindow(self.owner, "B", "left", live=True)
        self.window.hints = False
        self.window.resize(483, 560)
        self.window.show()
        self.app.processEvents()
        self.window._timer.stop()
        self.window.study_timer.stop()
        self.window._audio_output_status_timer.stop()
        self.window.visibility.progress = 1.0
        fade = self.window.visibility.opacity
        fade.start = fade.target = 1.0
        fade.duration = 0

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.owner._application_quitting = True
        self.owner.close()
        self.app.processEvents()

    def top_left_probe(self):
        half = NarrowLayout(self.window.width(), self.window.height()).button_size / 2
        return QPointF(16 + half, 16 + half)

    def click(self, point):
        QTest.mouseClick(self.window, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, point.toPoint())

    def test_fixed_top_left_routes_to_library_or_login_without_resizing(self):
        for login_required in (False, True):
            with self.subTest(login_required=login_required):
                self.owner._music_login_required = lambda: login_required
                self.owner._music_library_back_callback.reset_mock()
                self.owner._music_login_callback.reset_mock()
                point = self.top_left_probe()
                before = QRect(self.window.geometry())
                self.assertTrue(self.window._library_back_available())
                self.assertEqual(self.window._live_action_at(point), "library")
                with patch.object(self.window, "toggle_wide") as toggle, \
                     patch.object(self.window, "_seek_to_lyric") as seek:
                    self.click(point)
                    toggle.assert_not_called()
                    seek.assert_not_called()
                self.assertEqual(self.window.geometry(), before)
                self.assertTrue(self.window.narrow)
                self.assertEqual(self.owner._music_library_back_callback.call_count,
                                 int(not login_required))
                self.assertEqual(self.owner._music_login_callback.call_count,
                                 int(login_required))

    def test_disabled_owned_playback_leaves_top_left_empty_without_widening(self):
        self.settings["v2_owned_playback_enabled"] = False
        point = self.top_left_probe()
        before = QRect(self.window.geometry())
        self.assertFalse(self.window._library_back_available())
        self.assertNotEqual(self.window._live_action_at(point), "return")
        self.assertNotEqual(self.window._live_action_at(point), "favorite")
        with patch.object(self.window, "toggle_wide") as toggle, \
             patch.object(self.window, "_activate_song_collection"), \
             patch.object(self.window, "_seek_to_lyric"):
            self.click(point)
            toggle.assert_not_called()
        self.assertEqual(self.window.geometry(), before)
        self.assertTrue(self.window.narrow)
        self.owner._music_library_back_callback.assert_not_called()
        self.owner._music_login_callback.assert_not_called()

    def test_window_mode_stays_right_for_every_back_and_dj_gate(self):
        for width, owned, dj in itertools.product((320, 330, 360, 483, 959),
                                                 (False, True), (False, True)):
            with self.subTest(width=width, owned=owned, dj=dj):
                self.settings["v2_owned_playback_enabled"] = owned
                self.window.resize(width, 640)
                self.window.dj_available = dj
                rects = NarrowLayout(width, 640, library_available=owned,
                                     dj_available=dj)._top_rects
                self.assertGreater(rects["return"].left(), width / 2)
                self.assertGreaterEqual(rects["return"].left(), rects["translation"].right())
                self.assertIn("translation", rects)
                self.assertFalse(rects["return"].contains(self.top_left_probe()))
                if not owned:
                    reference = NarrowLayout(width, 640, library_available=True, dj_available=dj)
                    self.assertEqual(rects["collection"], reference.favorite)
                    self.assertNotEqual(self.window._live_action_at(self.top_left_probe()), "favorite")

    def test_empty_top_left_with_hidden_controls_never_toggles_window_mode(self):
        self.settings["v2_owned_playback_enabled"] = False
        self.window.visibility.progress = 0.
        before = QRect(self.window.geometry())
        with patch.object(self.window, "toggle_wide") as toggle, \
             patch.object(self.window, "_seek_to_lyric"):
            self.click(self.top_left_probe())
            toggle.assert_not_called()
        self.assertEqual(self.window.geometry(), before)
        self.assertTrue(self.window.narrow)

    def test_library_hit_has_priority_over_overlapping_top_button(self):
        back = NarrowLayout(self.window.width(), self.window.height()).back
        overlapping = [{"return": QRectF(back)}, {"library": QRectF(back)}, {}]
        with patch.object(self.window, "glass_groups", return_value=overlapping):
            self.assertEqual(self.window._live_action_at(self.top_left_probe()), "library")
            with patch.object(self.window, "toggle_wide") as toggle:
                self.click(self.top_left_probe())
                toggle.assert_not_called()
        self.owner._music_library_back_callback.assert_called_once()
        self.assertEqual((self.window.width(), self.window.height()), (483, 560))

    def test_explicit_return_button_keeps_window_mode_action(self):
        for owned in (False, True):
            with self.subTest(owned=owned):
                self.window.toggle_wide(False)
                self.window.resize(483, 560)
                self.settings["v2_owned_playback_enabled"] = owned
                point = next(group["return"].center()
                             for group in self.window.glass_groups() if "return" in group)
                self.assertGreater(point.x(), self.window.width() / 2)
                self.assertEqual(self.window._live_action_at(point), "return")
                with patch.object(self.window, "toggle_wide", wraps=self.window.toggle_wide) as toggle:
                    self.click(point)
                    toggle.assert_called_once_with()
                self.assertEqual((self.window.width(), self.window.height()), (960, 540))
                self.assertFalse(self.window.narrow)
        self.owner._music_library_back_callback.assert_not_called()
        self.owner._music_login_callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
