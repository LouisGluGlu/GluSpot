"""No-media state follows the interface theme without recoloring retained songs."""
from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.models import ColorToken
from api.providers import EmptyIslandDataProvider, empty_visual_data
from local.island_stack_visual import IslandStackVisual
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_palette import _png


class IdleNeutralPaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.visual = IslandStackVisual(EmptyIslandDataProvider(empty_visual_data()))
        self.visual.timer.stop()
        self.visual.location_mode = "free"
        self.visual.finder_mode = False

    def tearDown(self):
        self.visual.close()
        self.visual.deleteLater()
        self.app.processEvents()

    def test_provider_empty_marker_and_colors_are_neutral_without_theme_io(self):
        track = empty_visual_data().track
        self.assertEqual(track.position_source, "none")
        self.assertEqual(track.position_confidence, 0.)
        self.assertFalse(track.album_art_bytes)
        self.assertFalse(track.is_playing)
        for color in (track.album_color, track.secondary_color):
            self.assertEqual(color.red, color.green)
            self.assertEqual(color.green, color.blue)

    def test_idle_follows_the_saved_theme_and_windows_only_for_system(self):
        self.visual.music_ambience_style = "sea_level"
        cases = (
            ("dark", False, "pure_black"),
            ("light", True, "pure_white"),
            ("system", True, "pure_black"),
            ("system", False, "pure_white"),
        )
        for mode, windows_dark, style in cases:
            self.visual.settings_getter = lambda key, default=None, mode=mode: mode if key == "v2_theme_mode" else default
            with self.subTest(mode=mode, windows_dark=windows_dark), patch("local.ui_theme.system_dark_mode", return_value=windows_dark):
                self.assertTrue(self.visual.media_no_audio)
                self.assertEqual(self.visual._effective_music_ambience_style(), style)
                self.assertEqual(self.visual.music_ambience_style, "sea_level")
                for color in (*self.visual._current_music_surface_colors(), *self.visual._album_mist_palette()):
                    self.assertEqual(color.red(), color.green())
                    self.assertEqual(color.green(), color.blue())

    def test_dark_theme_paints_the_idle_bar_dark_even_when_windows_is_light(self):
        self.visual.music_ambience_style = "sea_level"
        self.visual.settings_getter = lambda key, default=None: "dark" if key == "v2_theme_mode" else default
        image = QImage(360, 120, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch("local.ui_theme.system_dark_mode", return_value=False), QPainter(image) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.visual._paint_music(painter, QRectF(8, 8, 344, 104), True)
        color = image.pixelColor(180, 60)
        self.assertLess(color.lightness(), 48)
        self.assertEqual(color.alpha(), 255)
        self.assertEqual(self.visual._music_lyric_text_color().getRgb()[:3], (255, 255, 255))

    def test_library_footer_idle_bar_matches_a_dark_interface(self):
        from local.music_library_window import MusicLibraryWindow
        window = MusicLibraryWindow()
        try:
            window.set_background_context(lambda key, default=None: "dark" if key == "v2_theme_mode" else default)
            window.now_playing_island.set_track(None)
            with patch("local.ui_theme.system_dark_mode", return_value=False):
                self.assertTrue(window.now_playing_island._no_audio_visual_active())
                self.assertEqual(window.now_playing_island._effective_music_ambience_style(), "pure_black")
                image = QImage(320, 100, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                with QPainter(image) as painter:
                    window.now_playing_island._paint_music(painter, QRectF(4, 4, 312, 92), True)
            self.assertLess(image.pixelColor(160, 48).lightness(), 48)
        finally:
            window.close()
            window.deleteLater()

    def test_empty_cover_placeholder_is_grayscale_in_both_system_themes(self):
        for dark in (False, True):
            with self.subTest(dark=dark), patch("local.ui_theme.system_dark_mode", return_value=dark):
                image = QImage(84, 84, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                with QPainter(image) as painter:
                    self.visual._paint_cover(painter, QRectF(2, 2, 80, 80), 12)
                for x, y in ((20, 20), (40, 40), (65, 65)):
                    color = image.pixelColor(x, y)
                    self.assertEqual(color.red(), color.green())
                    self.assertEqual(color.green(), color.blue())
                self.assertLess(image.pixelColor(40, 40).value(), 80) if dark else self.assertGreater(image.pixelColor(40, 40).value(), 180)

    def test_real_paused_track_without_cover_keeps_its_ordinary_provider_color(self):
        data = empty_visual_data("real paused", "artist")
        track = replace(data.track, position_source="windows-media", track_id="real-paused",
                        album_color=ColorToken(184, 32, 82), secondary_color=ColorToken(80, 12, 44))
        self.visual._apply_visual_data(replace(data, track=track))
        self.visual.music_ambience_style = "sea_level"
        self.visual.music_surface_transition_started_at = 0.
        self.assertFalse(self.visual.media_no_audio)
        self.assertEqual(self.visual._effective_music_ambience_style(), "sea_level")
        self.assertEqual(self.visual._current_music_surface_colors()[0].getRgb()[:3], (184, 32, 82))

    def test_player_idle_palette_follows_system_without_artwork_loading(self):
        window = MusicSurfaceWindow(self.visual)
        try:
            for dark in (False, True):
                with self.subTest(dark=dark), patch("local.ui_theme.system_dark_mode", return_value=dark):
                    palette = window._art_palette()
                    for color in (palette.primary, palette.secondary, *palette.ambient_colors):
                        self.assertEqual(color.red(), color.green())
                        self.assertEqual(color.green(), color.blue())
                    self.assertLess(palette.primary.value(), 80) if dark else self.assertGreater(palette.primary.value(), 180)
                    self.assertFalse(window._artwork_is_loading())
        finally:
            window.shutdown()

    def test_real_paused_artwork_is_retained_and_returns_from_idle(self):
        data = empty_visual_data("real paused", "artist")
        track = replace(data.track, position_source="owned-player", track_id="owned:netease:9",
                        album_art_bytes=_png(QColor("#d03c65")), album_color=ColorToken(208, 60, 101))
        self.visual._apply_visual_data(replace(data, track=track))
        self.assertFalse(self.visual.media_no_audio)
        self.assertFalse(self.visual.album_art.isNull())
        key = self.visual.album_art.cacheKey()
        self.visual._apply_visual_data(replace(data, track=track))
        self.assertEqual(self.visual.album_art.cacheKey(), key)
        self.visual._apply_visual_data(empty_visual_data())
        self.assertTrue(self.visual.media_no_audio)
        self.assertTrue(self.visual.album_art.isNull())


if __name__ == "__main__":
    unittest.main()
