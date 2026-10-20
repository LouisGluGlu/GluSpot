"""Opt-in scene glass: real blur, mapped crops, bounded live refresh and sharp ink."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local import liquid_glass as glass


class LiquidGlassBackdropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        glass.clear_glass_cache()

    def tearDown(self):
        glass.clear_glass_cache()

    def scene(self, color=QColor('#a43d69')):
        image = QImage(240, 100, QImage.Format.Format_RGB32)
        image.fill(color)
        return image

    def render(self, scene=None, sample=None, **options):
        image = QImage(180, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            glass.paint_liquid_glass(painter, QRectF(20, 20, 140, 60),
                                     backdrop=scene, backdrop_rect=sample,
                                     refractive=True, **options)
        return image

    def test_scene_high_frequency_detail_is_blurred_inside_translucent_face(self):
        scene = self.scene()
        with QPainter(scene) as painter:
            for x in range(scene.width()):
                painter.fillRect(x, 0, 1, scene.height(), QColor('white') if x % 4 < 2 else QColor('black'))
        image = self.render(scene, QRectF(0, 0, 140, 60))
        first, second = image.pixelColor(89, 50), image.pixelColor(91, 50)
        self.assertGreater(first.red(), 70)
        self.assertLess(first.red(), 170)
        self.assertLess(abs(first.red() - second.red()), 10)
        self.assertGreater(first.alpha(), 180)
        self.assertLess(first.alpha(), 230)
        self.assertEqual(image.pixelColor(20, 20).alpha(), 0)
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)

    def test_native_source_rectangle_selects_the_control_local_scene(self):
        scene = self.scene(QColor('red'))
        with QPainter(scene) as painter:
            painter.fillRect(120, 0, 120, 100, QColor('blue'))
        left = self.render(scene, QRectF(10, 10, 80, 60)).pixelColor(90, 50)
        right = self.render(scene, QRectF(150, 10, 80, 60)).pixelColor(90, 50)
        self.assertGreater(left.red(), left.blue() + 150)
        self.assertGreater(right.blue(), right.red() + 150)

    def test_scene_edge_padding_clamps_color_without_transparent_blur_fringe(self):
        scene = self.scene(QColor('#509ec4'))
        image = self.render(scene, QRectF(0, 0, 20, 20))
        center, near_edge = image.pixelColor(90, 50), image.pixelColor(42, 50)
        self.assertEqual(center.alpha(), near_edge.alpha())
        self.assertAlmostEqual(center.blue(), near_edge.blue(), delta=1)

    def test_theme_and_control_states_have_mild_distinct_neutral_veils(self):
        scene = self.scene(QColor('#808080'))
        dark = self.render(scene).pixelColor(90, 50)
        light = self.render(scene, light=True).pixelColor(90, 50)
        self.assertGreater(light.lightness(), dark.lightness() + 40)
        self.assertGreater(dark.lightness(), 65)
        self.assertLess(light.lightness(), 195)
        states = [self.render(scene, light=True, **state).pixelColor(90, 50)
                  for state in ({}, {'hovered': True}, {'pressed': True}, {'active': True})]
        self.assertEqual(len({color.rgba() for color in states}), 4)
        self.assertEqual(glass.glass_material_tint(light=True), QColor(255, 255, 255, 58))
        self.assertEqual(glass.glass_material_tint(pressed=True, active=True), QColor(20, 22, 27, 42))

    def test_missing_scene_has_a_translucent_theme_fallback(self):
        image = self.render(light=True)
        self.assertEqual(image.pixelColor(90, 50), QColor(255, 255, 255, 58))
        self.assertEqual(len(glass._glass_material_cache), 0)

    def test_hover_adds_light_without_darkening_the_scene_or_spilling_outside(self):
        for light in (False, True):
            for background in ('#202020', '#808080', '#e6e6e6'):
                for level in (0, 50, 100):
                    with self.subTest(light=light, background=background, level=level):
                        source = self.scene(QColor(background))
                        idle = self.render(source, light=light, material_level=level)
                        hover = self.render(source, light=light, material_level=level, hovered=True)
                        def displayed(image):
                            result = QImage(image.size(), QImage.Format.Format_RGB32)
                            result.fill(QColor(background))
                            with QPainter(result) as painter:
                                painter.drawImage(0, 0, image)
                            return result.pixelColor(90, 50).lightness()
                        self.assertGreater(displayed(hover), displayed(idle))
                        self.assertEqual(hover.pixelColor(0, 0).alpha(), 0)
                        self.assertEqual(hover.pixelColor(20, 20).alpha(), 0)

    def test_stable_slot_limits_live_scene_sampling_to_ten_hz(self):
        first, second = self.scene(QColor('red')), self.scene(QColor('blue'))
        with patch('local.liquid_glass.time.monotonic', return_value=10.):
            original = self.render(first, cache_token=('surface', 'next'))
        with patch('local.liquid_glass.time.monotonic', return_value=10.05):
            held = self.render(second, cache_token=('surface', 'next'))
        with patch('local.liquid_glass.time.monotonic', return_value=10.11):
            refreshed = self.render(second, cache_token=('surface', 'next'))
        self.assertEqual(original, held)
        self.assertNotEqual(original, refreshed)
        self.assertGreater(refreshed.pixelColor(90, 50).blue(), 150)

    def test_sample_geometry_and_state_changes_do_not_reuse_the_old_crop(self):
        scene = self.scene(QColor('red'))
        with QPainter(scene) as painter:
            painter.fillRect(120, 0, 120, 100, QColor('blue'))
        with patch('local.liquid_glass.time.monotonic', return_value=10.):
            first = self.render(scene, QRectF(10, 10, 70, 60), cache_token='control')
        with patch('local.liquid_glass.time.monotonic', return_value=10.01):
            moved = self.render(scene, QRectF(150, 10, 70, 60), cache_token='control')
            hovered = self.render(scene, QRectF(150, 10, 70, 60), cache_token='control', hovered=True)
        self.assertNotEqual(first, moved)
        self.assertNotEqual(moved, hovered)

    def test_live_veil_states_share_material_without_delaying_state_feedback(self):
        scene = self.scene(QColor('#808080'))
        with patch('local.liquid_glass.time.monotonic', return_value=10.), \
                patch.object(glass, '_render_glass_material', wraps=glass._render_glass_material) as raster:
            images = [self.render(scene, cache_token='control', **state)
                      for state in ({}, {'light': True}, {'hovered': True},
                                    {'pressed': True}, {'active': True})]
        self.assertEqual(raster.call_count, 1)
        self.assertEqual(len({image.pixelColor(90, 50).rgba() for image in images}), 5)

    def test_shared_state_material_still_refreshes_geometry_and_blur_immediately(self):
        scene = self.scene()
        with patch('local.liquid_glass.time.monotonic', return_value=10.), \
                patch.object(glass, '_render_glass_material', wraps=glass._render_glass_material) as raster:
            self.render(scene, QRectF(0, 0, 100, 60), cache_token='control')
            self.render(scene, QRectF(10, 0, 100, 60), cache_token='control', hovered=True)
            self.render(scene, QRectF(10, 0, 100, 60), cache_token='control',
                        hovered=True, material_level=80)
        self.assertEqual(raster.call_count, 3)

    def test_material_cache_and_each_raster_are_bounded_and_clearable(self):
        scene = self.scene()
        for index in range(80):
            self.render(scene, cache_token=('control', index))
        self.assertLessEqual(len(glass._glass_material_cache), glass.GLASS_MATERIAL_CACHE_MAX_ENTRIES)
        self.assertLessEqual(len(glass._glass_material_slots), glass.GLASS_MATERIAL_CACHE_MAX_ENTRIES)
        self.assertLessEqual(glass._glass_material_cache_bytes, glass.GLASS_MATERIAL_CACHE_MAX_BYTES)
        for image in glass._glass_material_cache.values():
            self.assertLessEqual(max(image.width(), image.height()), glass.GLASS_MATERIAL_MAX_EDGE)
            self.assertLessEqual(image.sizeInBytes(), 256 * 1024)
        glass.clear_glass_cache()
        self.assertEqual(glass._glass_material_cache_bytes, 0)
        self.assertFalse(glass._glass_material_cache)
        self.assertFalse(glass._glass_material_slots)

    def test_material_restores_painter_state_and_foreground_ink_stays_sharp(self):
        image = QImage(200, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            painter.setOpacity(.6)
            painter.translate(3, 4)
            painter.setClipRect(QRectF(0, 0, 190, 80))
            before = (painter.opacity(), painter.transform(), painter.clipPath(), painter.pen())
            glass.paint_liquid_glass(painter, QRectF(20, 20, 140, 60), refractive=True,
                                     backdrop=self.scene(), light=True, opacity=.5)
            self.assertEqual(before, (painter.opacity(), painter.transform(), painter.clipPath(), painter.pen()))
            painter.setOpacity(1.)
            painter.fillRect(QRectF(80, 40, 20, 20), QColor('#14151a'))
        self.assertEqual(image.pixelColor(93, 54), QColor('#14151a'))
        self.assertLess(image.pixelColor(48, 54).alpha(), 80)


if __name__ == '__main__':
    unittest.main()
