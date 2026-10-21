"""Finder and Tasker share compact materials without changing bar ownership."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.music_surface_palette import relative_luminance
from tests import test_finder_bar_surface as finder_fixture


class ImmersiveBarStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp = finder_fixture.FinderBarSurfaceTests.setUp
    tearDown = finder_fixture.FinderBarSurfaceTests.tearDown

    def tasker(self):
        visual = self.visual
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.mydockfinder_running = False
        visual.expanded = False
        visual.hovered_key = ""
        visual.taskbar_material_color = QColor("#30343a")
        visual.taskbar_follow_base_rect = QRectF(0, 1040, 1920, 40)
        visual.taskbar_selected_drop_zone = QRectF()
        visual._taskbar_height = lambda: 40.
        return visual

    def render(self, rect=QRectF(0, 0, 420, 40), dpr=1., paint_rect=None):
        image = QImage(round(rect.right() * dpr), round(rect.bottom() * dpr),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        with patch("local.island_stack_visual.time.monotonic", return_value=100.), QPainter(image) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.visual._paint_music(painter, rect if paint_rect is None else paint_rect, False)
        return image

    def test_tasker_three_materials_preserve_cover_timed_lyrics_and_real_taskbar_bounds(self):
        visual = self.tasker()
        selected = QRectF(200, 1040, 500, 40)
        before_size = visual._taskbar_compact_music_size(420., 40., selected_zone=selected)
        for dpr in (1., 2.):
            for style in ("transparent", "fade", "capsule"):
                with self.subTest(dpr=dpr, style=style):
                    visual.set_finder_bar_style(style, notify=False)
                    lyrics = []
                    rect = QRectF(0, 0, *before_size)
                    with patch.object(visual, "_paint_compact_lyric_viewport", wraps=visual._paint_compact_lyric_viewport) as reading:
                        image = self.render(rect, dpr)
                    lyrics = [call.args[1] for call in reading.call_args_list]
                    self.assertEqual(len(lyrics), 1)
                    content = visual._immersive_bar_content_rect(rect)
                    self.assertEqual(content, rect.adjusted(0., 5., 0., -5.))
                    self.assertTrue(content.contains(lyrics[0]))
                    self.assertTrue(content.contains(visual.music_cover_rect))
                    self.assertEqual(visual._hit_test(visual.music_cover_rect.center()), "music:open_surface")
                    self.assertEqual(visual._taskbar_compact_music_size(420., 40., selected_zone=selected), before_size)
                    self.assertEqual(visual.settings_snapshot()["v2_finder_bar_style"], style)
                    x, y = round(rect.center().x() * dpr), round((content.top() + 2.) * dpr)
                    self.assertEqual(image.pixelColor(x, y).alpha(), 0 if style == "transparent" else 222)
                    self.assertEqual(image.pixelColor(x, round(2 * dpr)).alpha(), 0)
                    if style == "fade":
                        self.assertEqual(image.pixelColor(round(3 * dpr), round(rect.center().y() * dpr)).alpha(), 0)
                        self.assertGreaterEqual(lyrics[0].left() - visual.music_cover_rect.right(), 24. - 1.e-6)

    def test_same_content_dimensions_and_native_tone_produce_same_finder_tasker_material(self):
        visual = self.tasker()
        visual.finder_bar_rect = QRectF(0, 0, 1920, 40)
        visual.finder_bar_enabled = True
        visual.finder_material.mid_color = QColor(visual.taskbar_material_color)
        # Isolate background+cover from the two placements' reading policies.
        with patch.object(visual, "_paint_compact_lyric_viewport"):
            for style in ("transparent", "fade", "capsule"):
                for dpr in (1., 2.):
                    with self.subTest(style=style, dpr=dpr):
                        visual.set_finder_bar_style(style, notify=False)
                        tasker = self.render(dpr=dpr)
                        content = visual._immersive_bar_content_rect(QRectF(0, 0, 420, 40))
                        visual.location_mode, visual.finder_mode = "finder", True
                        visual.mydockfinder_running = True
                        finder = self.render(dpr=dpr, paint_rect=content)
                        self.assertEqual(tasker, finder)
                        visual.location_mode, visual.finder_mode = "tasker", False
                        visual.mydockfinder_running = False

    def test_tasker_content_inset_does_not_change_native_row_or_short_spring_validity(self):
        visual = self.tasker()
        native = QRectF(0, 1040, 1920, 40)
        full = QRectF(20, 0, 420, 40)
        for height in (40., 30., 20., 8., 1.):
            with self.subTest(height=height):
                outer = QRectF(full.left(), full.top(), full.width(), height)
                before = QRectF(outer)
                content = visual._immersive_bar_content_rect(outer)
                self.assertEqual(outer, before)
                self.assertTrue(outer.contains(content))
                self.assertGreater(content.height(), 0.)
                self.assertEqual(content.center(), outer.center())
                self.assertEqual(content.width(), outer.width())
                self.assertEqual(visual.taskbar_follow_base_rect, native)
        visual.location_mode, visual.finder_mode = "finder", True
        visual.finder_bar_enabled = True
        visual.finder_bar_rect = QRectF(0, 0, 1920, 24)
        self.assertEqual(visual._immersive_bar_content_rect(full), full)

    def test_tasker_spare_vertical_row_keeps_drag_hit_without_expanding_cover_action(self):
        visual = self.tasker()
        outer = QRectF(0, 0, 420, 40)
        visual.taskbar_selected_drop_zone = QRectF(outer)
        with patch.object(visual, "mapToGlobal", return_value=QPoint(0, 0)):
            self.render(outer)
            cover = QRectF(visual.music_cover_rect)
            top_gap = QPointF(cover.center().x(), outer.top() + 2.)
            self.assertFalse(cover.contains(top_gap))
            self.assertEqual(visual._hit_test(top_gap), "music")
            self.assertEqual(visual._hit_test(cover.center()), "music:open_surface")

    def test_tasker_styles_keep_hover_transport_and_narrow_zone_play_hit_targets(self):
        visual = self.tasker()
        for style in ("transparent", "fade", "capsule"):
            with self.subTest(style=style):
                visual.set_finder_bar_style(style, notify=False)
                visual.hovered_key = "music"
                self.render()
                self.assertEqual(set(visual.music_button_rects), {
                    "music:previous", "music:toggle_play_pause", "music:next"})
                for command, button in visual.music_button_rects.items():
                    self.assertEqual(visual._hit_test(button.center()), command)
                visual.hovered_key = ""
                narrow = QRectF(0, 0, 58, 34)
                self.render(narrow)
                self.assertTrue(narrow.contains(visual.music_cover_rect))
                self.assertEqual(set(visual.music_button_rects), {"music:toggle_play_pause"})
                button = visual.music_button_rects["music:toggle_play_pause"]
                self.assertTrue(narrow.contains(button))
                self.assertEqual(visual._hit_test(button.center()), "music:toggle_play_pause")

    def test_tasker_transparent_contrast_uses_cached_native_tone_without_paint_capture(self):
        visual = self.tasker()
        visual.finder_material.mid_color = QColor("#fbfbfb")
        for level in (12, 128, 245):
            visual.taskbar_material_color = QColor(level, level, level)
            self.assertEqual(visual._finder_bar_backdrop(), QColor(level, level, level))
            ink = visual._music_lyric_text_color(255)
            self.assertEqual(ink.getRgb()[:3], visual._music_lyric_base_text_color().getRgb()[:3])
            lum = sorted((relative_luminance(ink), relative_luminance(visual.taskbar_material_color)))
            self.assertGreaterEqual((lum[1] + .05) / (lum[0] + .05), 4.5)
            with patch.object(visual, "_sample_taskbar_material_color", side_effect=AssertionError("paint must use cache")), \
                    patch("local.island_stack_visual.read_finder_material", side_effect=AssertionError("paint must use cache")):
                self.render()
        visual.taskbar_material_color = QColor()
        self.assertEqual(visual._finder_bar_backdrop(), QColor(34, 38, 46))

    def test_unattached_tasker_retains_system_theme_until_native_bounds_are_known(self):
        visual = self.tasker()
        visual.taskbar_follow_base_rect = QRectF()
        visual.last_taskbar_rect = QRectF()
        visual.taskbar_selected_drop_zone = QRectF()
        visual.music_ambience_style = "system"
        visual._music_system_dark = False
        for style in ("transparent", "fade", "capsule"):
            with self.subTest(style=style):
                visual.set_finder_bar_style(style, notify=False)
                self.assertFalse(visual._immersive_bar_compact_active())
                self.assertEqual(visual._music_lyric_text_color().getRgb()[:3], (0, 0, 0))
                self.assertEqual(visual._music_lyric_base_text_color().getRgb()[:3], (0, 0, 0))
                with patch.object(visual, "_paint_finder_bar_music", side_effect=AssertionError("native bounds required")), \
                        patch.object(visual, "_execute_music_surface_paint_step") as steps:
                    self.render()
                self.assertIn("compact_content", [call.args[0] for call in steps.call_args_list])
        for name in ("taskbar_follow_base_rect", "last_taskbar_rect", "taskbar_selected_drop_zone"):
            with self.subTest(native_bounds=name):
                setattr(visual, name, QRectF(0, 1040, 1920, 40))
                self.assertTrue(visual._immersive_bar_compact_active())
                setattr(visual, name, QRectF())

    def test_tasker_dj_uses_shared_material_and_single_row_metadata_without_finder_reveal(self):
        visual = self.tasker()
        visual._island_dj_speaking = True
        visual._island_dj_title, visual._island_dj_artist = "Next song", "GluSpot singer"
        visual._finder_dj_reveal_from = visual._finder_dj_reveal_target = 0.
        visual._finder_dj_reveal_at = 100.
        for style in ("transparent", "fade", "capsule"):
            visual.set_finder_bar_style(style, notify=False)
            with patch.object(visual, "_paint_personal_dj_island", wraps=visual._paint_personal_dj_island) as dj, \
                    patch.object(visual, "_paint_compact_lyric_viewport") as lyrics:
                self.render()
            dj.assert_called_once()
            lyrics.assert_not_called()
            self.assertTrue(visual.music_cover_rect.isEmpty())
            self.assertEqual(visual.music_button_rects, {})
        visual.finder_bar_style = "transparent"
        self.assertEqual(visual._personal_dj_island_backdrop(), visual.taskbar_material_color)
        visual.finder_bar_style = "fade"
        surface = QRectF(0, 0, 420, 40)
        self.assertEqual(visual._personal_dj_island_backdrop(QRectF(0, 0, 20, 40), surface_rect=surface),
                         visual.taskbar_material_color)

    def test_tasker_expanded_surface_keeps_existing_main_island_paint_plan(self):
        visual = self.tasker()
        visual.expanded = True
        visual.music_ambience_style = "pure_black"
        before_ink = visual._music_lyric_text_color()
        for style in ("transparent", "fade", "capsule"):
            visual.set_finder_bar_style(style, notify=False)
            with patch.object(visual, "_paint_finder_bar_music", side_effect=AssertionError("compact bars only")), \
                    patch.object(visual, "_execute_music_surface_paint_step") as steps:
                image = QImage(500, 240, QImage.Format.Format_ARGB32_Premultiplied)
                with QPainter(image) as painter:
                    visual._paint_music(painter, QRectF(0, 0, 500, 240), True)
            self.assertIn("expanded_content", [call.args[0] for call in steps.call_args_list])
            self.assertEqual(visual._music_lyric_text_color(), before_ink)


if __name__ == "__main__":
    unittest.main()
