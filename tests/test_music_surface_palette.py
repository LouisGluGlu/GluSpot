from __future__ import annotations

import unittest
from pathlib import Path

from PySide6.QtGui import QColor, QImage

from local.music_surface_palette import (
    MusicSurfacePalette,
    artwork_surface_palette,
    background_role_color,
    future_lyric_color,
    relative_luminance,
    single_hue_background_colors,
    solid_album_background_color,
)


def _artwork(bands: tuple[tuple[int, QColor], ...]) -> QImage:
    image = QImage(100, 100, QImage.Format.Format_RGB32)
    start = 0
    for end, color in bands:
        for y in range(start, end):
            for x in range(100):
                image.setPixelColor(x, y, color)
        start = end
    return image


def _hue_distance(first: QColor, second: QColor) -> int:
    distance = abs(first.hsvHue() - second.hsvHue()) % 360
    return min(distance, 360 - distance)


def _ink_contrast(ink: QColor, backdrop: QColor) -> float:
    alpha = ink.alphaF()
    composite = QColor(*(
        round(front * alpha + back * (1.0 - alpha))
        for front, back in zip(ink.getRgb()[:3], backdrop.getRgb()[:3])
    ))
    first, second = relative_luminance(composite), relative_luminance(backdrop)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


class MusicSurfacePaletteTests(unittest.TestCase):
    def test_pure_blue_multicolor_cover_preserves_exact_source_and_solid_island_roles(self):
        blue, purple, warm, neutral = (QColor(34, 34, 254), QColor(88, 10, 115),
                                       QColor(188, 145, 102), QColor(66, 66, 66))
        palette = artwork_surface_palette(_artwork(((62, blue), (80, purple), (92, warm), (100, neutral))),
                                          QColor("black"), QColor("black"))
        self.assertEqual(palette.source_primary, blue)
        self.assertEqual(palette.ambient_colors[0], blue)
        self.assertIn(purple, palette.ambient_colors)
        self.assertIn(warm, palette.ambient_colors)
        self.assertEqual(solid_album_background_color(palette), blue)
        self.assertFalse(single_hue_background_colors(palette.ambient_colors, weights=palette.ambient_weights))

    def test_bright_single_hue_display_layers_keep_hue_and_solid_source(self):
        for source in (QColor(233, 31, 20), QColor(16, 135, 164),
                       QColor(242, 204, 12), QColor(181, 35, 230)):
            with self.subTest(source=source.name()):
                palette = artwork_surface_palette(
                    _artwork(((100, source),)), QColor("black"), QColor("black"),
                )
                roles = single_hue_background_colors(
                    palette.ambient_colors, weights=palette.ambient_weights,
                )
                self.assertEqual(len(roles), 4)
                self.assertTrue(all(_hue_distance(role, source) <= 1 for role in roles))
                self.assertLess(roles[0].hsvSaturation(), source.hsvSaturation() * .4)
                self.assertGreater(roles[2].value() - roles[0].value(), 75)
                self.assertLess(roles[2].hsvSaturation(), roles[3].hsvSaturation())
                self.assertEqual(palette.ambient_colors, (source,))
                self.assertEqual(palette.ambient_weights, (1.0,))
                self.assertEqual(solid_album_background_color(palette), source)

    def test_near_single_hue_variations_use_one_tonal_family(self):
        colors = (QColor(16, 135, 164), QColor(30, 177, 201), QColor(90, 186, 209))
        roles = single_hue_background_colors(colors, weights=(.999, .305, .055))
        self.assertEqual(len(roles), 4)
        self.assertTrue(all(_hue_distance(role, colors[0]) <= 1 for role in roles))
        self.assertEqual(colors[0], QColor(16, 135, 164))

    def test_multicolor_gray_low_chroma_and_dark_covers_keep_source_route(self):
        controls = (
            ((QColor(195, 30, 40), QColor(30, 40, 195)), (.98, .02)),
            ((QColor(195, 30, 40), QColor(240, 240, 240)), (.99, .01)),
            ((QColor(112, 112, 112),), (1.0,)),
            ((QColor(112, 133, 140),), (1.0,)),
            ((QColor(7, 22, 38), QColor(20, 130, 240), QColor(180, 207, 235)), (1.0, .25, .05)),
            ((QColor(30, 3, 8),), (1.0,)),
            ((QColor(180, 35, 230),), (.79,)),
            ((), ()),
        )
        for colors, weights in controls:
            with self.subTest(colors=colors):
                self.assertEqual(single_hue_background_colors(colors, weights=weights), ())

    def test_ambient_core_keeps_real_strong_pigment_separate_from_same_hue_shadows(self):
        shadow, core, pale = QColor(7, 22, 38), QColor(20, 130, 240), QColor(180, 207, 235)
        palette = artwork_surface_palette(
            _artwork(((70, shadow), (95, core), (100, pale))), QColor("black"), QColor("black"),
        )
        self.assertEqual(palette.ambient_colors[0], shadow)
        self.assertIn(core, palette.ambient_colors)
        self.assertGreater(core.hsvSaturation(), 225)

    def test_one_percent_ambient_magenta_survives_brightness_and_chroma_fragmentation(self):
        image = QImage(72, 72, QImage.Format.Format_RGB32)
        image.fill(QColor(220, 184, 139))
        # Each tone alone occupies less than the family support threshold.
        # Together they are a real one-percent colored detail, not pixel noise.
        pigments = tuple(QColor.fromHsv(hue, chroma, value) for hue, chroma, value in (
            (322, 220, 230), (338, 235, 230), (325, 205, 160),
            (341, 230, 160), (329, 135, 205), (336, 145, 180),
        ))
        for index in range(54):
            image.setPixelColor(index % 9, 10 + index // 9, pigments[index // 9])
        palette = artwork_surface_palette(image, QColor("black"), QColor("black"))
        self.assertGreater(palette.ambient_colors[0].red(), palette.ambient_colors[0].blue())
        accents = [color for color in palette.ambient_colors if 310 <= color.hsvHue() <= 350]
        self.assertTrue(accents)
        self.assertTrue(any(color.hsvSaturation() >= 200 and color.value() >= 210 for color in accents))

    def test_ambient_one_pixel_neon_noise_is_rejected_on_black_art(self):
        image = QImage(72, 72, QImage.Format.Format_RGB32)
        image.fill(QColor(0, 0, 0))
        image.setPixelColor(14, 25, QColor("magenta"))
        palette = artwork_surface_palette(image, QColor("blue"), QColor("cyan"))
        self.assertEqual(palette.ambient_colors, (QColor("black"),))

    def test_scattered_one_percent_neon_noise_does_not_become_an_ambient_pigment(self):
        image = QImage(72, 72, QImage.Format.Format_RGB32)
        image.fill(QColor(0, 0, 0))
        for index in range(54):
            image.setPixelColor(4 + (index % 9) * 8, 4 + (index // 9) * 11, QColor("magenta"))
        palette = artwork_surface_palette(image, QColor("blue"), QColor("cyan"))
        self.assertEqual(palette.ambient_colors, (QColor("black"),))

    def test_uniform_colored_art_supplies_its_only_source_tone(self):
        source = QColor(15, 174, 210)
        palette = artwork_surface_palette(
            _artwork(((100, source),)), QColor("blue"), QColor("red"),
        )
        self.assertEqual(palette.ambient_colors, (source,))
        self.assertEqual(palette.ambient_weights, (1.0,))

    def test_sparse_ambient_accent_keeps_its_actual_source_coverage(self):
        image = QImage(72, 72, QImage.Format.Format_RGB32)
        image.fill(QColor(0, 0, 0))
        accent = QColor(230, 35, 145)
        for y in range(12, 18):
            for x in range(20, 29):
                image.setPixelColor(x, y, accent)
        palette = artwork_surface_palette(image, QColor("blue"), QColor("cyan"))
        self.assertEqual(palette.ambient_colors, (QColor("black"), accent))
        self.assertEqual(len(palette.ambient_colors), len(palette.ambient_weights))
        self.assertGreater(palette.ambient_weights[0], 0.50)
        self.assertGreater(palette.ambient_weights[1], 0.0)
        self.assertLess(palette.ambient_weights[1], 0.04)
        self.assertAlmostEqual(palette.ambient_weights[1], 54 / (72 * 72))

    def test_ambient_family_and_variant_weights_report_original_coverage(self):
        shadow, core = QColor(7, 22, 38), QColor(20, 130, 240)
        palette = artwork_surface_palette(
            _artwork(((75, shadow), (100, core))), QColor("black"), QColor("black"),
        )
        self.assertEqual(palette.ambient_colors, (shadow, core))
        # Both tones belong to the dominant blue family; its base has all
        # family support while the vivid variation owns its actual quarter.
        self.assertAlmostEqual(palette.ambient_weights[0], 1.0)
        self.assertAlmostEqual(palette.ambient_weights[1], 0.25)
        self.assertGreater(sum(palette.ambient_weights), 1.0)

    def test_ambient_weights_are_bounded_and_match_selected_color_order(self):
        fixtures = (
            _artwork(((50, QColor(8, 8, 8)), (100, QColor(235, 235, 235)))),
            _artwork(tuple((index * 10, QColor.fromHsv(hue, 220, 190))
                           for index, hue in enumerate(range(0, 360, 36), start=1))),
            _artwork(((60, QColor(220, 184, 139)), (85, QColor(25, 115, 200)),
                      (100, QColor(230, 35, 145)))),
        )
        for image in fixtures:
            palette = artwork_surface_palette(image, QColor("black"), QColor("black"))
            self.assertEqual(len(palette.ambient_colors), len(palette.ambient_weights))
            self.assertTrue(all(0.0 < weight <= 1.0 for weight in palette.ambient_weights))
            self.assertGreaterEqual(palette.ambient_weights[0], max(palette.ambient_weights))

    def test_ambient_tonal_variants_cannot_overflow_six_supported_families(self):
        bands = tuple(
            (index * 10, QColor.fromHsv(hue, chroma, value))
            for index, (hue, chroma, value) in enumerate((
                (0, 180, 210), (60, 190, 210), (120, 195, 210),
                (180, 195, 210), (240, 190, 210), (300, 180, 210),
                (0, 230, 110), (60, 230, 110), (120, 230, 110), (180, 230, 110),
            ), start=1)
        )
        palette = artwork_surface_palette(_artwork(bands), QColor("black"), QColor("black"))
        self.assertEqual(len(palette.ambient_colors), 6)

    def test_required_light_blue_ink_is_brighter_than_bright_gray_envelope(self):
        backdrop = QColor(145, 145, 145)
        ink = future_lyric_color(
            QColor("#1023ff"), backdrop, use_light=True,
            primary=QColor("#c8aa46"), require_light=True,
        )
        composite = QColor(*(
            round(front * ink.alphaF() + back * (1.0 - ink.alphaF()))
            for front, back in zip(ink.getRgb()[:3], backdrop.getRgb()[:3])
        ))
        self.assertGreater(relative_luminance(composite), relative_luminance(backdrop))
        self.assertGreaterEqual(_ink_contrast(ink, backdrop), 2.30)
        self.assertLessEqual(_hue_distance(ink, QColor("#1023ff")), 2)
        # The same fixed light ink must also remain visible on the dark field.
        self.assertGreaterEqual(_ink_contrast(ink, QColor(20, 20, 20)), 2.30)

    def test_required_light_ink_stays_valid_for_an_impossible_white_envelope(self):
        ink = future_lyric_color(
            QColor("blue"), QColor("white"), use_light=True, require_light=True,
        )
        self.assertTrue(ink.isValid())

    def test_three_role_construction_keeps_optional_ambient_palette_empty(self):
        palette = MusicSurfacePalette(QColor("red"), QColor("green"), QColor("blue"))
        self.assertEqual(palette.ambient_colors, ())
        self.assertEqual(palette.ambient_weights, ())
        with_colors = MusicSurfacePalette(
            QColor("red"), QColor("green"), QColor("blue"), (QColor("red"),),
        )
        self.assertEqual(with_colors.ambient_weights, ())

    def test_ambient_palette_keeps_six_supported_cover_pigments(self):
        pigments = tuple(QColor.fromHsv(hue, 210, 204) for hue in range(0, 360, 60))
        image = _artwork(tuple(zip((30, 54, 74, 87, 95, 100), pigments)))
        palette = artwork_surface_palette(image, QColor("black"), QColor("black"))

        self.assertEqual(
            tuple(color.getRgb() for color in palette.ambient_colors),
            tuple(color.getRgb() for color in pigments),
        )
        for index, first in enumerate(palette.ambient_colors):
            for second in palette.ambient_colors[index + 1:]:
                self.assertGreaterEqual(_hue_distance(first, second), 42)

    def test_ambient_palette_is_bounded_and_does_not_repeat_nearby_hues(self):
        image = _artwork(tuple(
            (index * 10, QColor.fromHsv(hue, 220, 190))
            for index, hue in enumerate(range(0, 360, 36), start=1)
        ))
        first = artwork_surface_palette(image, QColor("black"), QColor("black"))
        second = artwork_surface_palette(image.copy(), QColor("black"), QColor("black"))

        self.assertGreaterEqual(len(first.ambient_colors), 3)
        self.assertLessEqual(len(first.ambient_colors), 6)
        self.assertEqual(first.ambient_colors, second.ambient_colors)
        for index, color in enumerate(first.ambient_colors):
            for other in first.ambient_colors[index + 1:]:
                self.assertGreaterEqual(_hue_distance(color, other), 42)

    def test_ambient_palette_caps_mixed_chromatic_and_neutral_bands_at_six(self):
        colors = tuple(QColor.fromHsv(hue, 210, 204) for hue in range(0, 360, 60)) + (
            QColor(10, 10, 10), QColor(125, 125, 125), QColor(240, 240, 240),
        )
        palette = artwork_surface_palette(
            _artwork(tuple(zip((20, 35, 50, 60, 70, 80, 90, 95, 100), colors))),
            QColor("black"), QColor("black"),
        )
        self.assertEqual(len(palette.ambient_colors), 6)

    def test_equal_area_ambient_order_is_independent_of_band_position(self):
        colors = (QColor(195, 30, 40), QColor(30, 195, 40), QColor(30, 40, 195))
        palettes = []
        for order in (colors, tuple(reversed(colors))):
            image = QImage(72, 72, QImage.Format.Format_RGB32)
            for index, color in enumerate(order):
                for y in range(index * 24, (index + 1) * 24):
                    for x in range(72):
                        image.setPixelColor(x, y, color)
            palettes.append(artwork_surface_palette(image, QColor("black"), QColor("black")))
        self.assertEqual(palettes[0].ambient_colors, palettes[1].ambient_colors)

    def test_ambient_palette_retains_three_source_gray_bands_without_chroma(self):
        shades = (QColor(15, 15, 15), QColor(112, 112, 112), QColor(238, 238, 238))
        palette = artwork_surface_palette(
            _artwork(tuple(zip((34, 67, 100), shades))),
            QColor(22, 188, 255), QColor(18, 60, 126),
        )

        self.assertEqual(set(color.value() for color in palette.ambient_colors), {15, 112, 238})
        for color in palette.ambient_colors:
            self.assertEqual(color.red(), color.green())
            self.assertEqual(color.green(), color.blue())
        # Source shades are retained independently of the established dark
        # lyric/background roles, which continue to stay within their bounds.
        self.assertLessEqual(palette.primary.value(), 90)

    def test_ambient_palette_discards_unsupported_and_transparent_pigments(self):
        source = QColor(162, 32, 45)
        image = QImage(72, 72, QImage.Format.Format_RGBA8888)
        image.fill(source)
        for y in range(36):
            for x in range(72):
                image.setPixelColor(x, y, QColor(10, 220, 230, 12))
        for y in range(3):
            for x in range(3):
                image.setPixelColor(x, y, QColor(180, 30, 200))
        palette = artwork_surface_palette(image, QColor("blue"), QColor("cyan"))

        self.assertEqual(palette.ambient_colors, (source,))

    def test_ambient_palette_retains_supported_small_accent(self):
        source, accent = QColor(95, 78, 139), QColor(235, 28, 55)
        image = _artwork(((98, source), (100, accent)))
        palette = artwork_surface_palette(image, QColor("black"), QColor("black"))

        self.assertEqual(palette.ambient_colors, (source, accent))

    def test_isolated_opaque_pixel_cannot_supply_the_ambient_palette(self):
        image = QImage(72, 72, QImage.Format.Format_RGBA8888)
        image.fill(QColor(0, 0, 0, 0))
        image.setPixelColor(3, 3, QColor("magenta"))
        primary, secondary = QColor(35, 70, 60), QColor(15, 30, 25)
        palette = artwork_surface_palette(image, primary, secondary)
        self.assertEqual(palette.ambient_colors, (primary, secondary))

    def test_ambient_dark_pigment_keeps_dominant_red_without_blue_shadow(self):
        image = _artwork((
            (47, QColor(42, 7, 15)), (85, QColor(8, 5, 5)),
            (95, QColor(81, 13, 24)), (100, QColor(13, 25, 65)),
        ))
        palette = artwork_surface_palette(image, QColor("blue"), QColor("cyan"))

        chromatic = tuple(color for color in palette.ambient_colors if color.hsvHue() >= 0)
        self.assertEqual(len(chromatic), 1)
        self.assertGreater(chromatic[0].red(), chromatic[0].blue() * 2)

    def test_missing_and_fully_transparent_ambient_palette_use_only_supplied_fallbacks(self):
        primary, secondary = QColor(120, 20, 40), QColor(50, 20, 20)
        transparent = QImage(72, 72, QImage.Format.Format_RGBA8888)
        transparent.fill(QColor(5, 230, 180, 0))
        for image in (QImage(), transparent):
            with self.subTest(missing=image.isNull()):
                palette = artwork_surface_palette(image, primary, secondary)
                self.assertEqual(palette.ambient_colors, (primary, secondary))
                self.assertEqual(palette.ambient_weights, (0.5, 0.5))
        identical = artwork_surface_palette(QImage(), primary, primary)
        self.assertEqual(identical.ambient_colors, (primary,))
        self.assertEqual(identical.ambient_weights, (1.0,))

    def test_bulk_rgba_samples_match_qt_pixel_color_across_source_formats(self):
        colors = (
            QColor(42, 7, 15, 255), QColor(9, 18, 67, 128),
            QColor(230, 141, 36, 247), QColor(252, 251, 249, 23),
        )
        for source_format in (
            QImage.Format.Format_RGB32,
            QImage.Format.Format_ARGB32,
            QImage.Format.Format_RGBA8888,
        ):
            with self.subTest(source_format=source_format):
                source = QImage(4, 1, source_format)
                for index, color in enumerate(colors):
                    source.setPixelColor(index, 0, color)
                sample = source.convertToFormat(QImage.Format.Format_RGBA8888)
                pixels = sample.constBits()
                for index in range(len(colors)):
                    offset = index * 4
                    direct = QColor(*pixels[offset:offset + 4])
                    self.assertEqual(direct, sample.pixelColor(index, 0))

    def test_dominant_is_vivid_then_next_distinct_by_area_and_accent_by_chroma(self):
        original_primary = QColor(172, 116, 186)
        source_secondary = QColor(196, 144, 47)
        source_accent = QColor(15, 231, 121)
        image = _artwork((
            (62, original_primary), (92, source_secondary), (100, source_accent),
        ))
        palette = artwork_surface_palette(image, QColor(20, 40, 60), QColor(10, 20, 30))

        self.assertLessEqual(_hue_distance(palette.primary, original_primary), 1)
        self.assertGreater(palette.primary.hsvSaturation(), original_primary.hsvSaturation())
        self.assertEqual(palette.secondary, source_secondary)
        self.assertEqual(palette.characteristic, source_accent)
        self.assertFalse(palette.uses_primary_fallback)

    def test_shadow_dominant_cover_selects_distinct_warm_secondary_and_sparse_accent(self):
        image = _artwork((
            (60, QColor(7, 8, 10)),
            (90, QColor(212, 155, 43)),
            (100, QColor(231, 35, 49)),
        ))
        palette = artwork_surface_palette(image, QColor(58, 68, 88), QColor(34, 44, 62))

        self.assertLess(abs(palette.primary.red() - palette.primary.blue()), 8)
        self.assertGreater(palette.secondary.red(), palette.secondary.blue() + 100)
        self.assertGreater(palette.characteristic.red(), palette.characteristic.green() + 100)

    def test_black_gold_cover_does_not_turn_tiny_green_ornament_into_neon_lyrics(self):
        image = _artwork((
            (62, QColor(10, 9, 8)),
            (88, QColor(87, 69, 41)),
            (98, QColor(178, 141, 71)),
            (100, QColor(18, 166, 70)),
        ))
        palette = artwork_surface_palette(image, QColor(30, 70, 140), QColor(14, 32, 78))
        self.assertLessEqual(_hue_distance(palette.characteristic, QColor(178, 141, 71)), 3)
        self.assertGreater(palette.characteristic.red(), palette.characteristic.green())
        self.assertGreater(palette.characteristic.green(), palette.characteristic.blue())
        backdrop = background_role_color(palette.primary)
        ink = future_lyric_color(
            palette.characteristic, backdrop,
            use_light=relative_luminance(backdrop) < 0.11,
            primary=palette.primary,
        )
        self.assertLessEqual(_hue_distance(ink, palette.characteristic), 1)
        self.assertGreaterEqual(_ink_contrast(ink, backdrop), 2.15)

    def test_near_black_navy_does_not_displace_distinct_secondary(self):
        image = _artwork((
            (45, QColor(5, 6, 7)),
            (75, QColor(13, 25, 65)),
            (90, QColor(227, 142, 34)),
            (100, QColor(220, 213, 199)),
        ))
        palette = artwork_surface_palette(image, QColor(58, 68, 88), QColor(34, 44, 62))

        self.assertGreater(palette.secondary.red(), palette.secondary.blue() + 100)
        self.assertGreater(palette.characteristic.red(), palette.characteristic.blue() + 100)

    def test_dark_red_album_keeps_chromatic_primary_instead_of_gray_shadow(self):
        image = _artwork((
            (65, QColor(42, 7, 15)),
            (90, QColor(81, 13, 24)),
            (100, QColor(8, 5, 5)),
        ))
        palette = artwork_surface_palette(image, QColor(30, 70, 140), QColor(12, 45, 92))
        backdrop = background_role_color(palette.primary)
        ink = future_lyric_color(
            palette.characteristic, backdrop,
            use_light=relative_luminance(backdrop) < 0.11,
            primary=palette.primary,
        )
        self.assertTrue(palette.uses_primary_fallback)
        self.assertGreater(palette.primary.red(), palette.primary.green() * 2)
        self.assertGreater(backdrop.red(), backdrop.green() * 2)
        self.assertLessEqual(_hue_distance(ink, palette.primary), 1)
        self.assertGreaterEqual(_ink_contrast(ink, backdrop), 2.15)

    def test_sparse_near_black_blue_shadow_does_not_color_a_dark_red_cover(self):
        # A dim, high-HSV-saturation shadow is not a visible second pigment.
        # This approximates the supplied low-key red portrait without keeping
        # a copyrighted screenshot in the test fixtures.
        image = _artwork((
            (47, QColor(42, 7, 15)),
            (85, QColor(8, 5, 5)),
            (95, QColor(81, 13, 24)),
            (100, QColor(13, 25, 65)),
        ))
        palette = artwork_surface_palette(image, QColor(30, 70, 140), QColor(12, 45, 92))

        self.assertTrue(palette.uses_primary_fallback)
        self.assertGreater(palette.primary.red(), palette.primary.blue() * 2)
        self.assertEqual(palette.secondary.hsvHue(), palette.primary.hsvHue())
        self.assertGreater(background_role_color(palette.primary).red(), 2 * background_role_color(palette.primary).blue())

    def test_visible_blue_companion_is_preserved_when_it_is_in_the_cover(self):
        image = _artwork((
            (70, QColor(132, 24, 35)),
            (100, QColor(35, 57, 122)),
        ))
        palette = artwork_surface_palette(image, QColor(30, 70, 140), QColor(12, 45, 92))

        self.assertFalse(palette.uses_primary_fallback)
        self.assertLessEqual(_hue_distance(palette.secondary, QColor(35, 57, 122)), 1)
        self.assertLessEqual(_hue_distance(palette.characteristic, QColor(35, 57, 122)), 1)

    def test_monochrome_and_missing_art_stay_neutral_or_use_supplied_fallback(self):
        monochrome = _artwork(((50, QColor(2, 2, 2)), (100, QColor(246, 246, 246))))
        palette = artwork_surface_palette(monochrome, QColor(58, 68, 88), QColor(34, 44, 62))
        for color in (palette.primary, palette.secondary, palette.characteristic):
            self.assertEqual(color.red(), color.green())
            self.assertEqual(color.green(), color.blue())

        missing = artwork_surface_palette(QImage(), QColor(120, 20, 40), QColor(50, 20, 20))
        self.assertEqual(missing.primary, QColor(120, 20, 40))
        self.assertEqual(missing.secondary, QColor(50, 20, 20))
        self.assertEqual(missing.characteristic, QColor(120, 20, 40))

    def test_same_hue_roles_use_primary_fallback_and_lightened_read_ink(self):
        # Saturation/brightness treatment changes RGB, so this deliberately
        # uses two red shades rather than exact equality.
        image = _artwork((
            (82, QColor(194, 28, 45)),
            (100, QColor(222, 66, 74)),
        ))
        palette = artwork_surface_palette(image, QColor(20, 40, 60), QColor(10, 20, 30))

        self.assertTrue(palette.uses_primary_fallback)
        self.assertEqual(palette.secondary.hsvHue(), palette.primary.hsvHue())
        self.assertLess(palette.secondary.value(), palette.primary.value())
        backdrop = background_role_color(palette.primary)
        ink = future_lyric_color(
            palette.characteristic,
            backdrop,
            use_light=relative_luminance(backdrop) < 0.11,
            primary=palette.primary,
        )
        self.assertEqual(ink.hsvHue(), palette.primary.hsvHue())
        self.assertGreaterEqual(ink.value(), palette.primary.value() - 12)
        self.assertGreaterEqual(_ink_contrast(ink, backdrop), 2.15)

    def test_real_characteristic_remains_tinted_and_readable_for_dark_and_light_art(self):
        for accent in (QColor(235, 30, 60), QColor(203, 241, 230), QColor(230, 182, 25)):
            for backdrop in (QColor(56, 56, 56), QColor(107, 69, 124), QColor(236, 238, 242)):
                with self.subTest(accent=accent.name(), backdrop=backdrop.name()):
                    ink = future_lyric_color(
                        accent, backdrop, use_light=relative_luminance(backdrop) < 0.11,
                        primary=QColor(150, 76, 175),
                    )
                    self.assertLessEqual(_hue_distance(ink, accent), 1)
                    self.assertGreaterEqual(ink.hsvSaturation(), 110)
                    self.assertGreaterEqual(_ink_contrast(ink, backdrop), 2.15)

    def test_distinct_vivid_sparse_accent_survives_noise_filter(self):
        image = _artwork((
            (97, QColor(95, 78, 139)),
            (99, QColor(235, 28, 55)),  # two percent: a real accent
            (100, QColor(3, 4, 5)),
        ))
        palette = artwork_surface_palette(image, QColor(1, 2, 3), QColor(4, 5, 6))

        self.assertFalse(palette.uses_primary_fallback)
        self.assertGreater(palette.characteristic.red(), palette.characteristic.blue() + 80)
        self.assertGreater(palette.secondary.red(), palette.secondary.blue() + 20)

    def test_transparent_pixels_do_not_create_a_blue_default(self):
        image = QImage(100, 100, QImage.Format.Format_RGBA8888)
        image.fill(QColor(0, 0, 0, 0))
        for y in range(25, 100):
            for x in range(100):
                image.setPixelColor(x, y, QColor(242, 176, 28, 255))
        palette = artwork_surface_palette(image, QColor(20, 40, 60), QColor(10, 20, 30))

        self.assertGreater(palette.primary.red(), palette.primary.blue() + 60)
        self.assertNotEqual(palette.primary, QColor(22, 188, 255))

    def test_near_monochrome_cover_retains_real_one_percent_vivid_detail(self):
        image = _artwork((
            (49, QColor(2, 2, 2)), (99, QColor(246, 246, 246)),
            (100, QColor(225, 25, 45)),
        ))
        palette = artwork_surface_palette(image, QColor(30, 70, 140), QColor(14, 32, 78))

        self.assertEqual(palette.primary.red(), palette.primary.blue())
        self.assertGreater(palette.characteristic.red(), palette.characteristic.blue() + 100)
        self.assertFalse(palette.uses_primary_fallback)

    def test_uniform_white_black_and_gray_never_pick_a_chromatic_fallback(self):
        for source in (QColor(0, 0, 0), QColor(255, 255, 255), QColor(126, 126, 126)):
            with self.subTest(source=source.name()):
                palette = artwork_surface_palette(
                    _artwork(((100, source),)), QColor(22, 188, 255), QColor(18, 60, 126),
                )
                self.assertTrue(palette.uses_primary_fallback)
                for color in (palette.primary, palette.secondary, palette.characteristic):
                    self.assertEqual(color.red(), color.green())
                    self.assertEqual(color.green(), color.blue())
                self.assertLessEqual(palette.primary.value(), 90)

    def test_reference_cover_roles_are_not_blue_when_reference_is_available(self):
        # The attachment is user-local, so this comparison is a no-op on CI
        # while still guarding the actual reference cover during desktop QA.
        path = Path(r"C:\Users\USER\AppData\Local\Temp\codex-clipboard-06a9def4-3775-4603-bab8-3b43bcb9bf9c.png")
        if not path.is_file():
            self.skipTest("local reference screenshot is not available")
        screenshot = QImage(str(path))
        self.assertFalse(screenshot.isNull())
        # The cover occupies this region in the supplied 1498x843 capture.
        cover = screenshot.copy(75, 75, 515, 515)
        palette = artwork_surface_palette(cover, QColor(50, 50, 50), QColor(30, 30, 30))
        self.assertTrue(260 <= palette.primary.hsvHue() <= 305)
        self.assertTrue(140 <= palette.secondary.hsvHue() <= 180)
        self.assertTrue(140 <= palette.characteristic.hsvHue() <= 180)
        self.assertFalse(palette.uses_primary_fallback)

    def test_dark_red_reference_cover_does_not_collapse_to_gray_when_available(self):
        path = Path(r"C:\Users\USER\AppData\Local\Temp\codex-clipboard-13974a43-079e-45a2-a18c-2407d6edecf8.png")
        if not path.is_file():
            self.skipTest("local reference screenshot is not available")
        screenshot = QImage(str(path))
        self.assertFalse(screenshot.isNull())
        cover = screenshot.copy(68, 69, 467, 467)
        palette = artwork_surface_palette(cover, QColor(30, 70, 140), QColor(12, 45, 92))
        self.assertTrue(palette.uses_primary_fallback)
        self.assertGreater(palette.primary.red(), palette.primary.green() * 2)
        self.assertEqual(palette.secondary.hsvHue(), palette.primary.hsvHue())
        self.assertGreater(background_role_color(palette.primary).red(), background_role_color(palette.primary).green() * 2)


if __name__ == "__main__":
    unittest.main()
