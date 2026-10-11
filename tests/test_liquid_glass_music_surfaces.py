"""Optical-material integration preserves live music controls and motion."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.audio_output_motion import ConnectionPose, connection_capsule_rect, paint_connection_pose
from local.audio_output_status import ConnectedOutputStatus, OutputMember
from local.island_stack_visual import IslandStackVisual
from local.liquid_glass import clear_glass_cache


class MusicOpticalMaterialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _island_fixture(self, light=False):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = float
        visual._music_control_elastic_scale = lambda command: 1.0
        visual._music_control_icon_color = lambda alpha: QColor(16, 18, 22, alpha) if light else QColor(248, 249, 250, alpha)
        visual.hovered_key = ""
        visual.drag_candidate = False
        visual.dragging = False
        visual.drag_press_key = ""
        visual.music_button_rects = {"music:next": QRectF(12, 12, 40, 40)}
        visual.media_position_seconds = 43.5
        visual.media_track_id = "current-song"
        visual._audio_device_background_image = QImage(64, 64, QImage.Format.Format_RGB32)
        visual._audio_device_background_image.fill(QColor(255, 0, 0))

        def glyph(painter, command, rect):
            painter.fillRect(QRectF(rect.center().x() - 3, rect.center().y() - 3, 6, 6),
                             visual._music_control_icon_color(255))
            return True

        visual._paint_music_control_svg_icon = glyph
        return visual

    def _control_image(self, visual, opacity=1.0):
        image = QImage(64, 64, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            painter.setOpacity(opacity)
            before = (painter.opacity(), painter.transform(), painter.clipPath())
            visual._paint_music_control_button(painter, QRectF(12, 12, 40, 40), "music:next", 24, 13)
            self.assertEqual((painter.opacity(), painter.transform(), painter.clipPath()), before)
        return image

    def test_island_keeps_theme_glyph_ink_hit_rect_and_media_clock(self):
        for light in (False, True):
            with self.subTest(light=light):
                visual = self._island_fixture(light)
                hits = dict(visual.music_button_rects)
                image = self._control_image(visual)
                self.assertEqual(image.pixelColor(32, 32), visual._music_control_icon_color(255))
                self.assertEqual(visual.music_button_rects, hits)
                self.assertEqual((visual.media_track_id, visual.media_position_seconds), ("current-song", 43.5))
                self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
                self.assertGreater(image.pixelColor(18, 32).alpha(), 0)

    def test_island_hover_and_press_change_material_without_stale_notice_sampling(self):
        visual = self._island_fixture()
        idle = self._control_image(visual)
        visual.hovered_key = "music:next"
        hovered = self._control_image(visual)
        visual.drag_candidate = True
        visual.drag_press_key = "music:next"
        pressed = self._control_image(visual)
        self.assertNotEqual(idle.pixelColor(18, 32), hovered.pixelColor(18, 32))
        self.assertNotEqual(hovered.pixelColor(18, 32), pressed.pixelColor(18, 32))
        for image in (idle, hovered, pressed):
            material = image.pixelColor(18, 32)
            self.assertLess(material.red(), material.blue() + 10)
            self.assertEqual(image.pixelColor(32, 32), QColor(248, 249, 250))

    def test_island_material_multiplies_enclosing_fade(self):
        image = self._control_image(self._island_fixture(), .2)
        self.assertLessEqual(max(image.pixelColor(x, y).alpha() for y in range(64) for x in range(64)), 100)

    def _pose(self, capsule=1.0):
        member = OutputMember("mini", "HomePod Mini", "homepod-mini")
        return ConnectionPose(ConnectedOutputStatus(), ConnectedOutputStatus((member,)), member,
                              old_opacity=0, new_opacity=1, new_scale=1, blue=1, capsule=capsule)

    def test_connection_material_uses_supplied_scene_preserves_pose_and_painter(self):
        pose = self._pose(.55)
        circle = QRectF(210, 12, 42, 42)
        font = QFont("Segoe UI", 10)
        capsule = connection_capsule_rect(circle, pose, font, 12)
        images = []
        for color in (QColor(145, 40, 45), QColor(40, 70, 145)):
            clear_glass_cache()
            backdrop = QImage(320, 70, QImage.Format.Format_RGB32)
            backdrop.fill(color)
            image = QImage(320, 70, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                painter.setOpacity(.6)
                painter.setClipRect(QRectF(10, 10, 290, 50))
                before = (painter.opacity(), painter.transform(), painter.clipPath())
                paint_connection_pose(painter, circle, pose, font,
                                      backdrop=backdrop, backdrop_rect=capsule)
                self.assertEqual((painter.opacity(), painter.transform(), painter.clipPath()), before)
            images.append(image)
        sample_x = round(capsule.center().x())
        self.assertNotEqual(images[0].pixelColor(sample_x, 17), images[1].pixelColor(sample_x, 17))
        self.assertEqual(pose, self._pose(.55))
        self.assertTrue(all(image.pixelColor(0, 0).alpha() == 0 for image in images))

    def test_connection_motion_geometry_and_glyph_layers_remain_independent(self):
        pose = self._pose(.45)
        image = QImage(320, 70, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        glyphs = []
        with patch("local.audio_output_motion.paint_audio_output_icon",
                   side_effect=lambda painter, rect, kind, color: glyphs.append((QRectF(rect), kind, QColor(color)))), \
                QPainter(image) as painter:
            paint_connection_pose(painter, QRectF(210, 12, 42, 42), pose, QFont("Segoe UI", 10))
        self.assertEqual(len(glyphs), 1)
        self.assertEqual(glyphs[0][1], "homepod-mini")
        self.assertLessEqual(glyphs[0][0].right(), 252)
        self.assertEqual(pose.final_opacity, 0)
        self.assertEqual(pose.target.count, 1)


if __name__ == "__main__":
    unittest.main()
