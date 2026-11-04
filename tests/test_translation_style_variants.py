from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from PySide6.QtGui import QFont, QImage
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
from local.translation_style_variants import resolve_translation_style
from tests.test_music_lyric_visual_layers import _LayerOwner


class TranslationStyleTests(unittest.TestCase):
    def test_exact_research_parameters_and_case_insensitive_selector(self):
        for name, values in (
            ("A", (.50, .30, .18, 11.4, 1., False, .25, .40, .35, .97)),
            ("b", (.62, .78, .42, 8., .50, False, 0., .32, .25, .99)),
            (" C ", (.55, .55, .28, 8.36, .70, False, .08, .42, .40, .98)),
        ):
            with self.subTest(name=name), patch.dict(os.environ, {"GLUSPOT_TRANSLATION_VARIANT": name}):
                style = resolve_translation_style()
                self.assertEqual(style.name, name.strip().lower())
                self.assertEqual(style.weight, QFont.Weight.Normal)
                actual = (style.size_ratio, style.alpha_active, style.alpha_inactive,
                          style.gap_px_fn(38., 1.), style.blur_factor, style.karaoke_on_translation,
                          style.opacity_stagger_s, style.entry_duration_s, style.slide_factor,
                          style.inactive_scale)
                for found, expected in zip(actual, values):
                    self.assertAlmostEqual(found, expected)

    def test_classic_aliases_retain_baseline(self):
        for name in ("classic", "CLASSIC"):
            with self.subTest(name=name), patch.dict(os.environ):
                os.environ.pop("GLUSPOT_TRANSLATION_VARIANT", None)
                if name is not None:
                    os.environ["GLUSPOT_TRANSLATION_VARIANT"] = name
                style = resolve_translation_style()
                self.assertEqual(style.name, "classic")
                self.assertAlmostEqual(38 * style.size_ratio, (38 - 6) * .5)
                self.assertEqual((style.alpha_active, style.alpha_inactive, style.blur_factor,
                                  style.karaoke_on_translation, style.opacity_stagger_s,
                                  style.entry_duration_s, style.slide_factor, style.inactive_scale),
                                 (1., 1., 1., True, 0., .48, .55, 1.))


    def test_unset_empty_and_unknown_select_product_default_a(self):
        for name in (None, "", "  ", "unknown"):
            with self.subTest(name=name), patch.dict(os.environ):
                os.environ.pop("GLUSPOT_TRANSLATION_VARIANT", None)
                if name is not None:
                    os.environ["GLUSPOT_TRANSLATION_VARIANT"] = name
                self.assertEqual(resolve_translation_style().name, "a")


class TranslationVariantPaintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _surface(self, name):
        with patch.dict(os.environ, {"GLUSPOT_TRANSLATION_VARIANT": name}), \
                patch("time.monotonic", return_value=100.):
            owner = _LayerOwner()
            owner.timed_lyrics = [
                (0., "Soft light\n翻译：柔光"),
                (8., "Moving slowly\n翻译：慢行"),
                (17., "Hold this color\n翻译：颜色"),
                (26., "Let it stay\n翻译：留下"),
            ]
            owner.timed_lyric_units = [()] * 4
            owner.timed_lyric_timing_kinds = ["line"] * 4
            owner.media_position_source = "local-seek"
            owner.media_position_seconds = 12.
            owner.media_position_sampled_at = 100.
            window = MusicSurfaceWindow(owner)
            window.resize(960, 540)
        self.addCleanup(owner.close)
        self.addCleanup(window.shutdown)
        return owner, window

    def _render(self, owner, window, now):
        owner.media_position_sampled_at = now
        with patch("time.monotonic", return_value=now):
            window.render(QImage(960, 540, QImage.Format.Format_RGB32))

    def test_variants_paint_solid_translation_with_active_alpha_and_scaled_blur(self):
        for name in "abc":
            with self.subTest(variant=name):
                owner, window = self._surface(name)
                self._render(owner, window, 100.)
                with (
                    patch.object(window, "_paint_wrapped_lifted_lyric",
                                 wraps=window._paint_wrapped_lifted_lyric) as karaoke,
                    patch.object(window, "_draw_sprung_cached_wrapped_layout",
                                 wraps=window._draw_sprung_cached_wrapped_layout) as solid,
                ):
                    self._render(owner, window, 101.)
                self.assertFalse(any(call.args[2] in ("柔光", "慢行", "颜色", "留下")
                                     for call in karaoke.call_args_list))
                active = [call for call in solid.call_args_list if call.args[3] == "慢行"]
                self.assertTrue(active)
                self.assertEqual(active[0].kwargs.get("blur_level"), 0.)
                # Observe actual opacity while painting, not the restored painter.
                observed = []
                original = window._draw_sprung_cached_wrapped_layout

                def capture(painter, layout, origin, text, *args, **kwargs):
                    observed.append((text, painter.opacity(), kwargs.get("blur_level", 0.)))
                    return original(painter, layout, origin, text, *args, **kwargs)

                with patch.object(window, "_draw_sprung_cached_wrapped_layout", side_effect=capture):
                    self._render(owner, window, 101.1)
                self.assertAlmostEqual(next(alpha for text, alpha, _ in observed if text == "慢行"),
                                       window._translation_style.alpha_active)
                past_blur = next(blur for text, _, blur in observed if text == "柔光")
                self.assertAlmostEqual(past_blur, window._translation_style.blur_factor)
                main_height = window._song_layouts[1][1]
                secondary = window._song_secondary_layouts[1]
                scale = window._surface_scale()
                self.assertAlmostEqual(secondary[2] - main_height,
                                       window._translation_style.gap_px_fn(38. * scale, scale))
                self.assertEqual(secondary[0].font().pixelSize(),
                                 round(38. * scale * window._translation_style.size_ratio))

    def test_opacity_stagger_keeps_height_and_position_running(self):
        for name in "abc":
            with self.subTest(variant=name):
                owner, window = self._surface(name)
                self._render(owner, window, 100.)
                style = window._translation_style
                height, alpha, position = window._translation_layer_pose(1, "慢行", 100.04)
                self.assertEqual(height, 1.)
                self.assertGreater(position, 0.)
                self.assertEqual(alpha == 0., style.opacity_stagger_s > .04)
                self.assertEqual(window._translation_layer_pose(1, "慢行", 100.5), (1., 1., 1.))

    def test_cover_layout_shrinks_only_main_font(self):
        for name in ("classic", "a", "b", "c"):
            with self.subTest(variant=name):
                owner, window = self._surface(name)
                window.set_content_layout("cover_lyrics", animate=False)
                self._render(owner, window, 100.)
                scale = window._surface_scale()
                self.assertEqual(window._song_layouts[1][0].font().pixelSize(), round(38 * scale * .74))
                expected = ((38 - 6) * scale * .5 if name == "classic"
                            else 38 * scale * window._translation_style.size_ratio)
                self.assertEqual(window._song_secondary_layouts[1][0].font().pixelSize(), round(expected))

    def test_translated_aside_uses_solid_path_and_reserves_variant_gap(self):
        for name in "abc":
            with self.subTest(variant=name):
                owner, window = self._surface(name)
                owner.timed_lyrics[1] = (8., "Moving slowly (echo)\n翻译：缓慢（回声）前行")
                self._render(owner, window, 100.)
                with (
                    patch.object(window, "_paint_wrapped_lifted_lyric",
                                 wraps=window._paint_wrapped_lifted_lyric) as karaoke,
                    patch.object(window, "_paint_parenthetical_highlight",
                                 wraps=window._paint_parenthetical_highlight) as highlight,
                    patch.object(window, "_draw_sprung_cached_wrapped_layout",
                                 wraps=window._draw_sprung_cached_wrapped_layout) as solid,
                ):
                    self._render(owner, window, 101.)
                for renderer in (karaoke, highlight):
                    self.assertFalse(any(call.args[2] in ("回声", "缓慢前行")
                                         for call in renderer.call_args_list))
                self.assertTrue(any(call.args[3] == "回声" for call in solid.call_args_list))
                _, aside_height, aside_top = window._song_parenthetical_layouts[1]
                _, _, translation_top = window._song_parenthetical_translation_layouts[1]
                scale = window._surface_scale()
                self.assertAlmostEqual(translation_top - aside_top - aside_height,
                                       window._translation_style.gap_px_fn(38 * scale, scale))


if __name__ == "__main__":
    unittest.main()
