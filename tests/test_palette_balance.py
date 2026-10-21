from __future__ import annotations

import math
import unittest

from PySide6.QtGui import QColor, QImage, QPainter

from local.music_surface_palette import (
    _oklab,
    artwork_surface_palette,
    balanced_background_colors,
    cached_artwork_surface_palette,
    relative_luminance,
    solid_album_background_color,
)


def _posterized_cover(*, alpha_border: bool = False) -> QImage:
    image = QImage(100, 100, QImage.Format.Format_RGBA8888)
    image.fill(QColor(0, 255, 0, 0))
    inset = 10 if alpha_border else 0
    side = 100 - 2 * inset
    with QPainter(image) as painter:
        painter.fillRect(inset, inset, side, side * 7 // 10, QColor(250, 20, 34))
        painter.fillRect(inset, inset + side * 7 // 10, side, side * 2 // 10, QColor(245, 245, 245))
        painter.fillRect(inset, inset + side * 9 // 10, side, side // 10, QColor(0, 225, 255))
    return image


class PaletteBalanceTests(unittest.TestCase):
    def test_red_white_cyan_art_keeps_source_identity_but_gets_a_quiet_display_foundation(self):
        image = _posterized_cover()
        before = image.constBits().tobytes()
        palette = artwork_surface_palette(image, QColor("black"), QColor("black"))
        colors, weights = palette.ambient_colors, palette.ambient_weights
        slots = balanced_background_colors(colors, weights=weights)
        self.assertEqual(len(slots), len(colors) + 1)
        self.assertEqual(colors, (QColor(250, 20, 34), QColor(245, 245, 245), QColor(0, 225, 255)))
        self.assertAlmostEqual(weights[0], .70, delta=.015)
        self.assertAlmostEqual(weights[1], .20, delta=.015)
        self.assertAlmostEqual(weights[2], .10, delta=.015)
        self.assertEqual(solid_album_background_color(palette), colors[0])
        self.assertEqual(before, image.constBits().tobytes())
        source_lab, base_lab, core_lab = _oklab(colors[0]), _oklab(slots[0]), _oklab(slots[1])
        self.assertLess(math.hypot(*base_lab[1:]), math.hypot(*source_lab[1:]) * .25)
        self.assertLess(base_lab[0], core_lab[0])
        self.assertGreater(math.hypot(*core_lab[1:]), .10)
        self.assertEqual(slots[2].red(), slots[2].green())
        self.assertEqual(slots[2].green(), slots[2].blue())
        self.assertGreater(slots[3].blue(), slots[3].red())
        self.assertGreater(slots[3].green(), slots[3].red())
        self.assertTrue(all(relative_luminance(color) < .265 for color in slots))

    def test_balance_is_not_a_red_specific_desaturation_rule(self):
        for source in (QColor(250, 20, 34), QColor(20, 30, 255), QColor(245, 15, 235), QColor(255, 255, 0)):
            with self.subTest(source=source.name()):
                slots = balanced_background_colors((source, QColor("white")), weights=(.7, .3))
                self.assertEqual(len(slots), 3)
                original, displayed = _oklab(source), _oklab(slots[1])
                angle = abs(math.atan2(original[2], original[1]) - math.atan2(displayed[2], displayed[1]))
                self.assertLess(min(angle, math.tau - angle), math.radians(2))
                self.assertLess(math.hypot(*displayed[1:]), math.hypot(*original[1:]))

    def test_natural_muted_dark_and_black_white_art_keep_existing_route(self):
        cases = (
            ((QColor(188, 145, 102), QColor(225, 220, 210), QColor(25, 115, 200)), (.6, .3, .1)),
            ((QColor(112, 133, 140), QColor("white")), (.7, .3)),
            ((QColor(7, 22, 38), QColor(180, 207, 235)), (.8, .2)),
            ((QColor("black"), QColor("white")), (.7, .3)),
            ((QColor(250, 20, 34),), (1.0,)),
            ((QColor(250, 20, 34), QColor(0, 225, 255)), (.7, .3)),
        )
        for colors, weights in cases:
            with self.subTest(colors=colors):
                self.assertEqual(balanced_background_colors(colors, weights=weights), ())

    def test_transparent_edges_do_not_add_a_green_field_or_change_cached_source_roles(self):
        image = _posterized_cover(alpha_border=True)
        palette = artwork_surface_palette(image, QColor("black"), QColor("black"))
        self.assertEqual(len(palette.ambient_colors), 3)
        self.assertEqual(palette.source_primary, QColor(250, 20, 34))
        self.assertTrue(balanced_background_colors(palette.ambient_colors, weights=palette.ambient_weights))
        original = tuple(color.rgba() for color in palette.ambient_colors)
        slots = balanced_background_colors(palette.ambient_colors, weights=palette.ambient_weights)
        slots[1].setRed(0)
        palette.ambient_colors[0].setGreen(255)
        cached = cached_artwork_surface_palette(image, QColor("black"), QColor("black"))
        self.assertIsNotNone(cached)
        self.assertEqual(tuple(color.rgba() for color in cached.ambient_colors), original)
        self.assertTrue(all(color.hsvHue() != 120 for color in cached.ambient_colors))

    def test_missing_or_invalid_coverage_cannot_invent_a_neutral_area(self):
        colors = (QColor(250, 20, 34), QColor("white"))
        for weights in ((), (.7,), (.7, math.nan), (.7, -.1), (.4, .6), (.99, .01)):
            with self.subTest(weights=weights):
                self.assertEqual(balanced_background_colors(colors, weights=weights), ())
        self.assertEqual(balanced_background_colors((QColor(), QColor("white")), weights=(.7, .3)), ())


if __name__ == "__main__":
    unittest.main()
