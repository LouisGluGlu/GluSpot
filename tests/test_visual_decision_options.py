"""Decision candidates preserve the pinned defaults and their visual boundaries."""
from dataclasses import asdict, replace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from _demos.apple_lyrics.timeline import AppleKnobs, APPLE_KNOB_PRESETS, displayed_focus
from local import broadcast_indicator, music_surface_layout
from local.broadcast_indicator import broadcast_face_colors, paint_broadcast_indicator
from local.music_surface_layout import layout_g_targets
from local.music_surface_palette import relative_luminance


FONT_MEASURES = dict(
    title_font_height=42.0, title_font_pixel_size=36.0,
    artist_font_height=25.0, artist_font_pixel_size=22.0, time_font_height=12.0,
)


class CoverGrowthDecisionTests(unittest.TestCase):
    def targets(self, mode=None, reveal=0.0, size=(960, 540), **kwargs):
        return layout_g_targets(*size, **FONT_MEASURES, control_reveal=reveal,
                                cover_growth_mode=mode, **kwargs)

    def test_default_keeps_factor_title_gain_and_capped_hidden_width(self):
        self.assertEqual(music_surface_layout.LAYOUT_G_COVER_GROWTH_MODE, "current")
        self.assertEqual(music_surface_layout.LAYOUT_G_COVER_GROWTH_FACTORS["current"], 0.30)
        resting, hidden = self.targets(reveal=1.0), self.targets()
        self.assertEqual(hidden, self.targets("current"))
        self.assertAlmostEqual(resting.cover.w, 269.287598944591, delta=0.05)
        # Hidden controls grow the sleeve only as far as its left inset can
        # still match the top inset before the lyric lane.
        self.assertGreater(hidden.cover.w, resting.cover.w)
        self.assertLess(hidden.cover.w, resting.cover.w * 1.16)
        # Hidden: the cover, title and artist sit centred in the window and
        # the left inset keeps its shown value (no longer equal to the top).
        group = hidden.artist.y + hidden.artist.h - hidden.cover.y
        self.assertAlmostEqual(hidden.cover.y, (540.0 - group) / 2.0, delta=0.75)
        self.assertLess(hidden.cover.x, hidden.cover.y)
        partial = self.targets(reveal=0.85)
        self.assertGreaterEqual(partial.cover.w, resting.cover.w)
        self.assertLessEqual(partial.cover.w, hidden.cover.w)
        self.assertAlmostEqual(hidden.title_size / resting.title_size, 1.18)

    def test_options_are_distinct_and_visible_controls_stay_identical(self):
        resting = self.targets(reveal=1.0)
        self.assertEqual(self.targets("none").cover.w, resting.cover.w)
        large, current = self.targets("large"), self.targets("current")
        self.assertGreater(large.cover.w, resting.cover.w)
        self.assertGreater(current.cover.w, resting.cover.w)
        self.assertLess(abs(large.cover.w - current.cover.w), 2.0)
        self.assertLessEqual(large.cover.x, current.cover.x + 2.0)
        self.assertEqual(large.title_size, current.title_size)
        for mode in music_surface_layout.LAYOUT_G_COVER_GROWTH_FACTORS:
            self.assertEqual(self.targets(mode, reveal=1.0), resting)

    def test_all_modes_fit_lane_at_reveal_steps_and_translated_origins(self):
        for size in ((440, 480), (715, 540), (960, 540), (995, 690), (1920, 1080)):
            for mode in music_surface_layout.LAYOUT_G_COVER_GROWTH_FACTORS:
                widths = []
                for step in range(21):
                    with self.subTest(size=size, mode=mode, step=step):
                        target = self.targets(mode, 1 - step / 20, size, origin_x=27, origin_y=19)
                        self.assertGreaterEqual(target.cover.x, 27)
                        self.assertGreaterEqual(target.cover.y, 19)
                        self.assertLessEqual(target.cover.x + target.cover.w, 27 + size[0] - 1)
                        if step == 0:
                            lane_right = 27 + 44.6 * target.unit
                            if target.cover.x + target.cover.w < lane_right - 1.0:
                                self.assertAlmostEqual(target.cover.x - 27, target.cover.y - 19, delta=0.75)
                            else:
                                self.assertAlmostEqual(target.cover.x + target.cover.w, lane_right, delta=1.0)
                        if step == 20:
                            self.assertLessEqual(
                                target.cover.x + target.cover.w, 27 + 44.6 * target.unit + 0.6,
                            )
                        self.assertLessEqual(target.artist.y + target.artist.h, 19 + size[1])
                        widths.append(target.cover.w)
                self.assertEqual(widths, sorted(widths))

    def test_named_constant_selects_omitted_mode(self):
        with patch.object(music_surface_layout, "LAYOUT_G_COVER_GROWTH_MODE", "none"):
            self.assertEqual(self.targets(), self.targets("none"))


