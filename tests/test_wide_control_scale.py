"""Wide playback controls follow one window unit, including minimum hit sizes."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication

from _demos.narrow3.narrow_window import NarrowStudyWindow
from tests.test_music_surface_transition import _TransitionOwner
from tests.test_owned_playback_surface import _Provider


class WideControlScaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.owner.data_provider = _Provider()
        self.owner._music_library_back_callback = Mock()
        self.window = NarrowStudyWindow(self.owner, "B", "left", live=True)
        self.window._timer.stop()
        self.window.study_timer.stop()
        self.window._audio_output_status_timer.stop()
        self.window.set_content_layout("lyrics", animate=False)
        self.window._finish_content_layout()
        self.window._song_collection_state = lambda: SimpleNamespace(track_id="owned:netease:1")

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.owner.close()

    def pose(self, size):
        self.window._explicit_width_target = (*size, False)
        self.window.resize(*size)
        self.window._sync_narrow_mode()
        self.window._finish_width_chrome()
        self.window._finish_width_layout()
        self.window.narrow = False
        self.window._width_layout_fraction = 0.0
        self.window._control_reveal_from = self.window._control_reveal_target = 1.0

    def test_controls_follow_the_window_unit_and_minimum_hits(self):
        for size in ((715, 540), (560, 420), (1000, 700), (440, 340)):
            self.pose(size)
            unit = min(size[0] / 100.0, size[1] / 75.8)
            surface = QRectF(self.window.rect())
            _content, cover, _lyric = self.window._wide_content_column_geometry()
            title_font, title, artist_font, artist = self.window._metadata_layout(
                cover, _content, self.window._surface_scale(), 1.0)
            transport = self.window._wide_transport_rect(surface)
            top = self.window._window_control_base_rect(surface)
            collection = self.window._song_collection_geometry(surface, shifted=False)
            buttons = self.window._song_collection_rects(surface)
            icon = min(transport.height(), max(24.0, transport.width() * 5.2 / 30.2))
            # 440px is already the stacked lyric layout. Wide cover, transport
            # and chrome still follow u; the title sits beside that small cover.
            wide_pose = not self.window._content_layout_is_narrow()
            with self.subTest(size=size):
                self.assertAlmostEqual(self.window._control_unit(), unit)
                self.assertAlmostEqual(cover.left(), 6.8 * unit, delta=0.6)
                self.assertAlmostEqual(cover.top(), 6.8 * unit, delta=0.6)
                self.assertAlmostEqual(cover.width(), 37.8 * unit, delta=0.6)
                self.assertAlmostEqual(cover.width(), cover.height(), delta=0.2)
                if wide_pose:
                    self.assertAlmostEqual(title.top(), 46.2 * unit, delta=2.0)
                    self.assertAlmostEqual(title_font.pixelSize(), 3.1 * unit, delta=1.5)
                self.assertAlmostEqual(transport.left(), 10.6 * unit, delta=0.6)
                self.assertAlmostEqual(transport.width(), 30.2 * unit, delta=0.6)
                self.assertAlmostEqual(transport.top(), 56.0 * unit, delta=0.6)
                self.assertGreaterEqual(icon, 24.0)
                self.assertLessEqual(icon, max(32.0, 5.2 * unit) + 0.5)
                self.assertGreaterEqual(top.width(), 34.0)
                self.assertAlmostEqual(top.width(), max(34.0, 5.6 * unit), delta=0.6)
                self.assertAlmostEqual(self.window.width() - top.right(), 2.4 * unit, delta=0.6)
                self.assertGreaterEqual(collection.height(), 32.0)
                self.assertAlmostEqual(self.window.width() - collection.right(), 2.4 * unit, delta=1.5)
                self.assertEqual(artist.height() > 1.0, size[0] >= 500)
                self.assertEqual("playlist_add" in buttons, size[0] >= 440)
                self.assertIn("favorite", buttons)
