"""Continuous t156 background overlay stays bounded and independent of ink."""
import math
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageStat
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from _demos.narrow3.veil import BackgroundEdgeVeil


class NarrowBackgroundVeilTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        width, height = 384, 640
        pixels = bytes(round(128 + 60 * math.sin(x * math.pi / 32)
                             + 24 * math.cos(y * math.pi / 20))
                       for y in range(height) for x in range(width))
        image = Image.frombytes("L", (width, height), pixels).convert("RGBA")
        data = image.tobytes()
        self.raw = QImage(data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
        self.veil = BackgroundEdgeVeil()
        self.assertTrue(self.veil.prepare(self.raw, width, height))

    def test_moving_small_backdrop_reuses_profile_without_upscaling(self):
        source = self.raw.scaled(128, 160)
        self.veil.prepare(source, 433, 550)
        profile = self.veil._alpha_profile
        mask = self.veil._alpha_lut
        self.assertLessEqual(self.veil._source.width(), source.width())
        self.assertLessEqual(self.veil._source.height(), source.height())
        moved = source.copy()
        moved.fill(QColor('#c98b72'))
        self.veil.prepare(moved, 433, 550)
        self.assertIs(self.veil._alpha_profile, profile)
        self.assertIs(self.veil._alpha_lut, mask)
        self.assertLessEqual(self.veil.cache_bytes, 256 * 256 * 4)

    def render(self, progress):
        image = self.raw.copy()
        with QPainter(image) as painter:
            self.veil.paint(painter, QRectF(0, 0, image.width(), image.height()), progress)
        return image

    @staticmethod
    def gray(image):
        converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
        return Image.frombytes("RGBA", (image.width(), image.height()), bytes(converted.constBits())).convert("L")

    @staticmethod
    def energy(image):
        return ImageStat.Stat(ImageChops.difference(image, ImageChops.offset(image, 1, 1))).mean[0]

    def test_idle_and_reveal_reduce_detail_and_dim_raw_edge_bands(self):
        raw, idle, reveal = map(self.gray, (self.raw, self.render(0.), self.render(1.)))
        for box in ((0, 0, raw.width, 120), (0, 520, raw.width, 640)):
            with self.subTest(box=box):
                samples = [image.crop(box) for image in (raw, idle, reveal)]
                brightness = [ImageStat.Stat(image).mean[0] for image in samples]
                idle_dim = 1. - brightness[1] / brightness[0]
                reveal_dim = 1. - brightness[2] / brightness[0]
                self.assertGreaterEqual(idle_dim, .03)
                self.assertLessEqual(idle_dim, .06)
                self.assertGreaterEqual(reveal_dim, .10)
                self.assertLessEqual(reveal_dim, .185)
                raw_energy, idle_energy, reveal_energy = map(self.energy, samples)
                self.assertLess(idle_energy, raw_energy)
                self.assertLess(reveal_energy, raw_energy)
                self.assertLess(reveal_energy, idle_energy)

    def test_middle_and_sharp_foreground_remain_exact(self):
        image = self.render(.6)
        self.assertEqual(image.copy(0, 260, image.width(), 120),
                         self.raw.copy(0, 260, self.raw.width(), 120))
        with QPainter(image) as painter:
            painter.fillRect(QRectF(100, 300, 80, 20), QColor("white"))
        self.assertEqual(image.pixelColor(120, 310), QColor("white"))

    def test_paint_uses_exact_prepared_endpoints_without_filtering(self):
        builds = self.veil.builds
        with patch.object(Image.Image, "filter", side_effect=AssertionError("filter during paint")):
            idle, middle, reveal = self.render(0.), self.render(.5), self.render(1.)
            self.assertEqual(idle, self.render(-1.))
            self.assertEqual(reveal, self.render(2.))
        x, y = 100, 40
        colors = [image.pixelColor(x, y).red() for image in (idle, middle, reveal)]
        self.assertLessEqual(abs(colors[1] - (colors[0] + colors[2]) / 2.), 1.)
        self.assertEqual(self.veil.builds, builds)

    def test_fixed_reveal_has_identical_pixels_across_ten_frames(self):
        for progress in (0., 1.):
            expected = self.render(progress)
            for _frame in range(10):
                self.assertEqual(self.render(progress), expected)

    def test_cache_is_one_bounded_overlay_and_reuses_identical_source(self):
        self.assertFalse(self.veil.prepare(self.raw, self.raw.width(), self.raw.height()))
        self.assertEqual(self.veil.builds, 1)
        self.assertLessEqual(self.veil.cache_bytes, 512 * 512 * 4)
        self.assertEqual(len(self.veil._states), 1)
        prepared = self.veil._states[0].copy()
        self.raw.fill(Qt.GlobalColor.red)
        self.assertEqual(self.veil._states[0], prepared,
                         "Caller writes changed the frozen blurred overlay")
        self.assertEqual(self.render(0.).pixelColor(200, 300), QColor('red'),
                         "The transparent clear core must keep the live backdrop")
        self.assertTrue(self.veil.prepare(self.raw, 768, 1280))
        self.assertEqual(self.veil.builds, 2)
        self.assertEqual(len(self.veil._states), 1)
        self.assertLessEqual(self.veil.cache_bytes, 512 * 512 * 4)