class FaceBodyDecisionTests(unittest.TestCase):
    accent = QColor(92, 158, 232)

    def test_default_is_the_vivid_theme_body_and_white_eyes(self):
        self.assertEqual(broadcast_indicator.BROADCAST_FACE_BODY_MODE, "accent")
        body, eyes = broadcast_face_colors(self.accent)
        self.assertEqual(body.getRgb(), (69, 117, 170, 255))
        self.assertEqual(eyes.getRgb(), (245, 246, 248, 255))
        self.assertEqual(broadcast_face_colors(QColor())[0].getRgb(), (10, 10, 12, 255))
        self.assertEqual(broadcast_face_colors(QColor("white"))[0].getRgb(), (115, 115, 115, 255))
        near_black = broadcast_face_colors(self.accent, "current")[0]
        self.assertGreater(relative_luminance(body), relative_luminance(near_black) * 3)

    def test_alternatives_are_visibly_different(self):
        current, eyes = broadcast_face_colors(self.accent, "current")
        accent, accent_eyes = broadcast_face_colors(self.accent, "accent")
        warm, warm_eyes = broadcast_face_colors(self.accent, "warm")
        self.assertEqual(accent.getRgb(), (69, 117, 170, 255))
        self.assertEqual(warm.getRgb(), (112, 100, 92, 255))
        self.assertEqual((eyes, eyes), (accent_eyes, warm_eyes))
        self.assertGreater(relative_luminance(accent), relative_luminance(current) * 3)
        self.assertGreater(relative_luminance(warm), relative_luminance(current) * 3)

    def test_white_eyes_and_white_cover_keep_contrast_for_bright_accents(self):
        for source in ("white", "yellow", "cyan", "magenta", "red", "blue", "black", "#5c9ee8"):
            for mode in broadcast_indicator.BROADCAST_FACE_BODY_MODES:
                with self.subTest(source=source, mode=mode):
                    if mode == "album":
                        # Album mode keeps the colour its caller separated:
                        # the cover colour held at or under 0.32 luminance.
                        separated = broadcast_indicator.separated_face_color(QColor(source), QColor(20, 24, 30))
                        body, eyes = broadcast_face_colors(separated, mode)
                        self.assertEqual(body, separated)
                        self.assertLessEqual(relative_luminance(body), 0.33)
                        self.assertGreater((relative_luminance(eyes) + .05) / (relative_luminance(body) + .05), 2.4)
                        continue
                    body, eyes = broadcast_face_colors(QColor(source), mode)
                    luminance = relative_luminance(body)
                    self.assertLessEqual(luminance, 0.18)
                    self.assertGreater((relative_luminance(eyes) + .05) / (luminance + .05), 4.3)
                    self.assertGreater(1.05 / (luminance + .05), 4.5)

    def test_real_indicator_painter_uses_each_mode(self):
        for mode in broadcast_indicator.BROADCAST_FACE_BODY_MODES:
            image = QImage(128, 128, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                paint_broadcast_indicator(
                    painter, QRectF(0, 0, 128, 128), 7,
                    style="face", state="idle", elapsed=1.2, color=self.accent,
                    ring_painter=None, body_mode=mode,
                )
            # The body is shaded now (a soft highlight up-left, a deeper rim);
            # just below the centre it stays within a few levels of the body colour.
            painted = image.pixelColor(64, 90).getRgb()[:3]
            body = broadcast_face_colors(self.accent, mode)[0].getRgb()[:3]
            self.assertTrue(all(abs(a - b) <= 14 for a, b in zip(painted, body)), (mode, painted, body))

    def test_named_constant_selects_omitted_mode(self):
        with patch.object(broadcast_indicator, "BROADCAST_FACE_BODY_MODE", "warm"):
            self.assertEqual(broadcast_face_colors(self.accent), broadcast_face_colors(self.accent, "warm"))


class ApplePresetDecisionTests(unittest.TestCase):
    def test_every_default_field_is_preserved(self):
        self.assertEqual(asdict(AppleKnobs()), dict(
            block_gap_ratio=1.75, translation_gap_ratio=0.25, other_scale=0.88,
            blur_px=2.2, unread_alpha=0.55, feather_chars=0.75,
            scroll_ms=350.0, lead_ms=350.0,
        ))
        self.assertEqual(AppleKnobs.from_preset(), AppleKnobs())
        self.assertEqual(AppleKnobs.from_preset("current"), AppleKnobs())

    def test_presets_change_multiple_knobs_in_the_intended_direction(self):
        current = AppleKnobs()
        soft, dramatic = (AppleKnobs.from_preset(name) for name in ("soft_close", "dramatic"))
        self.assertLess(soft.block_gap_ratio, current.block_gap_ratio)
        self.assertLess(soft.blur_px, current.blur_px)
        self.assertGreater(soft.other_scale, current.other_scale)
        self.assertGreater(soft.unread_alpha, current.unread_alpha)
        self.assertGreater(dramatic.block_gap_ratio, current.block_gap_ratio)
        self.assertGreater(dramatic.blur_px, current.blur_px * 2)
        self.assertLess(dramatic.other_scale, current.other_scale)
        self.assertNotEqual(dramatic.lead_ms, current.lead_ms)
        for preset in APPLE_KNOB_PRESETS.values():
            for duration in (preset.scroll_ms, preset.lead_ms):
                self.assertTrue(250 <= duration <= 500)

    def test_customizing_preset_does_not_mutate_defaults_or_named_presets(self):
        soft = AppleKnobs.from_preset("soft_close")
        edited = replace(soft, blur_px=3.0)
        self.assertNotEqual(edited, soft)
        self.assertEqual(AppleKnobs.from_preset("soft_close").blur_px, 1.0)
        self.assertEqual(AppleKnobs().blur_px, 2.2)
        with self.assertRaises(TypeError):
            APPLE_KNOB_PRESETS["current"] = edited

    def test_preset_scrolls_remain_monotonic_without_overshoot(self):
        for knobs in APPLE_KNOB_PRESETS.values():
            frames = [displayed_focus(3.0 + step / 100, knobs.lead_seconds(), knobs.scroll_seconds())
                      for step in range(121)]
            self.assertEqual(frames, sorted(frames))
            self.assertEqual((frames[0], frames[-1]), (0.0, 1.0))


class ApplePresetWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_switching_presets_syncs_existing_sliders_and_reset_keeps_today(self):
        from _demos.apple_lyrics.demo import DemoWindow

        window = DemoWindow("dramatic")
        window.timer.stop()
        try:
            for name in ("dramatic", "soft_close", "current"):
                window.set_preset(name)
                expected = asdict(AppleKnobs.from_preset(name))
                self.assertEqual(asdict(window.knobs), expected)
                for slider in window._sliders:
                    self.assertAlmostEqual(slider.value() / slider.property("scale"),
                                           expected[slider.property("field")])
            window.set_preset("dramatic")
            window._reset_knobs()
            self.assertEqual(asdict(window.knobs), asdict(AppleKnobs()))
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
