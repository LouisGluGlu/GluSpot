"""Exact pre-optimization roles, source tones and opacity/neighbor evidence."""

import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtGui import QColor, QImage

import local.music_surface_palette as palette_module


def signature(palette):
    return [[color.rgba() for color in (palette.primary, palette.secondary, palette.characteristic)],
            [color.rgba() for color in palette.ambient_colors], list(palette.ambient_weights),
            None if palette.source_primary is None else palette.source_primary.rgba()]


class MusicPaletteAggregationTests(unittest.TestCase):
    def test_rgb8_hsv_exactly_matches_runtime_qt_across_random_and_threshold_colors(self):
        rng = np.random.default_rng(20261009)
        random = rng.integers(0, 256, (100000, 3), dtype=np.uint8)
        boundaries = np.array([
            (red, green, blue)
            for red in (0, 1, 11, 12, 15, 25, 26, 34, 35, 45, 46, 47, 48, 63, 64,
                        127, 128, 208, 209, 210, 235, 236, 254, 255)
            for green in (0, 11, 12, 25, 35, 45, 63, 127, 208, 235, 255)
            for blue in (0, 11, 12, 25, 35, 45, 63, 127, 208, 235, 255)
        ], dtype=np.uint8)
        rgb = np.concatenate((random, boundaries), axis=0)
        actual = palette_module._qt_rgb8_hsv_samples(rgb)
        for index, (red, green, blue) in enumerate(rgb):
            color = QColor(int(red), int(green), int(blue))
            self.assertEqual(tuple(actual[index]), (*color.getHsvF()[:3], color.hsvSaturation()),
                             f"RGB8 {red}, {green}, {blue}")

    def test_exact_pre_optimization_roles_across_textures_formats_and_alpha(self):
        reference = json.loads((Path(__file__).parent / "fixtures/music_palette_vectorization_baseline.json")
                               .read_text(encoding="utf8"))
        rng = np.random.default_rng(reference["seed"])
        formats = (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32,
                   QImage.Format.Format_ARGB32_Premultiplied, QImage.Format.Format_RGBA8888)
        for fixture in reference["palettes"]:
            index = fixture["index"]
            width, height = (127, 83) if index % 3 == 0 else (72, 72)
            pixels = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
            if index % 3 == 0:
                pixels[:, :, :3] = (pixels[:, :, :3] // 24) * 24
            if index % 5 == 0:
                pixels[:, :, :3] = (pixels[:, :, :1] // 7) * 7
            if index % 7 == 0:
                pixels[:, :, 3] = rng.choice([0, 12, 23, 24, 64, 127, 128, 254, 255],
                                             (height, width)).astype(np.uint8)
            if index % 11 == 0:
                pixels[:, :, 3] = 0
            image = QImage(pixels.tobytes(), width, height, width * 4,
                           QImage.Format.Format_RGBA8888).copy().convertToFormat(formats[index % 4])
            with self.subTest(index=index, format=formats[index % 4]):
                result = palette_module.artwork_surface_palette(image, QColor(44, 12, 32), QColor(9, 67, 18))
                self.assertEqual(signature(result), fixture["expected"])

    def test_lazy_ink_candidates_preserve_pre_optimization_selection_and_rgba(self):
        fixtures = json.loads((Path(__file__).parent / "fixtures/music_future_ink_lazy_baseline.json")
                              .read_text(encoding="utf8"))
        for index, fixture in enumerate(fixtures):
            with self.subTest(index=index):
                color = palette_module.future_lyric_color(
                    QColor(*fixture["accent"]), QColor(*fixture["backdrop"]),
                    primary=None if fixture["primary"] is None else QColor(*fixture["primary"]),
                    use_light=fixture["use_light"], require_light=fixture["require_light"])
                self.assertEqual(list(color.getRgb()), fixture["expected"])

    def test_stride_padding_cannot_change_sample_or_neighbor_support(self):
        width, height, stride = 72, 72, 72 * 4 + 24
        rows = np.full((height, stride), 255, dtype=np.uint8)
        pixels = rows[:, :width * 4].reshape(height, width, 4)
        pixels[:] = (15, 28, 47, 255)
        pixels[10:20, 11:16] = (211, 18, 80, 128)
        padded = QImage(rows.tobytes(), width, height, stride, QImage.Format.Format_RGBA8888)
        contiguous = QImage(pixels.copy().tobytes(), width, height, width * 4, QImage.Format.Format_RGBA8888)
        first = palette_module._sample_color_groups(padded)
        self.assertEqual(first, palette_module._sample_color_groups(contiguous))

    def test_palette_cache_reuses_roles_without_exposing_mutable_colors(self):
        image = QImage(180, 180, QImage.Format.Format_RGB32)
        image.fill(QColor(16, 129, 207))
        primary, secondary = QColor(51, 36, 28), QColor(11, 15, 47)
        original = palette_module.artwork_surface_palette(image, primary, secondary)
        expected = signature(original)
        original.primary.setAlpha(0)
        original.ambient_colors[0].setRgb(255, 0, 0)
        original.source_primary.setRgb(255, 255, 255)
        with patch.object(palette_module, "_extract_artwork_surface_palette", side_effect=AssertionError("cold extraction")):
            self.assertEqual(signature(palette_module.artwork_surface_palette(image, primary, secondary)), expected)

    def test_image_mutation_and_fallback_changes_cannot_reuse_old_roles(self):
        image = QImage(72, 72, QImage.Format.Format_RGB32)
        image.fill(QColor(211, 35, 16))
        primary, secondary = QColor(51, 36, 28), QColor(11, 15, 47)
        original = signature(palette_module.artwork_surface_palette(image, primary, secondary))
        image.fill(QColor(11, 35, 216))
        self.assertNotEqual(signature(palette_module.artwork_surface_palette(image, primary, secondary)), original)
        missing = QImage()
        first = palette_module.artwork_surface_palette(missing, primary, secondary)
        primary.setRgb(55, 82, 24)
        second = palette_module.artwork_surface_palette(missing, primary, secondary)
        self.assertNotEqual(first.source_primary, second.source_primary)

    def test_recent_palette_cache_retains_only_bounded_small_roles(self):
        primary, secondary = QColor(51, 36, 28), QColor(11, 15, 47)
        for index in range(palette_module._ARTWORK_PALETTE_CACHE_LIMIT + 5):
            image = QImage(8, 8, QImage.Format.Format_RGB32)
            image.fill(QColor.fromHsv(index * 5 % 360, 255, 180))
            palette_module.artwork_surface_palette(image, primary, secondary)
        self.assertLessEqual(len(palette_module._ARTWORK_PALETTE_CACHE), palette_module._ARTWORK_PALETTE_CACHE_LIMIT)

    def test_invalid_black_and_high_precision_fallbacks_do_not_collide(self):
        missing = QImage()
        secondary = QColor(9, 15, 24)
        colors = (QColor(), QColor("black"), QColor.fromRgbF(.40001, .20001, .60001),
                  QColor.fromRgbF(.40005, .20005, .60005))
        self.assertEqual(colors[0].rgba(), colors[1].rgba())
        self.assertEqual(colors[2].rgba(), colors[3].rgba())
        for source in colors:
            actual = palette_module.artwork_surface_palette(missing, source, secondary)
            self.assertEqual(actual.primary.isValid(), source.isValid())
            self.assertEqual(actual.primary.spec(), source.spec())
            self.assertEqual(actual.primary.getRgbF(), source.getRgbF())
            self.assertEqual(actual.source_primary.getRgbF(), source.getRgbF())

    def test_native_hsv_hsl_cmyk_fallback_parameters_survive_equal_rgb_cache_keys(self):
        pairs = (
            (QColor.fromHsv(0, 0, 128), QColor.fromHsv(180, 0, 128)),
            (QColor.fromHsl(0, 0, 128), QColor.fromHsl(180, 0, 128)),
            (QColor.fromCmyk(0, 0, 0, 255), QColor.fromCmyk(170, 90, 220, 255)),
        )
        missing = QImage()
        secondary = QColor(9, 15, 24)
        for first, second in pairs:
            self.assertEqual(first.spec(), second.spec())
            self.assertEqual(first.getRgbF(), second.getRgbF())
            self.assertNotEqual(first, second)
            for source in (first, second):
                actual = palette_module.artwork_surface_palette(missing, source, secondary)
                self.assertEqual(actual.primary, source)
                self.assertEqual(actual.characteristic, source)
                self.assertEqual(actual.source_primary, source)


if __name__ == "__main__":
    unittest.main()
