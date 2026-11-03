"""Back navigation preserves geometry and window mode owns narrow/wide sizing."""
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from api.providers import EmptyIslandDataProvider
from local.island_stack_visual import IslandStackVisual
from local.music_surface_window import MusicSurfaceWindow, MUSIC_WINDOW_ASPECT_RATIO, constrained_window_size
from _demos.narrow3.narrow_window import NarrowStudyWindow


class NarrowBackResizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.provider = EmptyIslandDataProvider()
        self.owner = IslandStackVisual(data_provider=self.provider)
        self.owner.timer.stop()
        self.settings = {"v2_owned_playback_enabled": True}
        self.owner.settings_getter = lambda key, default=None: self.settings.get(key, default)
        self.owner._music_library_back_callback = Mock()
        self.owner._music_login_required = lambda: False
        self.window = NarrowStudyWindow(self.owner, 'B', 'left', live=True)
        self.window._timer.stop()
        self.window.study_timer.stop()
        self.window._audio_output_status_timer.stop()
        self.window.hints = False

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.owner._application_quitting = True
        self.owner.close()
        self.app.processEvents()

    def show(self):
        self.window.show()
        self.app.processEvents()
        self.window._timer.stop()
        self.window.study_timer.stop()
        self.window._audio_output_status_timer.stop()

    def click_narrow(self, key):
        self.window.visibility.progress = 1.0
        fade = self.window.visibility.opacity
        fade.start = fade.target = 1.0
        fade.duration = 0
        point = next(group[key].center() for group in self.window.glass_groups() if key in group)
        self.window._press_at(point)
        if self.window.live:
            self.window._finish_live_press(point)

    def test_library_back_at_483x560_preserves_geometry_and_never_seeks(self):
        self.show()
        callback = self.owner._music_library_back_callback = Mock()
        with patch.object(self.window, '_seek_to_lyric') as seek:
            for live in (True, False):
                self.window.live = live
                for available in (False, True):
                    with self.subTest(live=live, available=available):
                        self.settings["v2_owned_playback_enabled"] = available
                        callback.reset_mock()
                        self.window.resize(483, 560)
                        before = QRect(self.window.geometry())
                        if available:
                            self.click_narrow('library')
                        else:
                            self.assertFalse(self.window._library_back_available())
                            self.assertTrue(all('library' not in group for group in self.window.glass_groups()))
                        self.assertEqual(self.window.geometry(), before)
                        self.assertTrue(self.window.narrow)
                        self.assertEqual(callback.call_count, int(available))
            seek.assert_not_called()

    def test_keys_and_explicit_targets_are_exact_and_idempotent_even_hidden(self):
        for shown in (False, True):
            if shown:
                self.show()
            for wide, key, target in ((True, Qt.Key.Key_W, (960, 540)),
                                      (False, Qt.Key.Key_N, (360, 640))):
                with self.subTest(shown=shown, wide=wide):
                    QTest.keyClick(self.window, key)
                    self.assertEqual((self.window.width(), self.window.height()), target)
                    self.assertEqual(self.window.narrow, not wide)
                    with patch.object(self.window, 'resize', wraps=self.window.resize) as resize:
                        self.window.toggle_wide(wide)
                        QTest.keyClick(self.window, key)
                        resize.assert_not_called()

    def test_manual_resize_uses_shared_content_measure_for_narrow_flag(self):
        self.show()
        self.window.toggle_wide(True)
        for width, height in ((483, 560), (959, 540), (960, 900), (1000, 900)):
            with self.subTest(width=width, height=height):
                self.window.resize(width, height)
                animation = self.window._width_chrome_animation
                animation.setCurrentTime(animation.duration())
                animation.setCurrentTime(animation.duration())
                self.assertEqual(self.window.narrow, self.window._width_breakpoint_narrow())
        self.window.resize(959, 540)
        self.window._width_chrome_animation.setCurrentTime(self.window._width_chrome_animation.duration())
        self.assertFalse(self.window.narrow)
        self.window.resize(960, 900)
        self.assertTrue(self.window.narrow)

    def test_narrow_size_toggles_and_live_wide_return_preserves_island_lifecycle(self):
        self.show()
        with patch.object(self.window, '_seek_to_lyric') as seek:
            for live in (True, False):
                with self.subTest(live=live):
                    self.window.live = live
                    self.window.toggle_wide(False)
                    self.window.resize(483, 560)
                    with patch.object(self.window, 'toggle_wide', wraps=self.window.toggle_wide) as toggle:
                        self.click_narrow('return')
                        toggle.assert_called_once_with()
                    self.assertEqual((self.window.width(), self.window.height()), (960, 540))
                    self.assertFalse(self.window.narrow)
                    self.window._sync_window_control_rects()
                    point = self.window._button_rects['return'].center().toPoint()
                    with patch.object(self.window, '_control_reveal_at', return_value=1.0), \
                         patch.object(self.window, 'toggle_wide', wraps=self.window.toggle_wide) as toggle, \
                         patch.object(MusicSurfaceWindow, 'return_to_island') as restore:
                        QTest.mouseClick(self.window, Qt.MouseButton.LeftButton,
                                         Qt.KeyboardModifier.NoModifier, point)
                        if live:
                            restore.assert_called_once_with()
                            toggle.assert_not_called()
                        else:
                            restore.assert_not_called()
                            toggle.assert_called_once_with()
                    self.assertEqual((self.window.width(), self.window.height()),
                                     (960, 540) if live else (360, 640))
                    self.assertEqual(self.window.narrow, not live)
            seek.assert_not_called()

    def drag(self, point, delta):
        origin = self.window.mapToGlobal(point.toPoint())
        for kind, global_point, buttons in (
            (QEvent.Type.MouseButtonPress, origin, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseMove, origin + delta, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseButtonRelease, origin + delta, Qt.MouseButton.NoButton),
        ):
            local = QPointF(self.window.mapFromGlobal(global_point))
            button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
            event = QMouseEvent(kind, local, QPointF(global_point), button, buttons,
                                Qt.KeyboardModifier.NoModifier)
            QCoreApplication.sendEvent(self.window, event)

    def test_move_and_resize_still_work_after_library_and_window_mode_back(self):
        self.show()
        with patch.object(self.window, '_available_geometry', return_value=QRect(0, 0, 1920, 1080)), \
             patch.object(self.window, '_seek_to_lyric') as seek:
            for action in ('library', 'return'):
                with self.subTest(action=action):
                    self.window.toggle_wide(False)
                    self.window.resize(483, 560)
                    self.click_narrow(action)
                    before = QRect(self.window.geometry())
                    self.drag(QPointF(self.window.width() / 2, 12), QPoint(23, 19))
                    self.assertEqual(self.window.geometry(), before.translated(23, 19))
                    before = QRect(self.window.geometry())
                    self.drag(QPointF(self.window.width() - 2, self.window.height() / 2), QPoint(41, 0))
                    self.assertEqual(self.window.width(), before.width() + 41)
                    expected_height = before.height()
                    if (before.width() + 41) > round(before.height() * MUSIC_WINDOW_ASPECT_RATIO):
                        expected_height = constrained_window_size(
                            before.width() + 41, before.height(), self.window.minimumSize(),
                            self.window._available_geometry().size()).height()
                    self.assertEqual(self.window.height(), expected_height)
                    self.assertEqual(self.window.pos(), before.topLeft())
                    self.assertFalse(self.window._moving)
                    self.assertFalse(self.window._resize_edges)
                    self.assertFalse(self.window._narrow_drag)
            seek.assert_not_called()


if __name__ == '__main__':
    unittest.main()
