from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner
from tests.test_owned_playback_surface import _Provider


class MusicSurfaceDjFooterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.owner.track_title = "Sinister"
        self.owner.artist = "Midnight"
        self.owner.data_provider = _Provider()
        self.enabled = True
        self.presentation = {"state": "loading", "introduction_pending": True,
            "next_preload_state": "idle", "is_preview": False}
        provider = self.owner.data_provider
        provider.fm = True
        provider.get_owned_personal_dj_enabled = lambda: self.enabled
        provider.is_owned_personal_dj_speaking = lambda: False
        provider.get_personal_dj_presentation = lambda: self.presentation
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(303, 400)
        with patch("local.music_surface_window.time.monotonic", return_value=100.):
            self.window._sync_owned_playback_mode()
            self.window._reset_control_idle(100.)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def render(self):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.window.render(image)
        return image

    def test_compact_footer_is_stable_for_enabled_disabled_pending_and_hidden_controls(self):
        region = QRectF(self.window.rect())
        expected = self.window._personal_dj_footer_height(region)
        self.assertGreaterEqual(expected, 50.)
        for enabled in (False, True):
            for pending, failed in ((False, False), (True, False), (False, True)):
                for reveal in (0., .5, 1.):
                    with self.subTest(enabled=enabled, pending=pending, failed=failed, reveal=reveal):
                        self.window._owned_personal_dj_enabled = enabled
                        self.window._dj_introduction_pending = pending
                        self.window._dj_introduction_failed = failed
                        self.window._control_reveal_from = self.window._control_reveal_target = reveal
                        self.assertEqual(self.window._personal_dj_footer_height(region), expected)
        self.window._owned_personal_dj_enabled = None
        self.assertEqual(self.window._personal_dj_footer_height(region), 0.)

    def test_wide_surface_does_not_reserve_the_compact_footer(self):
        self.window.resize(960, 540)
        self.assertEqual(self.window._personal_dj_footer_height(QRectF(self.window.rect())), 0.)

    def test_status_capsule_and_transport_do_not_intersect_at_reported_compact_sizes(self):
        for size in ((303, 400), (354, 401), (372, 432), (200, 400), (640, 400), (960, 540)):
            for layout in ("cover", "cover_lyrics"):
                for fullscreen in (False, True):
                    with self.subTest(size=size, layout=layout, fullscreen=fullscreen), \
                            patch("local.music_surface_window.time.monotonic", return_value=100.):
                        self.window.resize(*size)
                        self.window._fullscreen = fullscreen
                        self.window.set_content_layout(layout, animate=False)
                        self.window._cover_layout_from = self.window._cover_layout_to = 1.
                        self.window._reset_control_idle(100.)
                        self.render()
                        region = QRectF(self.window.rect())
                        icon, _ = self.window._personal_dj_geometry(region, 100.)
                        status = self.window._personal_dj_outro_status_rect(region, 100.)
                        transport = self.window._media_control_panel_rect
                        self.assertTrue(region.contains(status))
                        self.assertTrue(status.contains(icon))
                        self.assertTrue(region.contains(transport))
                        self.assertFalse(status.intersects(transport), (status, transport))
                        self.assertTrue(self.window._personal_dj_damage_rect(region).contains(status.toAlignedRect()))
                        self.assertTrue(self.window._personal_dj_hit_path(region, 100., shifted=False).contains(status.center()))

    def test_mouse_resize_enforces_400_height_while_width_stays_independent(self):
        self.assertEqual(self.window._minimum_window_size().width(), 1)
        self.assertEqual(self.window._minimum_window_size().height(), 400)
        self.window._drag_geometry = QRect(100, 100, 303, 401)
        self.window._drag_origin = QPoint(403, 501)
        self.window._resize_edges = {"right", "bottom"}
        self.window._moving = False
        move = SimpleNamespace(position=lambda: QPointF(272., 200.),
            globalPosition=lambda: QPointF(373., 301.),
            buttons=lambda: Qt.MouseButton.LeftButton, accept=lambda: None)
        with patch.object(self.window, "_available_geometry", return_value=QRect(0, 0, 1920, 1080)):
            self.window.mouseMoveEvent(move)
        self.assertEqual(self.window.width(), 273)
        self.assertEqual(self.window.height(), 400)
