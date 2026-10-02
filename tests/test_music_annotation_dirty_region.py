from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from local.music_surface_window import MusicSurfaceWindow


class _AnnotationOwner(QWidget):
    def __init__(self):
        super().__init__()
        self._music_windowed_preference = False
        self.music_intro_cleanup_enabled = False
        self.enable_dual_line_lyrics = True
        self.music_surface_reading_strategy = "gold"
        self.track_title = "Annotation dirty region"
        self.artist = "Test singer"
        self.media_track_id = "annotation-dirty-region"
        self.media_is_playing = True
        self.media_position_seconds = 4.0
        self.timed_lyrics = [
            (0.0, "Shine (echo) on me\n翻译：照耀（回声）我"),
            (20.0, "Second line"),
            (40.0, "Third line"),
            (60.0, "Fourth line"),
        ]
        self.timed_lyric_units = [(), (), (), ()]
        self.timed_lyric_timing_kinds = ["line"] * 4

    def _run_background(self, _name, callback):
        callback()


class MusicAnnotationDirtyRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _AnnotationOwner()
        # These comparisons isolate annotation ink; live backdrop motion
        # has its own frame/dirty-region regression coverage.
        self.owner.music_flow_intensity = 0.0
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(1920, 1080)
        self.window._fullscreen = True

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    @staticmethod
    def _image():
        image = QImage(3840, 2160, QImage.Format.Format_RGB32)
        image.setDevicePixelRatio(2.0)
        image.fill(Qt.GlobalColor.black)
        return image

    def test_short_bracket_layers_keep_active_region_near_their_ink(self):
        image = self._image()
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window.render(image)
        self.assertIsNotNone(self.window._song_parenthetical_layouts[0])
        self.assertIsNotNone(self.window._song_parenthetical_translation_layouts[0])
        active = self.window._active_lyric_animation_rect
        column = self.window._lyric_animation_rect
        self.assertGreater(active.width(), 0)
        self.assertGreater(active.height(), 0)
        self.assertLess(active.width(), column.width() * 0.75)
        self.assertGreater(active.height(), self.window._song_layouts[0][1])

    def test_partial_repaint_matches_full_frame_during_bracket_entrance(self):
        partial = self._image()
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window.render(partial)
        active = self.window._active_lyric_animation_rect
        region = QRegion(active)
        with patch("local.music_surface_window.time.monotonic", return_value=100.15):
            self.window.render(partial, active.topLeft(), region)
            full = self._image()
            self.window.render(full)
        logical = active.intersected(self.window.rect())
        physical = QRect(logical.x() * 2, logical.y() * 2,
                         logical.width() * 2, logical.height() * 2)
        partial_crop = partial.copy(physical)
        full_crop = full.copy(physical)
        self.assertEqual(
            bytes(partial_crop.constBits()),
            bytes(full_crop.constBits()),
        )

    def test_long_bracket_translation_extends_region_and_matches_full_frame(self):
        self.owner.timed_lyrics[0] = (
            0.0,
            "Shine (echo) on me\n翻译：照耀（这是一段很长很长的括号译文，需要延伸到歌词列的右侧）我",
        )
        self.owner.media_position_seconds = 4.0
        image = self._image()
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window.render(image)
        active = self.window._active_lyric_animation_rect
        self.assertGreater(active.width(), 646)
        self.assertLessEqual(active.right(), self.window.rect().right())
        with patch("local.music_surface_window.time.monotonic", return_value=100.2):
            self.window.render(image, active.topLeft(), QRegion(active))
            full = self._image()
            self.window.render(full)
        logical = active.intersected(self.window.rect())
        physical = QRect(logical.x() * 2, logical.y() * 2,
                         logical.width() * 2, logical.height() * 2)
        partial_crop = image.copy(physical)
        full_crop = full.copy(physical)
        self.assertEqual(bytes(partial_crop.constBits()), bytes(full_crop.constBits()))


if __name__ == "__main__":
    unittest.main()
