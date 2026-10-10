from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
from tests.test_audio_output_panel import _RouteProvider
from tests.test_music_surface_transition import _TransitionOwner


class FusedOutputSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.owner.data_provider = _RouteProvider()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.window.show()
        self.window._timer.stop()
        self.window._sync_audio_output_status(settle=True)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _controls_image(self):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch.object(self.window, "_control_reveal_at", return_value=1.0):
            with QPainter(image) as painter:
                self.window._paint_window_controls(painter, QRectF(self.window.rect()))
        return image

    def _open(self):
        with patch.object(self.window, "_control_reveal_at", return_value=1.0):
            self.window._toggle_audio_output_panel()
        panel = self.window._audio_output_panel
        panel._animation_timer.stop()
        panel._poll_timer.stop()
        self.app.processEvents()
        return panel

    def test_menu_replaces_output_graphic_in_every_visible_pose_and_restores_on_close(self):
        before = self._controls_image()
        audio = self.window._button_rects["audio_output"].center().toPoint()
        translation = self.window._button_rects["translation"].center().toPoint()
        self.assertGreater(before.pixelColor(audio).alpha(), 0)
        panel = self._open()
        for progress, target in ((0.0, 1.0), (.48, 1.0), (1.0, 1.0), (.48, 0.0), (.01, 0.0)):
            with self.subTest(progress=progress, target=target):
                panel._reveal, panel._reveal_target = progress, target
                panel._apply_reveal_pose()
                image = self._controls_image()
                self.assertEqual(image.pixelColor(audio).alpha(), 0)
                self.assertGreater(image.pixelColor(translation).alpha(), 0)
                self.assertTrue(panel.isVisible())
        panel.dismiss(immediate=True)
        self.assertGreater(self._controls_image().pixelColor(audio).alpha(), 0)
        self.assertEqual(self.owner.data_provider.calls, [("refresh",)])

    def test_source_circle_is_complete_and_menu_aligns_with_adjacent_controls(self):
        for width, height, fullscreen in ((595, 333, False), (960, 540, False), (1920, 1080, True)):
            with self.subTest(size=(width, height), fullscreen=fullscreen):
                self.window._dismiss_audio_output_panel(immediate=True)
                self.window._fullscreen = fullscreen
                self.window.resize(width, height)
                self._controls_image()
                source = QRectF(self.window._button_rects["audio_output"])
                panel = self._open()
                panel._reveal, panel._reveal_target = 0.0, 1.0
                panel._apply_reveal_pose()
                self.assertLessEqual(panel.y(), source.top())
                self.assertLess(source.top() - panel.y(), 1.0)
                self.assertAlmostEqual(panel.x() + panel.width(), source.right(), delta=.51)
                self.assertTrue(QRectF(panel.rect()).contains(panel.render_pose.shell_rect))
                native_circle = panel.render_pose.shell_rect.translated(panel.pos())
                self.assertAlmostEqual(native_circle.top(), source.top())
                self.assertAlmostEqual(native_circle.right(), source.right())
                panel._reveal = 1.0
                panel._apply_reveal_pose()
                self.assertAlmostEqual(panel.y() + panel.render_pose.shell_rect.top(),
                                       source.top(), delta=.51)
                self.assertLessEqual(panel.y() + panel.height(), self.window.height())


if __name__ == "__main__":
    unittest.main()
