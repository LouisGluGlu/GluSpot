from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QRect, QRectF, QSize
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.models import ColorToken, TrackData
from api.online_music import AccountSnapshot
from local.backdrop_variants import BACKDROP_VARIANTS, resolve_backdrop_variant
from local.music_surface_background import AlbumFlowBackgroundRenderer
from local.music_surface_palette import relative_luminance
from tests.glass_background_reference import reference_field_pixels


PALETTES = (("#3b2419", "#ae743e", "#edbd76"), ("#3e0712", "#dd173e", "#ff5c56"))


def pixels(image):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.width(), 4)[..., :3].copy()


class BackdropRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        env = patch.dict(os.environ, {"GLUSPOT_BACKDROP_VARIANT": "classic"})
        env.start()
        self.addCleanup(env.stop)

    def test_resolve_default_normalization_and_invalid_fallback(self):
        os.environ.pop("GLUSPOT_BACKDROP_VARIANT")
        self.assertEqual(resolve_backdrop_variant(), "classic")
        for value in (*BACKDROP_VARIANTS, " FLOW ", "", "unknown"):
            with self.subTest(value=value):
                expected = value.strip().lower() if value.strip().lower() in BACKDROP_VARIANTS else "classic"
                os.environ["GLUSPOT_BACKDROP_VARIANT"] = value
                self.assertEqual(resolve_backdrop_variant(), expected)
                self.assertEqual(resolve_backdrop_variant(value), expected)
        self.assertEqual(resolve_backdrop_variant("noir"), "noir")

    def test_default_and_explicit_classic_match_frozen_pixels_at_zero_intensity(self):
        os.environ.pop("GLUSPOT_BACKDROP_VARIANT")
        for palette in PALETTES:
            colors = tuple(QColor(color) for color in palette)
            weights = (.5, .3, .2)
            for size in (QSize(960, 540), QSize(433, 550)):
                expected = reference_field_pixels(AlbumFlowBackgroundRenderer,
                    AlbumFlowBackgroundRenderer._raster_size(size),
                    AlbumFlowBackgroundRenderer._palette_slots(colors, weights),
                    AlbumFlowBackgroundRenderer._domain_strengths(colors, weights), 0.)
                for variant in (None, "classic"):
                    with self.subTest(palette=palette, size=size, variant=variant):
                        actual = AlbumFlowBackgroundRenderer(variant).render(size, colors, 0., intensity=0., weights=weights)
                        np.testing.assert_array_equal(pixels(actual), pixels(expected))

    def test_new_styles_are_bounded_static_and_never_submit_pixel_work(self):
        colors = tuple(QColor(color) for color in PALETTES[0])
        for variant in BACKDROP_VARIANTS[1:]:
            with self.subTest(variant=variant):
                renderer = AlbumFlowBackgroundRenderer(variant)
                with patch("local.liquid_glass_tasks.glass_pixel_tasks", side_effect=AssertionError("static worker")):
                    first = renderer.render(QSize(3840, 2160), colors, 0., intensity=0.)
                    for now, intensity in ((.04, 0.), (120., 1.), (100000., .74)):
                        current = renderer.render(QSize(3840, 2160), colors, now, intensity=intensity,
                                                  material_owner=object())
                        self.assertEqual(current.cacheKey(), first.cacheKey())
                self.assertLessEqual(max(first.width(), first.height()), 256)
                self.assertEqual(renderer._frame.cacheKey(), first.cacheKey())

    def test_new_fields_keep_saturation_smoothness_and_white_ink_envelope(self):
        for palette in PALETTES:
            colors = tuple(QColor(color) for color in palette)
            frames = []
            for variant in BACKDROP_VARIANTS[1:]:
                with self.subTest(palette=palette, variant=variant):
                    renderer = AlbumFlowBackgroundRenderer(variant)
                    frame = renderer.render(QSize(256, 144), colors, 0.)
                    rgb = pixels(frame).astype(np.float32) / 255.
                    linear = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
                    brightest = float((linear @ np.array((.2126, .7152, .0722))).max())
                    envelope = relative_luminance(renderer.contrast_backdrop(frame))
                    self.assertLessEqual(brightest, envelope)
                    self.assertGreater((1. + .05) / (envelope + .05), 5.9)
                    # Dither is allowed a few levels; no contour/seam jumps.
                    self.assertLessEqual(float(np.abs(np.diff(rgb, axis=1)).max()) * 255, 6.)
                    if variant == "flow":
                        saturation = (rgb.max(axis=2) - rgb.min(axis=2)) / np.maximum(rgb.max(axis=2) + rgb.min(axis=2), 1e-8)
                        self.assertGreater(float(saturation.min()), .55)
                        self.assertGreater(float(rgb.max() - rgb.min()), .15)
                    if variant == "noir":
                        self.assertLessEqual(float(rgb.max()) * 255, 15.)
                    frames.append(pixels(frame))
            self.assertFalse(np.array_equal(frames[0], frames[1]))
            self.assertFalse(np.array_equal(frames[1], frames[2]))

    def test_invalid_empty_and_neutral_palettes_stay_neutral(self):
        for colors in ((), (QColor(),), (QColor("#222222"), QColor("#dddddd"))):
            for variant in BACKDROP_VARIANTS[1:]:
                frame = AlbumFlowBackgroundRenderer(variant).render(QSize(61, 37), colors, 0.)
                rgb = pixels(frame)
                np.testing.assert_array_equal(rgb[..., 0], rgb[..., 1])
                np.testing.assert_array_equal(rgb[..., 1], rgb[..., 2])

    def test_palette_size_and_variant_retarget_without_mutating_retained_frame(self):
        renderer = AlbumFlowBackgroundRenderer("flow")
        colors = (QColor("#e32641"), QColor("#610d25"))
        first = renderer.render(QSize(960, 540), colors, 0.)
        retained = pixels(first)
        colors[0].setRgb(20, 120, 230)
        changed = renderer.render(QSize(960, 540), colors, 0.)
        self.assertNotEqual(changed, first)
        resized = renderer.render(QSize(433, 550), colors, 0.)
        self.assertNotEqual(changed.size(), resized.size())
        renderer.set_backdrop_variant(" NOIR ")
        self.assertEqual(renderer.backdrop_variant, "noir")
        self.assertNotEqual(renderer.render(QSize(960, 540), colors, 0.), changed)
        np.testing.assert_array_equal(pixels(first), retained)
        with self.assertRaises(ValueError):
            renderer.set_backdrop_variant("invalid")
        os.environ["GLUSPOT_BACKDROP_VARIANT"] = "frosted"
        renderer.set_backdrop_variant(None)
        self.assertEqual(renderer.backdrop_variant, "frosted")

    def test_style_change_fences_inflight_classic_completion(self):
        renderer = AlbumFlowBackgroundRenderer()
        old_domain = (1, (256, 144), (256, 144, .74, ()), ())
        renderer._async_domain = old_domain
        renderer.set_backdrop_variant("flow")
        current = renderer.render(QSize(256, 144), (QColor("red"),), 0.)
        stale = QImage(256, 144, QImage.Format.Format_RGB32)
        stale.fill(QColor("white"))
        renderer._accept_background_frame(stale, old_domain, (1., 0., ((255, 255, 255),), ()))
        self.assertEqual(renderer._frame, current)


class BackdropWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        env = patch.dict(os.environ, {"GLUSPOT_BACKDROP_VARIANT": "classic",
                                     "GLUSPOT_LOGIN_VARIANT": "sidebar", "GLUSPOT_LIBRARY_VARIANT": "sidebar"})
        env.start()
        self.addCleanup(env.stop)
        screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 1920, 1080),
                                 geometry=lambda: QRect(0, 0, 1920, 1080))
        screen_patch = patch.object(QGuiApplication, "screenAt", return_value=screen)
        screen_patch.start()
        self.addCleanup(screen_patch.stop)

    def dispose(self, window):
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_login_setter_refreshes_visible_static_frame_and_preserves_account(self):
        from local.playback_entry_window import ENTRY_SOURCES, PlaybackEntryWindow
        window = PlaybackEntryWindow()
        self.addCleanup(self.dispose, window)
        window.show_sources()
        window.set_sources(tuple(SimpleNamespace(source_id=s.source_id, available=s.login_supported) for s in ENTRY_SOURCES))
        window.select_source("netease")
        window._expansion.stop()
        window._expanded = 1.
        account = AccountSnapshot("netease", True, "fixture", "LouisHerm", state="verified")
        window.set_account(account)
        window.resize(960, 540)
        window.show()
        self.app.processEvents()
        previous = window._background_frame.copy()
        for variant in BACKDROP_VARIANTS[1:]:
            window.set_backdrop_variant(variant)
            self.assertNotEqual(window._background_frame, previous)
            self.assertIs(window._account, account)
            self.assertEqual(window.login_state, "confirmed")
            self.assertFalse(window._frame_timer.isActive())
            key = window._background_frame.cacheKey()
            window._source_backdrop()
            self.assertEqual(window._background_frame.cacheKey(), key)
            previous = window._background_frame.copy()
        with self.assertRaises(ValueError):
            window.set_backdrop_variant("invalid")

    def test_library_setter_stops_timer_and_survives_palette_change_and_reopen(self):
        from local.music_library_window import MusicLibraryWindow
        window = MusicLibraryWindow()
        self.addCleanup(self.dispose, window)
        window.resize(960, 540)
        window.set_now_playing(TrackData("Trippin", "Kara Marni", (), ColorToken(174, 116, 62),
                                        ColorToken(59, 36, 25), (), is_playing=True))
        window.show()
        self.app.processEvents()
        self.assertTrue(window._background_timer.isActive())
        previous = window._background_frame.copy()
        for variant in BACKDROP_VARIANTS[1:]:
            window.set_backdrop_variant(variant)
            self.assertNotEqual(window._background_frame, previous)
            self.assertFalse(window._background_timer.isActive())
            window.hide()
            window.show()
            self.assertEqual(window._background_renderer.backdrop_variant, variant)
            self.assertFalse(window._update_background())
            previous = window._background_frame.copy()
        window.set_backdrop_variant("classic")
        self.assertTrue(window._background_timer.isActive())
        with self.assertRaises(ValueError):
            window.set_backdrop_variant("invalid")

    def test_music_surface_honors_environment_and_reuses_static_frame(self):
        from local.music_surface_window import MusicSurfaceWindow
        from tests.test_music_fullscreen import _Owner
        owner = _Owner()
        window = MusicSurfaceWindow(owner)
        self.addCleanup(owner.close)
        self.addCleanup(window.shutdown)
        target = QImage(960, 540, QImage.Format.Format_RGB32)
        previous = None
        for variant in BACKDROP_VARIANTS:
            os.environ["GLUSPOT_BACKDROP_VARIANT"] = variant
            with QPainter(target) as painter:
                window._paint_background(painter, QRectF(0, 0, 960, 540), QColor("#ae743e"), QColor("#3b2419"), 0.)
            self.assertEqual(window._album_flow_background.backdrop_variant, variant)
            current = window._background_cache.copy()
            if previous is not None:
                self.assertNotEqual(current, previous)
            if variant != "classic":
                key = window._background_cache.cacheKey()
                with QPainter(target) as painter:
                    window._paint_background(painter, QRectF(0, 0, 960, 540), QColor("#ae743e"), QColor("#3b2419"), 10.)
                self.assertEqual(window._background_cache.cacheKey(), key)
                self.assertFalse(window._background_rebuilt_this_paint)
            previous = current


if __name__ == "__main__":
    unittest.main()
