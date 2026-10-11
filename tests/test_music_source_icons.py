from __future__ import annotations

import hashlib
import math
import re
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from local.branding import ROOT_DIR
from local.music_source_icons import _rendered_icon, music_source_icon_image, paint_music_source_icon


USER_SVG_HASHES = {
    "qq": "3289a77ba0bef15680ebfeb1eb915502afeb12eb1a67b800b145dc60992907c4",
    "qishui": "1333697a2a9e0370049f75e886d2628c86ef669465cb24ec550b45b9dc3f1cfa",
    "kugou": "a17073123a027618e7442e81504f11100d390489b84edb01d841db9dd9fb8687",
    "netease": "09b41126393bda55692630e3bc455013b2b403eede123f3d462171752aa08486",
    "search": "67232ae5aa071ed77b8dd289fc418516b5009e93fa1bc2d66aa1eda22826c250",
    "logout": "726af968163ac94794ca55ce20ad3c79d85ad37d73aeafacba74902b92afb804",
    "switch": "37b1d01432b58498395fb9e5d0e2a93f3b650a03e5c156be153dec62c8ed62b4",
}


class MusicSourceIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_supplied_assets_keep_original_bytes_are_local_geometry_and_render(self):
        for key, digest in USER_SVG_HASHES.items():
            with self.subTest(icon=key):
                path = ROOT_DIR / "assets" / "music_sources" / f"{key}.svg"
                data = path.read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
                root = ET.fromstring(data)
                self.assertEqual({element.tag.rsplit("}", 1)[-1] for element in root.iter()}, {"svg", "path"})
                self.assertNotRegex(data, rb"<!ENTITY|<!DOCTYPE[^>]*\[|\bon\w+\s*=")
                for element in root.iter():
                    for name, value in element.attrib.items():
                        if name.rsplit("}", 1)[-1] in {"href", "src"}:
                            self.assertTrue(value.startswith("#"))
                        for reference in re.findall(r"url\((.*?)\)", value):
                            self.assertTrue(reference.strip().startswith("#"))
                self.assertTrue(QSvgRenderer(str(path)).isValid())
                image = music_source_icon_image(key, QSize(72, 72))
                alpha = [image.pixelColor(x, y).alpha() for y in range(72) for x in range(72)]
                self.assertGreater(sum(value > 0 for value in alpha), 100)

    def test_non_square_slot_preserves_svg_ratio_and_centers_ink(self):
        image = music_source_icon_image("search", QSize(180, 90))
        visible = [(x, y) for y in range(90) for x in range(180) if image.pixelColor(x, y).alpha() > 0]
        xs, ys = zip(*visible)
        self.assertLessEqual(max(xs) - min(xs) + 1, 90)
        self.assertLessEqual(max(ys) - min(ys) + 1, 90)
        self.assertGreaterEqual(min(xs), 45)
        self.assertLessEqual(max(xs), 134)

    def test_monochrome_tint_retains_shape_and_source_color_render_stays_original(self):
        original = music_source_icon_image("switch", QSize(64, 64))
        tinted = music_source_icon_image("switch", QSize(64, 64), tint=QColor("white"))
        for y in range(64):
            for x in range(64):
                before, after = original.pixelColor(x, y), tinted.pixelColor(x, y)
                self.assertEqual(before.alpha(), after.alpha())
                if before.alpha() == 255:
                    self.assertEqual(before.name(), "#000000")
                    self.assertEqual(after.name(), "#ffffff")
        qq = music_source_icon_image("qq", QSize(64, 64))
        colors = {qq.pixelColor(x, y).name() for y in range(64) for x in range(64)
                  if qq.pixelColor(x, y).alpha() == 255}
        self.assertIn("#33c079", colors)
        self.assertIn("#fddb00", colors)

    @staticmethod
    def _opaque_column_color(image, x):
        colors = [image.pixelColor(x, y) for y in range(image.height())
                  if image.pixelColor(x, y).alpha() == 255]
        if not colors:
            raise AssertionError(f"No opaque SVG ink at column {x}")
        if len({color.rgba() for color in colors}) != 1:
            raise AssertionError("Horizontal gradient varies vertically")
        return colors[0]

    @staticmethod
    def _gradient_stops():
        return ((0.0, QColor("#102030")), (0.25, QColor("#507090")),
                (0.5, QColor("#e0efff")), (0.75, QColor("#507090")),
                (1.0, QColor("#102030")))

    def test_gradient_has_simultaneous_deep_middle_light_bands_in_exact_svg_mask(self):
        original = music_source_icon_image("personal_fm", QSize(128, 128))
        image = music_source_icon_image("personal_fm", QSize(128, 128),
                                        gradient_stops=self._gradient_stops())
        for y in range(image.height()):
            for x in range(image.width()):
                self.assertEqual(original.pixelColor(x, y).alpha(), image.pixelColor(x, y).alpha())
        # The actual infinity holes stay transparent, while three distinct
        # tones coexist across its strokes in a single frame.
        self.assertEqual(image.pixelColor(36, 68).alpha(), 0)
        deep = self._opaque_column_color(image, 8)
        middle = self._opaque_column_color(image, 32)
        light = self._opaque_column_color(image, 64)
        self.assertLess(deep.lightness(), middle.lightness())
        self.assertLess(middle.lightness(), light.lightness())
        self.assertGreater(light.lightness() - deep.lightness(), 140)

    def test_gradient_phase_moves_bands_right_smoothly_and_repeats_without_seam(self):
        size = QSize(128, 128)
        stops = self._gradient_stops()
        start = music_source_icon_image("personal_fm", size, gradient_stops=stops)
        shifted = music_source_icon_image("personal_fm", size, gradient_stops=stops, gradient_phase=.125)
        self.assertEqual(self._opaque_column_color(start, 32), self._opaque_column_color(shifted, 48))
        self.assertNotEqual(self._opaque_column_color(start, 48), self._opaque_column_color(shifted, 48))
        repeated = music_source_icon_image("personal_fm", size, gradient_stops=stops, gradient_phase=1)
        self.assertEqual(start, repeated)
        near_end = music_source_icon_image("personal_fm", size, gradient_stops=stops, gradient_phase=.999)
        near_start = music_source_icon_image("personal_fm", size, gradient_stops=stops, gradient_phase=.001)
        for x in (8, 32, 48, 64, 96, 120):
            before = self._opaque_column_color(near_end, x)
            after = self._opaque_column_color(near_start, x)
            self.assertLessEqual(max(abs(a - b) for a, b in zip(before.getRgb(), after.getRgb())), 2)

    def test_gradient_painter_rasterizes_at_actual_device_dpr(self):
        stops = self._gradient_stops()
        for dpr in (1.0, 1.25, 1.5, 2.0):
            with self.subTest(dpr=dpr):
                physical = math.ceil(24 * dpr)
                image = QImage(physical, physical, QImage.Format.Format_ARGB32_Premultiplied)
                image.setDevicePixelRatio(dpr)
                image.fill(Qt.GlobalColor.transparent)
                with patch("local.music_source_icons.music_source_icon_image", wraps=music_source_icon_image) as render:
                    with QPainter(image) as painter:
                        paint_music_source_icon(painter, QRectF(0, 0, 24, 24), "personal_fm",
                                                gradient_stops=stops, gradient_phase=.125)
                self.assertEqual(render.call_args.args[1], QSize(physical, physical))
                expected = music_source_icon_image("personal_fm", QSize(physical, physical),
                                                   gradient_stops=stops, gradient_phase=.125)
                for y in range(physical):
                    for x in range(physical):
                        self.assertEqual(image.pixelColor(x, y), expected.pixelColor(x, y))

    def test_animated_gradient_reuses_one_bounded_mask_without_evicting_color_frames(self):
        original = music_source_icon_image("qq", QSize(73, 73))
        before = _rendered_icon.cache_info()
        for frame in range(120):
            stops = ((0.0, QColor(frame, 20, 40)), (.5, QColor(230, 240, 255)),
                     (1.0, QColor(frame, 20, 40)))
            music_source_icon_image("personal_fm", QSize(137, 137), gradient_stops=stops,
                                    gradient_phase=frame / 120)
        after = _rendered_icon.cache_info()
        self.assertLessEqual(after.misses - before.misses, 1)
        self.assertLessEqual(after.currsize - before.currsize, 1)
        self.assertEqual(original, music_source_icon_image("qq", QSize(73, 73)))
        for side in range(1, 100):
            music_source_icon_image("personal_fm", QSize(side, side), gradient_stops=self._gradient_stops())
        self.assertEqual(_rendered_icon.cache_info().maxsize, 88)
        self.assertLessEqual(_rendered_icon.cache_info().currsize, 88)
        bounded = music_source_icon_image("personal_fm", QSize(900, 700), gradient_stops=self._gradient_stops())
        self.assertEqual(bounded.size(), QSize(256, 256))

    def test_invalid_gradient_falls_back_to_existing_tint_without_poisoning_mask(self):
        expected = music_source_icon_image("personal_fm", QSize(32, 32), tint=QColor("#126abc"))
        for stops, phase in ((((-.1, QColor("red")), (1, QColor("blue"))), 0),
                             (((0, QColor("red")), (float("nan"), QColor("blue"))), 0),
                             (self._gradient_stops(), float("inf")),
                             (((0, QColor()), (1, QColor("blue"))), 0)):
            with self.subTest(stops=stops, phase=phase):
                actual = music_source_icon_image("personal_fm", QSize(32, 32), tint=QColor("#126abc"),
                                                 gradient_stops=stops, gradient_phase=phase)
                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
