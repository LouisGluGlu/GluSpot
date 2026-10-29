"""The crossover is shaped content with a font-sized reversal margin."""
import unittest

from PySide6.QtGui import QFont, QFontMetricsF
from PySide6.QtWidgets import QApplication

from local.music_lyric_fit import (
    CJK_PROBE, LATIN_PROBE, LYRIC_MIN_CJK, LYRIC_MIN_LATIN_WORDS,
    _advance, _probes, cjk_unit, fits, latin_unit, required_width,
)


class LyricFitWidthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def font(self, size=38):
        font = QFont("DejaVu Sans")
        font.setPixelSize(size)
        font.setWeight(QFont.Weight.Bold)
        return font

    def test_minimum_holds_five_cjk_characters_and_three_probe_words(self):
        font = self.font()
        required = required_width(font)
        self.assertEqual((LYRIC_MIN_CJK, LYRIC_MIN_LATIN_WORDS), (5, 3))
        self.assertLessEqual(_advance(CJK_PROBE[:5], font), required)
        self.assertLessEqual(_advance(LATIN_PROBE, font), required)
        self.assertTrue(fits(required, font, previous=None))
        self.assertFalse(fits(_advance(CJK_PROBE[:4], font), font, previous=None))
        # A two-word width is not itself evidence that three words will fit.
        self.assertFalse(fits(_advance("sing on", font), font, previous=None))

    def test_cjk_probe_includes_the_renderers_per_range_spacing(self):
        font = self.font()
        unspaced = QFontMetricsF(font).horizontalAdvance(CJK_PROBE) / len(CJK_PROBE)
        self.assertGreater(cjk_unit(font), unspaced)
        self.assertAlmostEqual(cjk_unit(font) * len(CJK_PROBE), _advance(CJK_PROBE, font))

    def test_requirement_and_deadband_grow_with_font_size(self):
        widths = [required_width(self.font(size)) for size in (18, 26, 38, 52)]
        self.assertEqual(widths, sorted(set(widths)))
        for size in (18, 38, 52):
            font = self.font(size)
            required, half_glyph = required_width(font), cjk_unit(font) / 2
            for previous, threshold in ((False, required - half_glyph),
                                        (True, required + half_glyph)):
                self.assertFalse(fits(threshold - .001, font, previous=previous))
                self.assertTrue(fits(threshold, font, previous=previous))

    def test_probe_cache_is_shared_and_device_and_font_scoped(self):
        font = self.font()
        _probes.cache_clear()
        first = required_width(font, dpr=1.)
        cjk_unit(font, dpr=1.)
        latin_unit(font, dpr=1.)
        self.assertEqual(first, required_width(font, dpr=1.))
        self.assertEqual(_probes.cache_info().misses, 1)
        self.assertEqual(_probes.cache_info().hits, 3)
        self.assertEqual(first, required_width(font, dpr=2.))
        self.assertEqual(_probes.cache_info().misses, 2)
