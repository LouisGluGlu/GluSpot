from __future__ import annotations

import unittest
from pathlib import Path

from PySide6.QtGui import QColor, QImage

from local.music_surface_palette import (
    artwork_surface_palette,
    background_role_color,
    future_lyric_color,
    relative_luminance,
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
