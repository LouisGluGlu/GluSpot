"""The shared liquid-glass plate follows the backdrop and caches its blur."""
from __future__ import annotations

import unittest

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.liquid_glass_controls import (
    ControlGlassMaterial,
    control_glass_ink,
    control_glass_levels,
    paint_control_glass,
)


class LiquidGlassControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_slider_raises_blur_and_tint_together(self):
        clear = control_glass_levels(0.0)
        mid = control_glass_levels(0.5)
        solid = control_glass_levels(1.0)
        self.assertAlmostEqual(clear.blur_px, 4.0)
        self.assertAlmostEqual(clear.tint, 0.15)
        # The right end is the previous 75% mark, not the old 30 px / 75% tint.
        self.assertAlmostEqual(solid.blur_px, 23.5)
        self.assertAlmostEqual(solid.tint, 0.60)
        self.assertAlmostEqual(mid.blur_px, (clear.blur_px + solid.blur_px) / 2.0)
        self.assertAlmostEqual(mid.tint, (clear.tint + solid.tint) / 2.0)
        self.assertLess(clear.blur_px, mid.blur_px)
        self.assertLess(mid.blur_px, solid.blur_px)
        self.assertLess(clear.tint, mid.tint)
        self.assertLess(mid.tint, solid.tint)
        self.assertEqual(control_glass_ink(False), QColor(0, 0, 0))
        self.assertEqual(control_glass_ink(True), QColor(255, 255, 255))

    def test_light_plate_keeps_the_backdrop_color_and_caches_the_blur(self):
        backdrop = QImage(180, 80, QImage.Format.Format_RGB32)
        backdrop.fill(QColor(210, 60, 40))
        for x in range(100):
            for y in range(80):
                backdrop.setPixelColor(x, y, QColor(30, 150, 40))
        material = ControlGlassMaterial()
        material.set_backdrop(backdrop)
        image = QImage(backdrop.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0))
        plate = QRectF(16, 16, 68, 44)
        with QPainter(image) as painter:
            paint_control_glass(painter, plate, material, dark=False, amount=0.0)
        center = image.pixelColor(int(plate.center().x()), int(plate.center().y()))
        self.assertGreater(center.green(), center.red())
        self.assertGreater(center.green(), 60)
        builds = material.blur_builds
        with QPainter(image) as painter:
            paint_control_glass(painter, plate, material, dark=False, amount=0.0)
        self.assertEqual(material.blur_builds, builds)
        with QPainter(image) as painter:
            paint_control_glass(painter, plate, material, dark=False, amount=1.0)
        milky = image.pixelColor(int(plate.center().x()), int(plate.center().y()))
        self.assertGreater(milky.red(), center.red())
        self.assertGreater(milky.green(), 40)


class SceneGlassGapTests(unittest.TestCase):
    """A backdrop painted only around controls must not darken a plate's edge."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_transparent_backdrop_gaps_do_not_make_a_dark_rim(self):
        from local.liquid_glass_controls import SceneGlassPatches
        scene = QImage(400, 200, QImage.Format.Format_ARGB32_Premultiplied)
        scene.fill(QColor(0, 0, 0, 0))
        with QPainter(scene) as painter:
            painter.fillRect(QRectF(60, 40, 280, 120), QColor(200, 180, 60))   # painted only here
        plate = QRectF(70, 50, 260, 100)
        found = SceneGlassPatches().patch(scene, QRectF(0, 0, 400, 200), plate, 18.0, 20.0)
        self.assertIsNotNone(found)
        image = found[0]
        edge = image.pixelColor(2, image.height() // 2)
        centre = image.pixelColor(image.width() // 2, image.height() // 2)
        self.assertGreater(edge.red(), centre.red() - 25)

