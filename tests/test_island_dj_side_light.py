from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.models import AudioFrame
from local.music_surface_palette import MusicSurfacePalette
from local.personal_dj_icon import paint_personal_dj_icon, personal_dj_ring_poses
from tests import test_island_lyric_follow_geometry as geometry_fixture


class IslandDjSideLightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def visual(self):
        visual = geometry_fixture.IslandLyricFollowGeometryTests._visual()
        visual.expanded = False
        visual._finder_bar_active = lambda: False
        visual._solid_album_surface_palette = lambda: MusicSurfacePalette(
            QColor("#151a29"), QColor("#282b30"), QColor("#b96b37"))
        return visual

    def test_every_ring_keeps_characteristic_left_and_background_right(self):
        rect = QRectF(20, 20, 280, 160)
        characteristic = QColor("#b96b37")
        for backdrop in (QColor("#000000"), QColor("#ffffff"), QColor("#808080"), QColor("#18354b")):
            for elapsed in (0., .28, .85, 2.2):
                for pose in personal_dj_ring_poses(elapsed):
                    with self.subTest(backdrop=backdrop.name(), elapsed=elapsed, identity=pose[0]):
                        image = QImage(320, 200, QImage.Format.Format_ARGB32_Premultiplied)
                        image.fill(backdrop)
                        with patch("local.personal_dj_icon.personal_dj_ring_poses", return_value=(pose,)), \
                                QPainter(image) as painter, patch.object(painter, "setPen", wraps=painter.setPen) as pens:
                            paint_personal_dj_icon(painter, rect, characteristic, backdrop=backdrop,
                                elapsed=elapsed, bands=AudioFrame(.2, .1, .05))
                            stops = pens.call_args.args[0].brush().gradient().stops()
                        self.assertEqual(stops[0][1].getRgb()[:3], characteristic.getRgb()[:3])
                        self.assertEqual(stops[-1][1].getRgb()[:3], backdrop.getRgb()[:3])
                        for _position, color in stops[1:-1]:
                            for value, first, last in zip(color.getRgb()[:3], characteristic.getRgb()[:3], backdrop.getRgb()[:3]):
                                self.assertGreaterEqual(value, min(first, last))
                                self.assertLessEqual(value, max(first, last))

    def test_native_pixels_merge_right_rim_into_black_white_gray_and_colored_backdrops(self):
        rect = QRectF(30, 20, 240, 160)
        pose = (2, .5, .94, 1.)
        for backdrop in (QColor("#000000"), QColor("#ffffff"), QColor("#808080"), QColor("#18354b")):
            with self.subTest(backdrop=backdrop.name()):
                image = QImage(300, 200, QImage.Format.Format_RGB32)
                image.fill(backdrop)
                with patch("local.personal_dj_icon.personal_dj_ring_poses", return_value=(pose,)), QPainter(image) as painter:
                    paint_personal_dj_icon(painter, rect, QColor("#b96b37"), backdrop=backdrop)
                def distance(left, right):
                    return max(max(abs(a - b) for a, b in zip(image.pixelColor(x, y).getRgb()[:3], backdrop.getRgb()[:3]))
                        for x in range(left, right) for y in range(94, 107))
                left, right = distance(108, 127), distance(180, 193)
                self.assertGreater(left, 45)
                self.assertLess(right, left * .15)
                self.assertEqual(image.pixelColor(150, 100), backdrop)

    def test_live_snapshot_samples_each_ring_right_side_in_logical_coordinates(self):
        visual = self.visual()
        image = QImage(200, 160, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(2.)
        image.fill(QColor("#203040"))
        with QPainter(image) as painter:
            painter.fillRect(QRectF(50, 0, 50, 80), QColor("#90a0b0"))
        visual._audio_device_background_image = image
        visual._audio_device_background_rect = QRectF(-80, -60, 100, 80)
        visual._island_dj_background_ready = True
        with patch.object(visual, "_solid_album_surface_palette", side_effect=AssertionError("snapshot must win")):
            self.assertEqual(visual._personal_dj_island_backdrop(QRectF(-75, -55, 20, 40)), QColor("#203040"))
            self.assertEqual(visual._personal_dj_island_backdrop(QRectF(-30, -55, 20, 40)), QColor("#90a0b0"))
        visual._island_dj_background_ready = False
        self.assertEqual(visual._personal_dj_island_backdrop(QRectF(-30, -55, 20, 40)), QColor("#151a29"))

    def test_finder_compact_background_wins_over_old_shell_snapshot(self):
        visual = self.visual()
        visual._finder_bar_active = lambda: True
        visual.finder_material = Mock(mid_color=QColor("#dde1e7"))
        visual._island_dj_background_ready = True
        for style in ("transparent", "capsule", "fade"):
            with self.subTest(style=style):
                visual.finder_bar_style = style
                self.assertEqual(visual._personal_dj_island_backdrop(QRectF(5, 0, 18, 24)), visual._finder_bar_effective_backdrop())

    def test_finder_faded_edge_uses_ring_location_instead_of_center_pigment(self):
        visual = self.visual()
        visual._finder_bar_active = lambda: True
        visual.finder_material = Mock(mid_color=QColor("#dde1e7"))
        visual.finder_bar_style = "fade"
        surface = QRectF(40, 0, 500, 24)
        edge = visual._personal_dj_island_backdrop(QRectF(40, 0, 45, 24), surface_rect=surface)
        center = visual._personal_dj_island_backdrop(QRectF(270, 0, 20, 24), surface_rect=surface)
        base = visual._finder_bar_backdrop()
        def distance(color):
            return sum(abs(a - b) for a, b in zip(color.getRgb()[:3], base.getRgb()[:3]))
        self.assertLess(distance(edge), distance(center) * .51)
        self.assertGreater(distance(edge), distance(center) * .49)

    def test_speech_background_is_painted_once_and_same_frame_ring_source_is_reused(self):
        visual = self.visual()
        visual._island_dj_speaking = True
        visual._island_dj_title = "Next song"
        visual._island_dj_artist = "Next singer"
        visual.data_provider = Mock()
        image = QImage(440, 112, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        backgrounds = []
        ring_colors = []
        def step(name, painter, rect, radius, expanded, style):
            if name == "solid_album_background":
                backgrounds.append(name)
                painter.fillRect(rect, QColor("#42556b"))
        def ring(painter, rect, color, **kwargs):
            ring_colors.append(kwargs["backdrop"](rect))
        with patch.object(visual, "_record_visual_paint_section"), \
                patch.object(visual, "_execute_music_surface_paint_step", side_effect=step), \
                patch("local.island_stack_visual.paint_personal_dj_icon", side_effect=ring):
            for _frame in range(2):
                with QPainter(image) as painter:
                    visual._paint_music(painter, QRectF(0, 0, 440, 112), False)
                buffer = visual._audio_device_background_image
                self.assertLessEqual(buffer.sizeInBytes(), 440 * 112 * 4)
                if _frame:
                    self.assertIs(buffer, previous_buffer)
                previous_buffer = buffer
                self.assertFalse(visual._island_dj_background_ready)
        self.assertEqual(backgrounds, ["solid_album_background"] * 2)
        self.assertEqual(ring_colors, [QColor("#42556b")] * 2)
        visual.data_provider.get_personal_dj_presentation.assert_not_called()

    def test_completed_intro_morph_uses_same_background_blend(self):
        visual = self.visual()
        visual._island_dj_outro_started = 100.
        visual._island_dj_outro_until = 102.32
        visual._normalize_music_expanded_layout_style = lambda value: "standard"
        image = QImage(580, 240, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch("local.island_stack_visual.time.monotonic", return_value=100.16), \
                patch.object(visual, "_paint_interactive_music_cover"), \
                patch.object(visual, "_paint_music_control_button"), \
                patch.object(visual, "_paint_lyric_viewport"), \
                patch("local.island_stack_visual.paint_personal_dj_icon") as rings, QPainter(image) as painter:
            visual._paint_music_expanded(painter, QRectF(0, 0, 580, 240))
        self.assertEqual(rings.call_args.kwargs["backdrop"](rings.call_args.args[1]), QColor("#151a29"))

    def test_click_feedback_survives_completion_morph_and_normal_expanded_return(self):
        visual = self.visual()
        visual._island_dj_outro_started = 100.
        visual._island_dj_outro_until = 102.32
        visual._island_button_ripples = [object()]
        visual._normalize_music_expanded_layout_style = lambda value: "standard"
        shell = QRectF(0, 0, 580, 240)
        image = QImage(580, 240, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        for now in (100.16, 100.5):
            with self.subTest(now=now):
                order = []
                visual._island_glow_paint_now = now
                def step(name, *_args):
                    if name == "expanded_content":
                        order.append("content")
                with patch("local.island_stack_visual.time.monotonic", return_value=now), \
                        patch.object(visual, "_record_visual_paint_section"), \
                        patch.object(visual, "_execute_music_surface_paint_step", side_effect=step), \
                        patch.object(visual, "_paint_music_expanded", side_effect=lambda *_args: order.append("content")), \
                        patch.object(visual, "_paint_island_button_ripples", side_effect=lambda *_args, **_kwargs: order.append("feedback")) as ripples, \
                        QPainter(image) as painter:
                    visual._paint_music(painter, shell, True)
                self.assertEqual(order, ["feedback", "content"])
                ripples.assert_called_once()
                self.assertEqual(ripples.call_args.args[1], now)
                self.assertEqual(ripples.call_args.kwargs["owner_key"], "music")
                self.assertEqual(ripples.call_args.kwargs["shell_rect"], shell)
                self.assertEqual(ripples.call_args.kwargs["shell_path"].boundingRect(), shell)


if __name__ == "__main__":
    unittest.main()
