"""Resize shows controls, then hides them five seconds after the drag stops."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from _demos.narrow3.motion import Visibility
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_centered_layout import _Owner


class ResizeVisibilityTests(unittest.TestCase):
    def test_drag_holds_controls_and_a_new_drag_restarts_the_five_seconds(self):
        visibility = Visibility(True)
        visibility.advance(10, False, False, False)
        self.assertEqual(visibility.opacity.target, 0.0)
        visibility.hold_resize(True, 20)
        visibility.advance(24, False, False, False)
        self.assertEqual(visibility.progress, 1.0)
        visibility.hold_resize(False, 24)
        visibility.advance(28.9, False, False, False)
        self.assertEqual(visibility.opacity.target, 1.0)
        visibility.hold_resize(True, 29)
        visibility.hold_resize(False, 29)
        visibility.advance(33.9, False, False, False)
        self.assertEqual(visibility.opacity.target, 1.0)
        visibility.advance(34.1, False, False, False)
        self.assertEqual(visibility.opacity.target, 0.0)


class ResizeControlRevealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _Owner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window._timer.stop()
        self.window.resize(960, 540)
        self.window.show()
        self.app.processEvents()
        self.window._control_reveal_target = 0.0
        self.window._control_reveal_from = 0.0

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def reveal(self, now):
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            value = self.window._control_reveal_at(now)
        return value, self.window._control_reveal_target

    def test_resize_shows_controls_until_five_seconds_after_release(self):
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window._set_resize_chrome_hold(True)
        shown, target = self.reveal(104.0)
        self.assertEqual(shown, 1.0)
        self.assertEqual(target, 1.0)
        with patch("local.music_surface_window.time.monotonic", return_value=104.0):
            self.window._set_resize_chrome_hold(False)
        shown, target = self.reveal(108.9)
        self.assertEqual(shown, 1.0)
        self.assertEqual(target, 1.0)
        _shown, target = self.reveal(109.1)
        self.assertEqual(target, 0.0)

    def test_dragging_again_before_five_seconds_restarts_the_wait(self):
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window._set_resize_chrome_hold(True)
            self.window._set_resize_chrome_hold(False)
        with patch("local.music_surface_window.time.monotonic", return_value=103.0):
            self.window._set_resize_chrome_hold(True)
            self.window._set_resize_chrome_hold(False)
        _shown, target = self.reveal(107.9)
        self.assertEqual(target, 1.0)
        _shown, target = self.reveal(108.1)
        self.assertEqual(target, 0.0)


class BroadcastGlassBackdropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _Owner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window._timer.stop()
        self.window.resize(480, 240)
        self.window._reset_control_idle(now=1000.0)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def paint(self, color):
        scene = QImage(self.window.size(), QImage.Format.Format_RGB32)
        scene.fill(color)
        self.window._background_cache = scene
        self.window._clear_control_renderer.clear()
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(color)
        with QPainter(image) as painter:
            self.window._paint_surface_glass(painter, QRectF(30, 80, 92, 40), role="collection")
            self.window._paint_surface_glass(painter, QRectF(150, 76, 44, 44), role="dj-icon")
        return image

    @staticmethod
    def tone(image, x, y):
        red = green = blue = count = 0
        for dy in range(-6, 7):
            for dx in range(-6, 7):
                pixel = image.pixelColor(x + dx, y + dy)
                red += pixel.red()
                green += pixel.green()
                blue += pixel.blue()
                count += 1
        return red // count, green // count, blue // count

    def _theme(self, mode):
        self.owner.settings_getter = lambda key, default=None: {
            "v2_theme_mode": mode, "v2_music_glass_level": 50,
        }.get(key, default)

    def test_broadcast_disc_follows_the_same_backdrop_as_the_collection(self):
        self._theme("dark")
        pink = self.paint(QColor(214, 72, 128))
        pink_collection = self.tone(pink, 76, 100)
        pink_broadcast = self.tone(pink, 172, 98)
        self.assertGreater(pink_collection[0], pink_collection[2] + 25)
        self.assertGreater(pink_broadcast[0], pink_broadcast[2] + 25)
        self.assertLess(abs(pink_collection[0] - pink_broadcast[0]), 50)
        blue = self.paint(QColor(36, 92, 214))
        blue_broadcast = self.tone(blue, 172, 98)
        self.assertGreater(blue_broadcast[2], blue_broadcast[0] + 25)
        self.assertGreater(abs(pink_broadcast[0] - blue_broadcast[0]), 40)

    def test_light_default_plate_is_solid_and_shared_by_the_broadcast_disc(self):
        self._theme("light")
        image = self.paint(QColor(214, 72, 128))
        collection = self.tone(image, 76, 100)
        broadcast = self.tone(image, 172, 98)
        self.assertGreater(min(collection), 200)
        self.assertGreater(min(broadcast), 200)
        self.assertLess(abs(collection[0] - broadcast[0]), 20)
