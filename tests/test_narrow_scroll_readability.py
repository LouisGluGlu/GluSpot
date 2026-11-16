"""Wheel browsing sharpens row ink without changing the chrome materials."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QWheelEvent
from PySide6.QtWidgets import QApplication

from tests import test_narrow_v4 as fixtures


class NarrowScrollReadabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp = fixtures.NarrowCommitTests.setUp
    tearDown = fixtures.NarrowCommitTests.tearDown
    apply = fixtures.NarrowCommitTests.apply

    def wheel(self, now, delta=-120, inside=True):
        window = self.window
        point = QPointF(180, 320) if inside else QPointF(2, 2)
        window._lyric_clip_rect = QRectF(30, 150, 300, 350)
        event = QWheelEvent(point, point, QPoint(), QPoint(0, delta),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.ScrollUpdate, False)
        with patch('local.music_surface_window.time.monotonic', return_value=now):
            window.wheelEvent(event)

    def levels(self, now):
        positions = tuple(float(row * 80) for row in range(7))
        ranges = tuple((row, row + 1) for row in range(7))
        with patch('local.music_surface_window.time.monotonic', return_value=now):
            return self.window._lyric_blur_follow_levels('song', 3, positions, positions, ranges)[0]

    def test_rows_are_sharp_during_wheel_and_restore_after_short_settle(self):
        self.assertTrue(self.window.narrow)
        self.assertEqual(self.levels(100), (2, 1, 1, 0, 1, 1, 2))
        self.wheel(100)
        self.assertIsNotNone(self.window._browse_index)
        self.assertEqual(self.levels(100.1), (0,) * 7)
        self.wheel(100.25)
        self.assertEqual(self.levels(100.5), (0,) * 7)
        self.assertEqual(self.levels(100.61), (2, 1, 1, 0, 1, 1, 2))
        # The viewport may remain browsed for its existing four-second idle
        # period; the row blur has its own brief settle deadline.
        self.assertIsNotNone(self.window._browse_index)

    def test_non_lyric_and_zero_delta_input_do_not_clear_rows(self):
        for delta, inside in ((0, True), (-120, False)):
            with self.subTest(delta=delta, inside=inside):
                self.wheel(100, delta, inside)
                self.assertEqual(self.levels(100.1), (2, 1, 1, 0, 1, 1, 2))

    def test_cover_lyrics_browsing_uses_the_same_sharp_settle_path(self):
        self.window.mode = 'cover_lyrics'
        self.wheel(100)
        self.assertEqual(self.levels(100.1), (0,) * 7)
        self.assertEqual(self.levels(100.36), (2, 1, 1, 0, 1, 1, 2))

    def test_wheel_preserves_prepared_chrome_veil_and_control_glass(self):
        window = self.window
        # Establish the real prepared chrome at a quiet presentation pose.
        source = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        source.fill(Qt.GlobalColor.black)
        with QPainter(source) as painter:
            for x in range(0, source.width(), 8):
                painter.fillRect(x, 0, 4, source.height(), Qt.GlobalColor.white)
        window._background_cache = source
        window.visibility.progress = 1.
        window.refresh_controls(100)
        window.refresh_background_veil(100, force=True)
        bands = window._controls_bands
        veil_states = window.background_veil._states
        self.assertTrue(window.background_veil.ready)
        self.wheel(100)
        self.assertEqual(self.levels(100.1), (0,) * 7)
        self.assertIs(window._controls_bands, bands)
        self.assertIs(window.background_veil._states, veil_states)
        image = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        with QPainter(image) as painter, \
                patch.object(window.background_veil, 'paint', wraps=window.background_veil.paint) as veil:
            window.background_veil.paint(painter, QRectF(window.rect()), 1.)
            window._paint_chrome_bands(painter)
            self.assertEqual(veil.call_count, 1)
        self.assertGreater(image.pixelColor(180, 10).alpha(), 0)
        self.assertEqual(self.levels(100.36), (2, 1, 1, 0, 1, 1, 2))


if __name__ == '__main__':
    unittest.main()
