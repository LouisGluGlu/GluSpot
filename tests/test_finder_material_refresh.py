"""Actual material changes, self-sampling and expanded Finder pixel regressions."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from local.island_stack_visual import IslandKind, SpringRect, StackVisual
from local.music_surface_palette import MusicSurfacePalette, future_lyric_color, relative_luminance
from tests import test_finder_bar_surface as finder_fixture


class FinderMaterialRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        finder_fixture.FinderBarSurfaceTests.setUpClass()

    def setUp(self):
        self.case = finder_fixture.FinderBarSurfaceTests()
        self.case.setUp()
        self.case.presence()
        self.visual = self.case.visual
        self.visual.finder_material = replace(self.visual.finder_material,
            mid_color=QColor(180, 180, 180), source="fixture; screen sample")
        self.visual.finder_material_refreshed_at = 99.

    def tearDown(self):
        self.case.tearDown()

    def material(self, color):
        return replace(self.visual.finder_material, mid_color=QColor(color), source="fixture; screen sample")

    def test_visible_color_change_refreshes_within_quarter_second_without_discovery(self):
        visual = self.visual
        dark = self.material("#060909")
        with patch.object(visual, "_finder_bar_screen", return_value=self.case.screen), \
                patch("local.island_stack_visual.refresh_live_finder_bar", return_value=self.case.bar), \
                patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.case.screen]), \
                patch("local.island_stack_visual.read_finder_material", return_value=dark) as sampler:
            visual._refresh_finder_bar_follow_position(now=100.)
            visual._refresh_finder_bar_follow_position(now=100.1)
            visual._refresh_finder_bar_follow_position(now=100.26)
        self.assertEqual(sampler.call_count, 2)
        self.assertEqual(visual._finder_bar_backdrop(), QColor("#060909"))
        self.assertEqual(sampler.call_args.kwargs["trusted_bar_height"], 24.)
        self.assertGreater(visual._finder_bar_ink().lightness(), 180)

    def test_hidden_bar_does_not_sample_and_failed_sample_retains_last_tone(self):
        visual = self.visual
        fallback = replace(visual.finder_material, mid_color=QColor("#eeeeee"), source="fallback")
        with patch.object(visual, "_finder_bar_screen", return_value=self.case.screen), \
                patch("local.island_stack_visual.read_finder_material", return_value=fallback) as sampler:
            visual.finder_bar_visible = False
            visual._refresh_finder_bar_material(now=100.)
            sampler.assert_not_called()
            visual.finder_bar_visible = True
            previous = visual._finder_bar_backdrop()
            visual._refresh_finder_bar_material(now=101.)
            self.assertEqual(visual._finder_bar_backdrop(), previous)
            self.assertEqual(visual.finder_material_refreshed_at, 101.)

    def test_self_exclusion_uses_actual_footprint_not_whole_transparent_window(self):
        visual = self.visual
        visual.isVisible = lambda: True
        shell = QRectF(120., 0., 300., 24.)
        visual._build_visuals = lambda: [SimpleNamespace(rect=shell)]
        visual.motions = {"music": SpringRect(shell)}
        visual.motions["music"].opacity = 1.
        exclusions = visual._finder_material_sample_exclusions()
        self.assertTrue(exclusions)
        self.assertTrue(all(rect.width() == 304. for rect in exclusions))
        self.assertTrue(all(rect.left() == -1802. for rect in exclusions))
        self.assertTrue(all(rect.width() < visual.width() for rect in exclusions))

    def test_expanded_material_color_eases_and_retargets_without_jumping(self):
        visual = self.visual
        initial = visual._finder_bar_backdrop()
        dark = self.material("#060909")
        with patch.object(visual, "_finder_bar_screen", return_value=self.case.screen), \
                patch("local.island_stack_visual.read_finder_material", return_value=dark):
            visual._refresh_finder_bar_material(now=100.)
        self.assertEqual(visual._finder_material_display_color(100.), initial)
        halfway = visual._finder_material_display_color(100.225)
        self.assertGreater(halfway.red(), 6)
        self.assertLess(halfway.red(), 180)
        self.assertEqual(visual._finder_material_display_color(100.45), QColor("#060909"))
        with patch.object(visual, "_finder_bar_screen", return_value=self.case.screen), \
                patch("local.island_stack_visual.read_finder_material", return_value=dark):
            visual._refresh_finder_bar_material(now=100.25)
        self.assertEqual(visual._finder_material_transition_until, 100.45)
        light = self.material("#e0e4e5")
        with patch.object(visual, "_finder_bar_screen", return_value=self.case.screen), \
                patch("local.island_stack_visual.read_finder_material", return_value=light):
            visual._refresh_finder_bar_material(now=100.275)
        self.assertEqual(visual._finder_material_display_color(100.275), visual._finder_material_transition_from)
        self.assertTrue(visual._active_visual_animation_plan(100.3).active)

    def test_expanded_shell_has_no_stretched_texture_ridge_or_inferred_border(self):
        visual = self.visual
        visual.expanded = True
        visual._group_frame_should_snap_to_target = lambda *_args: True
        texture = QImage(128, 24, QImage.Format.Format_ARGB32)
        with QPainter(texture) as painter:
            for y in range(24):
                painter.fillRect(0, y, 128, 1, QColor(20 if y % 2 else 230, 20, 20))
        visual.finder_material.texture_image = texture
        visual.finder_material.has_border = True
        visual.finder_material.border_width = 20.
        shell = QRectF(70., 24., 440., 160.)
        motion = SpringRect(shell)
        motion.opacity = 1.
        items = [(StackVisual("music", IslandKind.MUSIC, shell, False, None, 10), motion)]
        image = QImage(600, 240, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            visual._paint_myfinder_group_frame(painter, items, 1.)
        color = visual._finder_bar_backdrop()
        frame = visual.group_frame_mask_path.boundingRect()
        for y in range(round(frame.top()) + 5, round(frame.bottom()) - 5):
            self.assertEqual(image.pixelColor(round(frame.center().x()), y), color)
        self.assertTrue(visual.finder_hover_rect.contains(shell.center()))

    def test_fade_keeps_cover_lane_clear_and_metadata_on_solid_color(self):
        visual = self.visual
        visual.finder_bar_style = "fade"
        surface = QRectF(0., 0., 360., 24.)
        image = QImage(360, 24, QImage.Format.Format_ARGB32)
        image.fill(visual._finder_bar_backdrop())
        lyrics = []
        with patch.object(visual, "_paint_compact_lyric_viewport", side_effect=lambda _p, rect: lyrics.append(rect)), \
                QPainter(image) as painter:
            visual._paint_finder_bar_music(painter, surface)
        self.assertEqual(image.pixelColor(3, 12), visual._finder_bar_backdrop())
        self.assertGreaterEqual(lyrics[0].left(), visual.music_cover_rect.right() + 24.)
        self.assertAlmostEqual(visual._finder_bar_fade_opacity(surface, lyrics[0].left()), 222. / 255.)
        self.assertTrue(surface.contains(visual.music_cover_rect))

    def test_transparent_ring_uses_finder_tone_and_colored_styles_use_song(self):
        visual = self.visual
        a = MusicSurfacePalette(QColor("#703020"), QColor("#404040"), QColor("#b76532"))
        b = MusicSurfacePalette(QColor("#204070"), QColor("#404040"), QColor("#4b82db"))
        visual._solid_album_surface_palette = lambda: a
        visual.finder_bar_style = "transparent"
        first = visual._personal_dj_island_color()
        visual._solid_album_surface_palette = lambda: b
        self.assertEqual(visual._personal_dj_island_color(), first)
        background = visual._finder_bar_backdrop()
        expected = future_lyric_color(background, background,
            use_light=relative_luminance(background) < .11, primary=background)
        self.assertEqual(first, expected)
        for style in ("capsule", "fade"):
            visual.finder_bar_style = style
            visual._solid_album_surface_palette = lambda: a
            first = visual._personal_dj_island_color()
            visual._solid_album_surface_palette = lambda: b
            self.assertNotEqual(visual._personal_dj_island_color(), first)


if __name__ == "__main__":
    unittest.main()
