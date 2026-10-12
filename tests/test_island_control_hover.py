from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.island_stack_visual import IslandStackVisual


class IslandControlHoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.visual = IslandStackVisual.__new__(IslandStackVisual)
        self.visual._u = float
        self.visual.hovered_key = ""
        self.visual.drag_candidate = self.visual.dragging = False
        self.visual.drag_press_key = ""
        self.visual._music_control_elastic_scale = lambda command: 1.0
        self.visual._music_control_fill_color = lambda alpha: QColor(18, 20, 24, alpha)
        self.visual._paint_music_control_svg_icon = lambda painter, command, rect: True
        self.visual.music_button_rects = {
            "music:previous": QRectF(15, 10, 36, 36),
            "music:toggle_play_pause": QRectF(65, 10, 36, 36),
            "music:next": QRectF(115, 10, 36, 36),
        }
        self.visual._island_music_control_base_rects = dict(self.visual.music_button_rects)
        self.image = QImage(180, 64, QImage.Format.Format_ARGB32_Premultiplied)
        self.image.fill(Qt.GlobalColor.transparent)

    def test_all_music_buttons_have_half_second_hover_and_matching_visible_hit(self):
        for command in self.visual.music_button_rects:
            with self.subTest(command=command):
                self.visual._island_music_control_hover_motions = {}
                self.visual.hovered_key = command
                base = self.visual._island_music_control_base_rects[command]
                self.visual._set_island_music_control_hover(command, 100.0)
                with patch("local.island_stack_visual.time.monotonic", return_value=100.5), \
                        patch("local.island_stack_visual.paint_liquid_glass") as paint:
                    with QPainter(self.image) as painter:
                        self.visual._paint_music_control_button(painter, base, command, 36, 13)
                shown = paint.call_args.args[1]
                self.assertAlmostEqual(shown.width(), base.width() * 1.10)
                self.assertEqual(self.visual.music_button_rects[command], shown)
                self.assertAlmostEqual(paint.call_args.kwargs["radius"], 13 * 1.10)
                self.assertEqual(paint.call_args.kwargs["base_color"], QColor(18, 20, 24, 36))

    def test_neighbors_move_apart_then_return_without_modifying_layout_bases(self):
        bases = dict(self.visual._island_music_control_base_rects)
        self.visual.hovered_key = "music:previous"
        self.visual._advance_island_music_control_hover(100.0)
        for index in range(1, 51):
            self.visual._advance_island_music_control_hover(100.0 + index * .01)
        near = self.visual._island_music_control_hover_rect("music:toggle_play_pause", bases["music:toggle_play_pause"], 100.5)
        far = self.visual._island_music_control_hover_rect("music:next", bases["music:next"], 100.5)
        self.assertGreater(near.center().x() - bases["music:toggle_play_pause"].center().x(),
                           far.center().x() - bases["music:next"].center().x())
        self.assertGreater(far.center().x(), bases["music:next"].center().x())
        self.visual.hovered_key = ""
        self.visual._advance_island_music_control_hover(100.5)
        for index in range(1, 101):
            self.visual._advance_island_music_control_hover(100.5 + index * .01)
        restored = self.visual._island_music_control_hover_rect("music:toggle_play_pause", bases["music:toggle_play_pause"], 101.5)
        self.assertAlmostEqual(restored.center().x(), bases["music:toggle_play_pause"].center().x(), delta=.01)
        self.assertEqual(self.visual._island_music_control_base_rects, bases)

    def test_existing_shell_clip_contains_hover_and_its_hit_shape(self):
        command, base = "music:next", QRectF(40, 10, 36, 36)
        self.visual.hovered_key = command
        self.visual._set_island_music_control_hover(command, 100.0)
        self.visual._island_music_control_neighbor_offsets = {command: QPointF(10, 0)}
        with patch("local.island_stack_visual.time.monotonic", return_value=100.5), \
                patch("local.island_stack_visual.paint_liquid_glass") as paint:
            with QPainter(self.image) as painter:
                painter.setClipRect(QRectF(0, 0, 80, 60))
                self.visual._paint_music_control_button(painter, base, command, 36, 13)
        shown = paint.call_args.args[1]
        self.assertLessEqual(shown.right(), 79.0)
        self.assertEqual(self.visual.music_button_rects[command], shown)
        self.assertAlmostEqual(shown.width(), base.width() * 1.10)


if __name__ == "__main__":
    unittest.main()
