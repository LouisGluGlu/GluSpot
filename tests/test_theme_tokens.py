"""Clear/tint tokens, contrast, and interruptible theme interpolation."""
from __future__ import annotations

from pathlib import Path
import unittest

from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication

from local import ui_theme
from local.theme_tokens import (
    LIGHT_MUTED,
    LIGHT_TEXT,
    DARK_TEXT,
    backdrop_needs_dark_ink,
    chrome_qss,
    composite,
    contrast_ratio,
    glass_edge_rgba,
    glass_tint_amount,
    interpolate_tokens,
    readable_ink,
    relative_luminance,
    resolve_tokens,
    themed_base,
)


ROOT = Path(__file__).resolve().parents[1]
DARK_LITERALS = ("#111827", "#181d29", "#24262b", "#0b0c0e", "12, 14, 20", "#1c1c1e")


class ThemeTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        ui_theme.theme_transition().snap(resolve_tokens(True, 50))

    def test_light_and_dark_bases_are_different_opaque_shades(self):
        light = themed_base(False)
        dark = themed_base(True)
        self.assertGreater(relative_luminance(light), 0.85)
        self.assertLess(relative_luminance(dark), 0.08)
        self.assertNotEqual(light, dark)
        tinted = themed_base(False, (40, 90, 180))
        self.assertGreater(min(tinted), 245)
        self.assertNotEqual(tinted, light)

    def test_light_default_glass_is_more_solid_than_dark(self):
        from local.theme_tokens import glass_fill_rgba
        self.assertGreater(glass_tint_amount(50, light=True), glass_tint_amount(50, light=False))
        light_fill = glass_fill_rgba(False, 50)
        dark_fill = glass_fill_rgba(True, 50)
        self.assertGreaterEqual(light_fill[3], 220)
        self.assertLess(dark_fill[3], 90)
        self.assertGreaterEqual(contrast_ratio(LIGHT_TEXT, composite(light_fill, (248, 248, 250))), 4.5)
        self.assertEqual(glass_tint_amount(0, light=True), 0)
        self.assertEqual(glass_tint_amount(100, light=False), 1)
        self.assertAlmostEqual(glass_tint_amount(100, light=True), 1)

    def test_text_contrast_holds_at_both_slider_ends(self):
        for dark in (False, True):
            for level in (0, 100):
                with self.subTest(dark=dark, level=level):
                    tokens = resolve_tokens(dark, level, accent=(180, 40, 70))
                    glass = composite(tokens.glass_fill, tokens.background)
                    self.assertGreaterEqual(contrast_ratio(tokens.text, tokens.background), 4.5)
                    self.assertGreaterEqual(contrast_ratio(tokens.text, glass), 4.5)
                    self.assertGreaterEqual(contrast_ratio(tokens.icon, glass), 4.5)
                    self.assertGreaterEqual(contrast_ratio(tokens.icon, tokens.background), 4.5)
                    if level == 100:
                        self.assertGreaterEqual(tokens.glass_fill[3], 250)
                    else:
                        self.assertLess(tokens.glass_fill[3], 80)

    def test_interpolation_is_continuous_and_interruptible(self):
        dark = resolve_tokens(True, 20)
        light = resolve_tokens(False, 80)
        start = interpolate_tokens(dark, light, 0)
        end = interpolate_tokens(dark, light, 1)
        mid = interpolate_tokens(dark, light, 0.5)
        self.assertEqual(start.background, dark.background)
        self.assertEqual(end.background, light.background)
        self.assertEqual(end.text, LIGHT_TEXT)
        self.assertEqual(start.text, DARK_TEXT)
        self.assertNotEqual(mid.background, dark.background)
        self.assertNotEqual(mid.background, light.background)
        self.assertLess(mid.shade, 1)
        self.assertGreater(mid.shade, 0)
        transition = ui_theme.theme_transition()
        transition.snap(dark)
        transition.retarget(light, animate=True)
        transition.set_progress_for_test(0.35)
        current = transition.current()
        transition.retarget(dark, animate=True)
        self.assertEqual(transition._from.background, current.background)
        self.assertEqual(transition._to.background, dark.background)
        self.assertTrue(transition.active)
        transition.snap(dark)
        self.assertFalse(transition.active)
        animation = transition._animation
        if animation is not None:
            self.assertEqual(animation.state(), QAbstractAnimation.State.Stopped)

    def test_light_chrome_strings_do_not_use_dark_literals(self):
        blob = " ".join(chrome_qss(resolve_tokens(False, 50)).values()).lower()
        for literal in DARK_LITERALS:
            self.assertNotIn(literal, blob)

    def test_themed_pages_have_a_light_field_path(self):
        library = (ROOT / "local" / "music_library_window.py").read_text(encoding="utf-8")
        login = (ROOT / "local" / "playback_entry_window.py").read_text(encoding="utf-8")
        settings = (ROOT / "local" / "settings_window.py").read_text(encoding="utf-8")
        self.assertIn("paint_light_backdrop", library)
        self.assertIn("paint_light_backdrop", login)
        self.assertIn("mac_preferences_theme(False)", settings)
        self.assertIn("opacity=1.", login)

    def test_white_background_ink_stays_readable(self):
        white = (255, 255, 255)
        near_white = (244, 244, 246)
        dark = (22, 24, 28)
        self.assertTrue(backdrop_needs_dark_ink(white))
        self.assertTrue(backdrop_needs_dark_ink(near_white))
        self.assertFalse(backdrop_needs_dark_ink(dark))
        self.assertEqual(readable_ink(white), LIGHT_TEXT)
        self.assertEqual(readable_ink(near_white, secondary=True), LIGHT_MUTED)
        self.assertGreaterEqual(contrast_ratio(readable_ink(white), white), 4.5)
        self.assertGreaterEqual(contrast_ratio(readable_ink(near_white, secondary=True), near_white), 3.0)
        self.assertEqual(readable_ink(dark), DARK_TEXT)
        edge = glass_edge_rgba(False, 50)
        self.assertLess(edge[0], 80)
        self.assertGreater(edge[3], 40)


if __name__ == "__main__":
    unittest.main()
