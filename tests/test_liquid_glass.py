"""Native glass-rim regressions: original pigment, clear center and state."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from local.liquid_glass import GLASS_FACE_OPACITY, paint_liquid_glass, widget_glass_backdrop


class LiquidGlassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def render(self, **options):
        image = QImage(180, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            paint_liquid_glass(painter, QRectF(20, 20, 140, 60), **options)
        return image

    def test_original_face_keeps_pigment_and_only_reduces_its_own_alpha(self):
        for color in (QColor(55, 56, 57, 236), QColor('#34c759'), QColor(248, 249, 250, 245),
                      QColor(18, 20, 24, 24)):
            with self.subTest(color=color):
                image = self.render(base_color=color)
                # Compare native premultiplied pixels, including low-alpha
                # channel quantization, rather than unpremultiplied RGB deltas.
                reference = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
                reference.fill(Qt.GlobalColor.transparent)
                expected = QColor(color)
                expected.setAlphaF(color.alphaF() * GLASS_FACE_OPACITY)
                with QPainter(reference) as painter:
                    painter.fillRect(reference.rect(), expected)
                self.assertEqual(image.pixel(90, 50), reference.pixel(0, 0))
                self.assertEqual(image.pixelColor(20, 20).alpha(), 0)
                self.assertEqual(image.pixelColor(0, 0).alpha(), 0)

    def test_rim_only_has_empty_center_and_no_new_button_plate(self):
        image = self.render()
        self.assertEqual(image.pixelColor(90, 50).alpha(), 0)
        self.assertGreater(image.pixelColor(90, 20).alpha(), 0)
        for y in range(26, 74):
            for x in range(50, 130):
                self.assertEqual(image.pixelColor(x, y).alpha(), 0)

    def test_hover_press_and_selection_do_not_recolor_original_center(self):
        color = QColor(73, 108, 179, 230)
        idle, hover, pressed, active = (self.render(base_color=color, **opts) for opts in
                                       ({}, {'hovered': True}, {'pressed': True}, {'active': True}))
        for image in (hover, pressed, active):
            self.assertEqual(image.pixelColor(90, 50), idle.pixelColor(90, 50))
        self.assertNotEqual(idle, hover)
        self.assertNotEqual(hover, pressed)
        self.assertEqual(idle, active)

    def test_cached_backdrop_and_legacy_tint_never_add_a_replacement_layer(self):
        first = QImage(180, 100, QImage.Format.Format_RGB32)
        first.fill(QColor('#e41f38'))
        second = QImage(first.size(), first.format())
        second.fill(QColor('#247bbf'))
        color = QColor(55, 56, 57, 236)
        plain = self.render(base_color=color)
        for scene in (first, second):
            actual = self.render(base_color=color, backdrop=scene, backdrop_rect=QRectF(scene.rect()),
                                 tint=QColor('red'), cache_token='old')
            self.assertEqual(actual, plain)

    def test_center_has_no_wash_or_bloom_and_original_color_is_not_mutated(self):
        color = QColor(104, 31, 61, 212)
        original = QColor(color)
        image = self.render(base_color=color, border_color=QColor(75, 76, 77, 240), radius=12)
        for y in (30, 50, 70):
            for x in (40, 90, 140):
                self.assertEqual(image.pixelColor(x, y), image.pixelColor(90, 50))
        self.assertEqual(color, original)

    def test_painter_opacity_clip_transform_and_pen_are_restored(self):
        image = QImage(200, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            painter.setOpacity(.6)
            painter.translate(3, 4)
            painter.setClipRect(QRectF(0, 0, 190, 80))
            before = (painter.opacity(), painter.transform(), painter.clipBoundingRect(), painter.pen())
            paint_liquid_glass(painter, QRectF(20, 20, 140, 60), base_color=QColor(55, 56, 57, 236), opacity=.5)
            after = (painter.opacity(), painter.transform(), painter.clipBoundingRect(), painter.pen())
        self.assertEqual(before, after)
        self.assertAlmostEqual(image.pixelColor(93, 54).alpha(), 236 * GLASS_FACE_OPACITY * .3, delta=1)

    def test_backdrop_mapping_is_local_and_never_captures_widgets(self):
        host, child = QWidget(), QWidget()
        child.setParent(host)
        host.resize(400, 200)
        child.setGeometry(100, 50, 80, 40)
        host._background_cache = QImage(180, 100, QImage.Format.Format_RGB32)
        with patch.object(host, 'grab', side_effect=AssertionError('capture forbidden')), \
             patch.object(host, 'render', side_effect=AssertionError('capture forbidden')):
            image, region = widget_glass_backdrop(child)
        self.assertEqual(image, host._background_cache)
        self.assertEqual(region, QRectF(45, 25, 36, 20))
        host.close()

    def test_caller_glyph_pixels_remain_sharp_and_exact(self):
        image = self.render(base_color=QColor('#f5f5f7'), light=True)
        with QPainter(image) as painter:
            painter.fillRect(QRectF(80, 40, 20, 20), QColor('#14151a'))
        self.assertEqual(image.pixelColor(90, 50), QColor('#14151a'))


if __name__ == '__main__':
    unittest.main()
