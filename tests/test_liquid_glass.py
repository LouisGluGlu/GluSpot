"""Optical sampling, bounded caching and native painter-state regressions."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from local.liquid_glass import (GLASS_CACHE_BYTES, GLASS_CACHE_ENTRIES,
                                GLASS_GEOMETRY_BYTES, GLASS_GEOMETRY_ENTRIES,
                                clear_glass_cache, glass_cache_info,
                                glass_geometry_cache_info, paint_liquid_glass, widget_glass_backdrop)
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner


class LiquidGlassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        clear_glass_cache()

    def tearDown(self):
        clear_glass_cache()

    @staticmethod
    def scene():
        image = QImage(180, 100, QImage.Format.Format_RGB32)
        for y in range(image.height()):
            for x in range(image.width()):
                image.setPixelColor(x, y, QColor((x * 7) % 255, (y * 13) % 255, (x + y) % 255))
        return image

    def render(self, backdrop=None, source=None, **options):
        image = QImage(180, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            paint_liquid_glass(painter, QRectF(20, 20, 140, 60), backdrop=backdrop,
                               backdrop_rect=source, cache_token="test", **options)
        return image

    def test_lens_uses_actual_backdrop_and_keeps_round_corners_clear(self):
        scene = self.scene()
        first = self.render(scene, QRectF(20, 20, 140, 60))
        other = self.render(scene, QRectF(0, 0, 140, 60))
        fallback = self.render()
        self.assertNotEqual(first.pixelColor(90, 50), fallback.pixelColor(90, 50))
        self.assertNotEqual(first.pixelColor(90, 50), other.pixelColor(90, 50))
        self.assertEqual(first.pixelColor(20, 20).alpha(), 0)
        self.assertEqual(first.pixelColor(90, 50).alpha(), 255)
        self.assertGreater(fallback.pixelColor(90, 50).alpha(), 100)
        self.assertLess(fallback.pixelColor(90, 50).alpha(), 230)

    def test_states_change_material_without_losing_transparency(self):
        idle, hover, pressed, active = (self.render(**opts) for opts in
                                       ({}, {"hovered": True}, {"pressed": True}, {"active": True}))
        self.assertGreater(hover.pixelColor(90, 50).lightness(), idle.pixelColor(90, 50).lightness())
        self.assertLess(pressed.pixelColor(90, 50).lightness(), hover.pixelColor(90, 50).lightness())
        self.assertGreater(active.pixelColor(90, 50).lightness(), 200)
        self.assertLess(active.pixelColor(90, 50).alpha(), 255)

    def test_cache_reuses_then_refreshes_in_place_at_bounded_rate(self):
        scene = self.scene()
        with patch("local.liquid_glass.time.monotonic", return_value=1):
            first = self.render(scene)
        scene.fill(QColor("#e45f73"))
        with patch("local.liquid_glass.time.monotonic", return_value=1.05):
            held = self.render(scene)
        with patch("local.liquid_glass.time.monotonic", return_value=1.11):
            refreshed = self.render(scene)
        self.assertEqual(first, held)
        self.assertNotEqual(first, refreshed)
        self.assertEqual(glass_cache_info()[0], 1)

    def test_material_cache_is_bounded_during_many_buttons_and_resizes(self):
        source = self.scene()
        output = QImage(400, 200, QImage.Format.Format_ARGB32_Premultiplied)
        output.fill(Qt.GlobalColor.transparent)
        with QPainter(output) as painter:
            for i in range(GLASS_CACHE_ENTRIES + 12):
                paint_liquid_glass(painter, QRectF(2, 2, 300 + i % 30, 190),
                                   backdrop=source, cache_token=i)
        entries, size = glass_cache_info()
        self.assertLessEqual(entries, GLASS_CACHE_ENTRIES)
        self.assertLessEqual(size, GLASS_CACHE_BYTES)
        entries, size = glass_geometry_cache_info()
        self.assertLessEqual(entries, GLASS_GEOMETRY_ENTRIES)
        self.assertLessEqual(size, GLASS_GEOMETRY_BYTES)

    def test_painter_opacity_clip_transform_and_pen_are_restored(self):
        image = QImage(200, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            painter.setOpacity(.6)
            painter.translate(3, 4)
            painter.setClipRect(QRectF(0, 0, 190, 80))
            before = (painter.opacity(), painter.transform(), painter.clipBoundingRect(), painter.pen())
            paint_liquid_glass(painter, QRectF(20, 20, 140, 60), backdrop=self.scene(), opacity=.5)
            after = (painter.opacity(), painter.transform(), painter.clipBoundingRect(), painter.pen())
        self.assertEqual(before, after)

    def test_backdrop_mapping_is_local_and_never_captures_widgets(self):
        host, child = QWidget(), QWidget()
        child.setParent(host)
        host.resize(400, 200)
        child.setGeometry(100, 50, 80, 40)
        host._background_cache = self.scene()
        with patch.object(host, "grab", side_effect=AssertionError("capture forbidden")), \
             patch.object(host, "render", side_effect=AssertionError("capture forbidden")):
            image, region = widget_glass_backdrop(child)
        self.assertEqual(image, host._background_cache)
        self.assertEqual(region, QRectF(45, 25, 36, 20))
        host.close()

    def test_light_and_dark_rims_leave_content_ink_outside_material_owner(self):
        # The material is purely a background; caller-painted glyphs remain exact.
        image = self.render(light=True)
        with QPainter(image) as painter:
            painter.fillRect(QRectF(80, 40, 20, 20), QColor("#14151a"))
        self.assertEqual(image.pixelColor(90, 50), QColor("#14151a"))

    def test_control_prewarm_backdrop_stays_consistent_through_idle_and_reversal(self):
        owner = _TransitionOwner()
        window = MusicSurfaceWindow(owner)
        window._timer.stop()
        window._audio_output_status_timer.stop()
        window._fullscreen = True
        window._reset_control_idle(100)
        first = self.scene()
        window._background_cache = first
        lead = window.FULLSCREEN_CONTROL_IDLE_SECONDS - 2
        try:
            with patch("local.music_surface_window.time.monotonic", return_value=100 + lead):
                frozen = window._shared_glass_background()
                initial_key = window._control_glass_cache_key()
            window._background_cache = QImage(first.size(), QImage.Format.Format_RGB32)
            window._background_cache.fill(QColor("#47b389"))
            with patch("local.music_surface_window.time.monotonic", return_value=100 + lead + .5):
                self.assertEqual(window._shared_glass_background(), frozen)
                self.assertEqual(window._control_glass_cache_key(), initial_key)
            window._control_reveal_target = 0
            window._control_reveal_from = 1
            window._control_transition_started_at = 110
            with patch("local.music_surface_window.time.monotonic", return_value=110.1):
                self.assertEqual(window._shared_glass_background(), frozen)
            window._control_reveal_from, window._control_reveal_target = .5, 1
            window._control_last_motion_at = window._control_transition_started_at = 110.1
            with patch("local.music_surface_window.time.monotonic", return_value=110.2):
                self.assertEqual(window._shared_glass_background(), frozen)
            with patch("local.music_surface_window.time.monotonic", return_value=112):
                self.assertEqual(window._shared_glass_background(), window._background_cache)
            window._control_prewarm_remaining = 0
            with patch("local.music_surface_window.time.monotonic", return_value=110.1 + lead):
                self.assertNotEqual(window._control_glass_cache_key(), initial_key)
                self.assertGreater(window._control_prewarm_remaining, 0)
                frozen = window._shared_glass_background()
                owner.media_track_id = "another-song"
                window._background_cache = first
                self.assertNotEqual(window._shared_glass_background(), frozen)
        finally:
            window.shutdown()
            owner.close()


if __name__ == "__main__":
    unittest.main()
