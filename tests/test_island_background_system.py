"""Actual OS theme selection and flat cover-color island regressions."""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.providers import EmptyIslandDataProvider, empty_visual_data
from local import ui_theme
from local.island_stack_visual import IslandStackVisual
from local.music_surface_palette import MusicSurfacePalette, solid_album_background_color, solid_album_ink_color


class IslandBackgroundSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.visual = IslandStackVisual(data_provider=EmptyIslandDataProvider(empty_visual_data()))
        self.visual.timer.stop()
        self.visual.location_mode = "free"
        self.visual.finder_mode = False
        self.visual.music_surface_transition_mode = ""
        self.visual.music_surface_transition_started_at = 0.0

    def tearDown(self):
        self.visual.close()
        self.visual.deleteLater()
        self.app.processEvents()

    def _cover(self, color):
        image = QImage(72, 72, QImage.Format.Format_ARGB32)
        image.fill(QColor(color))
        self.visual.album_art = image
        # A stale/provider companion must not override the decoded artwork.
        self.visual.album_color = QColor("#16BCFF")
        self.visual.secondary_color = QColor("#E32984")
        self.visual.music_ambience_style = "solid_album"

    def _background(self):
        image = QImage(340, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            self.visual._paint_solid_album_music_background(painter, QRectF(10, 10, 320, 80), 22)
            self.visual._paint_music_surface_transition_wash(painter, QRectF(10, 10, 320, 80), 22, True)
        finally:
            painter.end()
        return image

    def test_system_theme_ignores_forced_application_mode(self):
        with patch.object(ui_theme, "_windows_apps_use_light_theme", return_value=0):
            self.assertTrue(ui_theme.system_dark_mode())
            self.assertFalse(ui_theme.app_dark_mode(lambda key, default=None: "light" if key == "v2_theme_mode" else default))
        with patch.object(ui_theme, "_windows_apps_use_light_theme", return_value=1):
            self.assertFalse(ui_theme.system_dark_mode())
            self.assertTrue(ui_theme.app_dark_mode(lambda key, default=None: "dark" if key == "v2_theme_mode" else default))

    def test_system_selection_resolves_both_native_palettes_and_keeps_canonical_state(self):
        for dark, style, ink in ((False, "pure_white", (0, 0, 0)), (True, "pure_black", (255, 255, 255))):
            with self.subTest(dark=dark), patch("local.island_stack_visual.system_dark_mode", return_value=dark):
                self.visual.set_music_ambience_style("system", notify=False)
                self.assertEqual(self.visual.music_ambience_style, "system")
                self.assertEqual(self.visual._effective_music_ambience_style(), style)
                self.assertEqual(self.visual._music_lyric_text_color().getRgb()[:3], ink)
                self.assertEqual(self.visual._music_meta_text_color().getRgb()[:3], ink)
                self.assertEqual(self.visual._music_control_icon_color().getRgb()[:3], ink)

    def test_legacy_styles_migrate_at_runtime_boundary(self):
        for old, expected in (("pure_black", "system"), ("pure_white", "system"), ("duotone_full", "sea_level")):
            with self.subTest(old=old), patch("local.island_stack_visual.system_dark_mode", return_value=False):
                self.visual.set_music_ambience_style(old, notify=False)
                self.assertEqual(self.visual.music_ambience_style, expected)
        self.assertEqual(set(self.visual.MUSIC_AMBIENCE_STYLES), {"sea_level", "solid_album", "system"})

    def test_system_theme_poll_is_bounded_and_native_theme_change_forces_refresh(self):
        with patch("local.island_stack_visual.system_dark_mode", side_effect=[False, True, False]) as reader:
            with patch("local.island_stack_visual.time.monotonic", return_value=100.0):
                self.visual.set_music_ambience_style("system", notify=False)
                for _ in range(10):
                    self.visual._effective_music_ambience_style()
            with patch("local.island_stack_visual.time.monotonic", return_value=100.5):
                self.assertFalse(self.visual._sync_music_background_to_theme())
            self.assertEqual(reader.call_count, 1)
            with patch("local.island_stack_visual.time.monotonic", return_value=102.1):
                self.assertTrue(self.visual._sync_music_background_to_theme())
                self.assertEqual(self.visual._effective_music_ambience_style(), "pure_black")
            with patch("local.island_stack_visual.time.monotonic", return_value=102.2):
                self.assertTrue(self.visual._sync_music_background_to_theme(force=True))
                self.assertEqual(self.visual._effective_music_ambience_style(), "pure_white")
            self.assertEqual(reader.call_count, 3)

    def test_explicit_system_is_not_rewritten_by_app_or_taskbar_follow(self):
        saved = []
        self.visual.settings_setter = lambda key, value: saved.append((key, value))
        self.visual.settings_getter = lambda key, default=None: {
            "v2_music_background_follow_theme": True,
            "v2_tasker_follow_taskbar_color": True,
            "v2_theme_mode": "light",
        }.get(key, default)
        self.visual.music_background_follow_previous_style = ""
        self.visual.taskbar_material_is_light = True
        with patch("local.island_stack_visual.system_dark_mode", return_value=True):
            self.visual.set_music_ambience_style("system", notify=False)
            self.assertFalse(self.visual._sync_music_background_to_taskbar_color(force=True))
            self.visual._sync_music_background_to_theme(force=True)
        self.assertEqual(self.visual.music_ambience_style, "system")
        self.assertEqual(self.visual._effective_music_ambience_style(), "pure_black")
        self.assertEqual(saved, [])

    def test_flat_background_preserves_bright_dark_neutral_and_saturated_cover_tones(self):
        for tone in ("#FFFFFF", "#000000", "#E3DDCC", "#20282D", "#CF346A", "#1966DA"):
            with self.subTest(tone=tone):
                self._cover(tone)
                self.assertEqual(self.visual._solid_album_background_color().name().upper(), tone)

    def test_solid_background_uses_dominant_tone_instead_of_distinct_secondary(self):
        self._cover("#1966DA")
        with QPainter(self.visual.album_art) as painter:
            painter.fillRect(0, 54, 72, 18, QColor("#B46E34"))
        self.assertEqual(self.visual._solid_album_background_color().name().upper(), "#1966DA")

    def test_palette_cache_detects_in_place_artwork_change(self):
        self._cover("#CF346A")
        first = self.visual._solid_album_surface_palette()
        self.assertIs(first, self.visual._solid_album_surface_palette())
        self.visual.album_art.fill(QColor("#E3DDCC"))
        self.assertEqual(self.visual._solid_album_background_color().name().upper(), "#E3DDCC")
        self.assertIsNot(first, self.visual._solid_album_surface_palette())

    def test_solid_background_has_no_sheen_reactive_gradient_or_transition_wash(self):
        self._cover("#CF346A")
        self.visual._music_reactive_style_response = Mock(return_value={"solid_accent_alpha": 1.0, "energy": 1.0})
        self.visual.music_surface_transition_mode = "white_bridge"
        self.visual.music_surface_transition_started_at = 1.0
        image = self._background()
        for x, y in ((40, 25), (170, 25), (300, 25), (40, 50), (170, 50), (300, 75)):
            self.assertEqual(image.pixelColor(x, y).getRgb(), (207, 52, 106, 255))

    def test_dual_panel_layout_keeps_one_flat_color_across_both_regions(self):
        self._cover("#E3DDCC")
        image = QImage(340, 140, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            self.visual._paint_dual_panel_music_background(painter, QRectF(10, 10, 320, 120), 22, "solid_album")
        finally:
            painter.end()
        for x, y in ((40, 30), (170, 50), (300, 100), (170, 118)):
            self.assertEqual(image.pixelColor(x, y).getRgb(), (227, 221, 204, 255))

    def test_solid_ink_and_svg_controls_follow_background_contrast(self):
        for tone, ink, suffix in (("#FFFFFF", (0, 0, 0), ""), ("#000000", (255, 255, 255), " (1)")):
            with self.subTest(tone=tone):
                self._cover(tone)
                self.assertEqual(self.visual._music_lyric_text_color(255).getRgb()[:3], ink)
                self.assertEqual(self.visual._music_lyric_base_text_color(82).getRgb(), (*ink, 82))
                self.assertEqual(self.visual._music_meta_text_color(240).getRgb(), (*ink, 240))
                self.assertTrue(str(self.visual._music_control_icon_spec("music:previous")["path"]).endswith(f"上一首{suffix}.svg"))

    def test_custom_fallback_preserves_neutral_and_unsaturated_selection(self):
        for tone in ("#FFFFFF", "#000000", "#B8B9BB", "#A6D1DA"):
            with self.subTest(tone=tone):
                self.visual.set_solid_album_background_fallback(tone, notify=False)
                self.assertEqual(self.visual.solid_album_background_fallback, tone)

    def test_shared_helpers_copy_source_and_select_the_better_contrast_ink(self):
        tone = QColor(227, 221, 204, 100)
        palette = MusicSurfacePalette(QColor("#CF346A"), QColor("#B46E34"), QColor("#E0CB24"), (tone,))
        background = solid_album_background_color(palette)
        self.assertEqual(background.getRgb(), (227, 221, 204, 255))
        self.assertEqual(tone.alpha(), 100)
        self.assertEqual(solid_album_ink_color(background, 190).getRgb(), (0, 0, 0, 190))
        self.assertEqual(solid_album_ink_color(QColor("#20282D"), 246).getRgb(), (255, 255, 255, 246))


if __name__ == "__main__":
    unittest.main()
