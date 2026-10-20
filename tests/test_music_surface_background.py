from __future__ import annotations

import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageStat
import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage

from local.music_surface_background import AlbumFlowBackgroundRenderer
from local.music_surface_palette import future_lyric_color, relative_luminance, single_hue_background_colors


def _pixels(image: QImage) -> Image.Image:
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombytes("RGBA", (rgba.width(), rgba.height()), rgba.constBits().tobytes(),
                           "raw", "RGBA", rgba.bytesPerLine()).convert("RGB")


def _difference(first: QImage, second: QImage) -> tuple[float, int]:
    difference = ImageChops.difference(_pixels(first), _pixels(second))
    return sum(ImageStat.Stat(difference).mean) / 3.0, max(high for _low, high in difference.getextrema())


def _advance(renderer: AlbumFlowBackgroundRenderer, colors: tuple[QColor, ...], until: float,
             start: float = 0.0, intensity: float = 0.74) -> QImage:
    frame = QImage()
    frames = round((until - start) / renderer.FRAME_SECONDS)
    for index in range(1, frames + 1):
        frame = renderer.render(QSize(1920, 1080), colors, start + index * renderer.FRAME_SECONDS, intensity)
    return frame


class AlbumFlowBackgroundTests(unittest.TestCase):
    COLORS = (QColor(222, 53, 44), QColor(35, 120, 216), QColor(199, 170, 52))

    @staticmethod
    def _previous_ambient_color(source):
        hue, saturation, value, _alpha = source.getHsv()
        return AlbumFlowBackgroundRenderer._bounded_luminance(
            QColor.fromHsv(hue, saturation, min(220, round(80 + value * .55))))

    def test_pure_pigments_soften_and_darken_without_rotating_the_album_hue(self):
        for source in (QColor(34, 34, 254), *(QColor.fromHsv(hue, 250, 245) for hue in range(0, 360, 30))):
            with self.subTest(source=source.name()):
                previous = self._previous_ambient_color(source)
                actual = AlbumFlowBackgroundRenderer._ambient_color(source)
                hue_delta = abs(source.hsvHue() - actual.hsvHue()) % 360
                self.assertLessEqual(min(hue_delta, 360 - hue_delta), 1)
                self.assertLess(actual.hsvSaturationF(), previous.hsvSaturationF() * .80)
                self.assertLess(actual.valueF(), previous.valueF())
                self.assertLess(relative_luminance(actual), relative_luminance(previous) * .90)

    def test_moderate_pigments_dark_shadows_and_single_hue_layers_keep_established_mapping(self):
        for source in (QColor.fromHsv(240, 175, 254), QColor.fromHsv(0, 180, 210),
                       QColor.fromHsv(60, 255, 45), QColor(7, 22, 38), QColor(30, 3, 8)):
            with self.subTest(source=source.name()):
                self.assertEqual(AlbumFlowBackgroundRenderer._ambient_color(source), self._previous_ambient_color(source))
        colors = (QColor(34, 34, 254),)
        roles = single_hue_background_colors(colors, weights=(1.0,))
        slots = AlbumFlowBackgroundRenderer._palette_slots(colors, (1.0,))
        self.assertEqual(slots[:4], tuple(AlbumFlowBackgroundRenderer._bounded_luminance(color) for color in roles))

    def test_purity_compression_enters_continuously_and_retains_source_color_objects(self):
        sources = tuple(QColor.fromHsv(240, saturation, 254) for saturation in range(170, 256))
        originals = tuple(source.rgba() for source in sources)
        displayed = tuple(AlbumFlowBackgroundRenderer._ambient_color(source) for source in sources)
        self.assertEqual(tuple(source.rgba() for source in sources), originals)
        for first, second in zip(displayed, displayed[1:]):
            # Continuous compression plus RGB8 luminance rounding may move
            # four channel steps at its steepest point; there is no branch
            # jump when a source first enters the high-purity interval.
            self.assertLessEqual(max(abs(a-b) for a, b in zip(first.getRgb()[:3], second.getRgb()[:3])), 4)
        self.assertLessEqual(max(abs(a-b) for a, b in zip(displayed[10].getRgb()[:3], displayed[11].getRgb()[:3])), 1)

    def test_blue_dominant_multicolor_fields_reduce_saturation_and_luminance_through_motion(self):
        # The supplied screenshot has this supported blue plus purple, warm
        # and neutral regions, so the existing single-hue route cannot apply.
        colors = (QColor(34, 34, 254), QColor(88, 10, 115), QColor(188, 145, 102), QColor(66, 66, 66))
        self.assertFalse(single_hue_background_colors(colors, weights=(.62, .18, .12, .08)))
        actual_slots = AlbumFlowBackgroundRenderer._palette_slots(colors)
        with patch.object(AlbumFlowBackgroundRenderer, "_ambient_color", side_effect=self._previous_ambient_color):
            previous_slots = AlbumFlowBackgroundRenderer._palette_slots(colors)
        renderer = AlbumFlowBackgroundRenderer()
        actual_chroma, previous_chroma, actual_light, previous_light = [], [], [], []
        for second in range(0, 61, 5):
            renderer._geometry_seconds = second * .74
            actual = np.asarray(_pixels(renderer._paint_field(QSize(256, 144), actual_slots))).astype(float)
            previous = np.asarray(_pixels(renderer._paint_field(QSize(256, 144), previous_slots))).astype(float)
            for pixels, chromas, lights in ((actual, actual_chroma, actual_light), (previous, previous_chroma, previous_light)):
                high, low = pixels.max(2), pixels.min(2)
                chromas.append(float(np.mean((high-low) / np.maximum(high, 1))))
                linear = np.where(pixels / 255 <= .04045, pixels / 255 / 12.92, ((pixels / 255 + .055) / 1.055) ** 2.4)
                lights.append(float(np.mean(linear @ np.array((.2126, .7152, .0722)))))
        self.assertLess(max(actual_chroma), max(previous_chroma) * .82)
        self.assertLess(np.mean(actual_light), np.mean(previous_light) * .95)
        self.assertGreater(max(actual_light) - min(actual_light), .004)

    def test_bright_single_hue_has_soft_tonal_depth_instead_of_a_saturated_wash(self):
        for source in (QColor(233, 31, 20), QColor(16, 135, 164),
                       QColor(242, 204, 12), QColor(181, 35, 230)):
            with self.subTest(source=source.name()):
                renderer = AlbumFlowBackgroundRenderer()
                roles = renderer._palette_slots((source,), (1.0,))
                strengths = renderer._domain_strengths((source,), (1.0,))
                observed_ranges, mean_saturation = [], []
                for second in range(0, 61, 3):
                    renderer._geometry_seconds = second * .74
                    frame = renderer._paint_field(QSize(256, 144), roles, strengths)
                    rgb = np.asarray(_pixels(frame)).astype(float)
                    high, low = rgb.max(2), rgb.min(2)
                    mean_saturation.append(np.mean((high - low) / np.maximum(high, 1)))
                    observed_ranges.append(float(np.percentile(high, 90) - np.percentile(high, 10)))
                    for role in roles:
                        self.assertLessEqual(relative_luminance(role), renderer.MAX_LUMINANCE)
                self.assertLess(max(mean_saturation), source.hsvSaturationF() * .65)
                self.assertGreater(max(observed_ranges), 35)
                self.assertGreater(max(observed_ranges) - min(observed_ranges), 15)
                self.assertLess(strengths[2], strengths[0])

    def test_other_cover_routes_keep_exact_native_pixels(self):
        controls = (self.COLORS, (QColor(112, 112, 112),),
                    (QColor(8, 25, 38), QColor(20, 130, 240)),
                    (QColor(195, 30, 40), QColor(240, 240, 240)))
        for colors in controls:
            with self.subTest(colors=colors):
                self.assertFalse(single_hue_background_colors(colors))
                actual = AlbumFlowBackgroundRenderer().render(QSize(256, 144), colors, 0.0)
                with patch("local.music_surface_background.single_hue_background_colors", return_value=()):
                    previous = AlbumFlowBackgroundRenderer().render(QSize(256, 144), colors, 0.0)
                self.assertEqual(_difference(actual, previous), (0.0, 0))

    def test_single_hue_display_layers_keep_live_lyric_contrast_across_hues(self):
        for hue in range(0, 360, 15):
            source = QColor.fromHsv(hue, 235, 225)
            renderer = AlbumFlowBackgroundRenderer()
            frame = renderer.render(QSize(256, 144), (source,), 0.0, weights=(1.0,))
            backdrop = renderer.contrast_backdrop(frame)
            self.assertIsNotNone(backdrop)
            self.assertLessEqual(relative_luminance(backdrop), renderer.MAX_LUMINANCE)
            self.assertGreaterEqual(1.05 / (relative_luminance(backdrop) + .05), 3.3)
            ink = future_lyric_color(source, backdrop, primary=source, use_light=True, require_light=True)
            composed = QColor(*(round(front * ink.alphaF() + back * (1 - ink.alphaF()))
                                for front, back in zip(ink.getRgb()[:3], backdrop.getRgb()[:3])))
            ratio = (relative_luminance(composed) + .05) / (relative_luminance(backdrop) + .05)
            self.assertGreaterEqual(ratio, 2.30)

    def test_4k_portrait_and_small_outputs_are_opaque_and_bounded(self):
        renderer = AlbumFlowBackgroundRenderer()
        for size in (QSize(3840, 2160), QSize(1080, 7680), QSize(192, 108), QSize(0, 0)):
            with self.subTest(size=size):
                image = renderer.render(size, self.COLORS, 1.0)
                self.assertFalse(image.isNull())
                self.assertLessEqual(image.width(), renderer.MAX_WIDTH)
                self.assertLessEqual(image.height(), renderer.MAX_HEIGHT)
                self.assertEqual(Image.frombytes("RGBA", (image.width(), image.height()),
                                                image.constBits().tobytes()).getchannel("A").getextrema(), (255, 255))

    def test_frame_cache_throttles_motion_and_returned_image_is_safe_to_retain(self):
        renderer = AlbumFlowBackgroundRenderer()
        first = renderer.render(QSize(3840, 2160), self.COLORS, 0.0)
        cached = renderer.render(QSize(3840, 2160), self.COLORS, 0.016)
        self.assertEqual(first.cacheKey(), cached.cacheKey())
        next_frame = renderer.render(QSize(3840, 2160), self.COLORS, 0.034)
        self.assertNotEqual(first.cacheKey(), next_frame.cacheKey())
        cached.fill(QColor(255, 255, 255))
        retained = renderer.render(QSize(3840, 2160), self.COLORS, 0.035)
        self.assertEqual(next_frame.cacheKey(), retained.cacheKey())
        self.assertNotEqual(first.pixelColor(0, 0), QColor(255, 255, 255))

    def test_unchanged_palette_skips_color_analysis_even_across_new_frames(self):
        renderer = AlbumFlowBackgroundRenderer()
        colors = tuple(QColor(color) for color in self.COLORS)
        with patch.object(renderer, '_palette_slots', wraps=renderer._palette_slots) as slots, \
                patch.object(renderer, '_domain_strengths', wraps=renderer._domain_strengths) as strengths:
            first = renderer.render(QSize(960, 540), colors, 0., weights=(.6, .3, .1))
            cached = renderer.render(QSize(960, 540), tuple(QColor(color) for color in colors),
                                     .016, weights=(.6, .3, .1))
            moving = renderer.render(QSize(960, 540), colors, .034, weights=(.6, .3, .1))
            renderer.render(QSize(500, 700), colors, .05, .3, weights=(.6, .3, .1))
        self.assertEqual(first.cacheKey(), cached.cacheKey())
        self.assertNotEqual(first.cacheKey(), moving.cacheKey())
        self.assertEqual(slots.call_count, 1)
        self.assertEqual(strengths.call_count, 1)

    def test_palette_input_snapshot_observes_mutated_colors_and_support(self):
        renderer = AlbumFlowBackgroundRenderer()
        colors = (QColor('#c73542'), QColor('#2476c1'))
        with patch.object(renderer, '_palette_slots', wraps=renderer._palette_slots) as slots:
            renderer.render(QSize(960, 540), colors, 0., weights=(.6, .4))
            old_target = renderer._target_colors
            colors[0].setRgb(33, 198, 71)
            renderer.render(QSize(960, 540), colors, 0., weights=(.6, .4))
            self.assertNotEqual(renderer._target_colors, old_target)
            renderer.render(QSize(960, 540), colors, 0., weights=(.99, .01))
            self.assertEqual(slots.call_count, 3)
        # Only the most recent <=6 colors/support values are retained.
        renderer.render(QSize(960, 540), self.COLORS * 20, .1,
                        weights=(1. / 60,) * 60)
        self.assertEqual(len(renderer._palette_input_key[0]), renderer.MAX_COLORS)
        self.assertEqual(len(renderer._palette_input_key[1]), renderer.MAX_COLORS)

    def test_heavy_softening_has_no_sharp_color_boundary(self):
        frame = AlbumFlowBackgroundRenderer().render(QSize(1920, 1080), self.COLORS, 0.0)
        pixels = _pixels(frame)
        horizontal = ImageChops.difference(pixels.crop((0, 0, pixels.width - 1, pixels.height)),
                                          pixels.crop((1, 0, pixels.width, pixels.height)))
        vertical = ImageChops.difference(pixels.crop((0, 0, pixels.width, pixels.height - 1)),
                                        pixels.crop((0, 1, pixels.width, pixels.height)))
        # Bounded raster pixels cover 7.5 screen pixels at 1080p. The larger
        # pigment range must keep a soft edge without erasing its core.
        self.assertLessEqual(max(high for _low, high in horizontal.getextrema() + vertical.getextrema()), 8)
        self.assertGreater(max(high - low for low, high in pixels.getextrema()), 15)

    def test_field_drifts_and_morphs_continuously(self):
        renderer = AlbumFlowBackgroundRenderer()
        first = renderer.render(QSize(1920, 1080), self.COLORS, 0.0)
        next_frame = _advance(renderer, self.COLORS, 1.0 / 30.0)
        self.assertLessEqual(_difference(first, next_frame)[1], 8)
        self.assertLess(_difference(first, next_frame)[0], 1.0)
        later = _advance(renderer, self.COLORS, 12.0, start=1.0 / 30.0)
        self.assertGreater(_difference(first, later)[0], 2.0)
        # Shape and size change independently of position, rather than a
        # static field merely translating across the surface.
        early_bounds = renderer._blob_path(QSize(256, 144), 0, 0.0).boundingRect()
        later_bounds = renderer._blob_path(QSize(256, 144), 0, 12.0 * 0.74).boundingRect()
        self.assertGreater(abs(early_bounds.width() - later_bounds.width())
                           + abs(early_bounds.height() - later_bounds.height()), 10.0)

    def test_long_stall_and_backwards_wall_clock_do_not_jump_motion(self):
        renderer = AlbumFlowBackgroundRenderer()
        renderer.render(QSize(1920, 1080), self.COLORS, 0.0)
        before = _advance(renderer, self.COLORS, 1.0)
        resumed = renderer.render(QSize(1920, 1080), self.COLORS, 120.0)
        healthy_renderer = AlbumFlowBackgroundRenderer()
        healthy_renderer.render(QSize(1920, 1080), self.COLORS, 0.0)
        _advance(healthy_renderer, self.COLORS, 1.0)
        healthy = healthy_renderer.render(QSize(1920, 1080), self.COLORS, 1.0 + renderer.FRAME_SECONDS)
        # Compare with the actual next healthy pose: richer crossing domains
        # can change one channel more without advancing extra motion on resume.
        self.assertEqual(_difference(resumed, healthy), (0.0, 0))
        self.assertLess(_difference(before, resumed)[0], 1.0)
        backwards = renderer.render(QSize(1920, 1080), self.COLORS, 2.0)
        self.assertEqual(_difference(resumed, backwards), (0.0, 0))

    def test_palette_retarget_and_interruption_start_at_displayed_colors(self):
        renderer = AlbumFlowBackgroundRenderer()
        red, blue, green = (QColor(226, 34, 29),), (QColor(25, 50, 230),), (QColor(26, 209, 49),)
        first = renderer.render(QSize(1920, 1080), red, 0.0)
        started = renderer.render(QSize(1920, 1080), blue, 0.0)
        self.assertEqual(_difference(first, started), (0.0, 0))
        halfway = _advance(renderer, blue, 0.6)
        red_mean, _green_mean, blue_mean = ImageStat.Stat(_pixels(halfway)).mean
        self.assertLess(abs(red_mean - blue_mean), 12)
        interrupted = renderer.render(QSize(1920, 1080), green, 0.6)
        self.assertEqual(_difference(halfway, interrupted), (0.0, 0))
        completed = _advance(renderer, green, 1.9, start=0.6)
        red_mean, green_mean, blue_mean = ImageStat.Stat(_pixels(completed)).mean
        # The finished single-hue page retains green dominance after its
        # new display layers soften saturation; raw source green is intact.
        self.assertGreater(green_mean, red_mean + 15)
        self.assertGreater(green_mean, blue_mean + 15)

    def test_monochrome_and_missing_art_remain_neutral_with_tonal_motion(self):
        for colors in ((), (QColor(255, 255, 255),), (QColor(0, 0, 0),),
                       (QColor(59, 59, 59), QColor(120, 120, 120))):
            with self.subTest(colors=colors):
                renderer = AlbumFlowBackgroundRenderer()
                first = renderer.render(QSize(1920, 1080), colors, 0.0)
                later = _advance(renderer, colors, 6.0)
                channels = _pixels(later).split()
                self.assertEqual(ImageChops.difference(channels[0], channels[1]).getbbox(), None)
                self.assertEqual(ImageChops.difference(channels[1], channels[2]).getbbox(), None)
                self.assertGreater(_difference(first, later)[0], 0.2)

    def test_zero_intensity_freezes_geometry_and_ignores_excess_colors(self):
        renderer = AlbumFlowBackgroundRenderer()
        colors = self.COLORS + self.COLORS
        first = renderer.render(QSize(1920, 1080), colors, 0.0, 0.0)
        later = _advance(renderer, colors, 3.0, intensity=0.0)
        self.assertEqual(_difference(first, later), (0.0, 0))
        ignored = renderer.render(QSize(1920, 1080), colors + (QColor(255, 255, 255),), 3.0, 0.0)
        self.assertEqual(later.cacheKey(), ignored.cacheKey())

    def test_strength_change_preserves_elapsed_pose_and_zero_freezes_then_resumes(self):
        renderer = AlbumFlowBackgroundRenderer()
        size = QSize(128, 72)
        renderer.render(size, self.COLORS, 0.0)
        # Establish a long-running phase: recomputing elapsed*new_strength
        # here used to teleport both the geometry and its tonal wave.
        for index in range(1, 1801):
            moving = renderer.render(size, self.COLORS, index / 30.0)
        slowed = renderer.render(size, self.COLORS, 60.0, 0.10)
        self.assertEqual(_difference(moving, slowed), (0.0, 0))
        stopped = renderer.render(size, self.COLORS, 60.0, 0.0)
        self.assertEqual(_difference(slowed, stopped), (0.0, 0))
        for index in range(1, 31):
            frozen = renderer.render(size, self.COLORS, 60.0 + index / 30.0, 0.0)
        self.assertEqual(_difference(stopped, frozen), (0.0, 0))
        restored = renderer.render(size, self.COLORS, 61.0, 1.0)
        self.assertEqual(_difference(frozen, restored), (0.0, 0))
        for index in range(1, 151):
            resumed = renderer.render(size, self.COLORS, 61.0 + index / 30.0, 1.0)
        self.assertGreater(_difference(restored, resumed)[0], 0.5)

    def test_neutral_mapping_preserves_gray_order_and_visible_light_dark_range(self):
        shades = [AlbumFlowBackgroundRenderer._ambient_color(QColor(level, level, level)).value()
                  for level in (0, 31, 48, 76, 153, 235, 255)]
        self.assertEqual(shades, sorted(shades))
        self.assertLessEqual(shades[0], 32)
        self.assertGreaterEqual(shades[-1] - shades[0], 100)
        self.assertGreaterEqual(shades[-2] - shades[-3], 25)
        colors = (QColor(235, 235, 235), QColor(65, 65, 65))
        frame = AlbumFlowBackgroundRenderer().render(QSize(1920, 1080), colors, 0.0)
        levels = np.asarray(_pixels(frame))[:, :, 0]
        self.assertGreater(np.percentile(levels, 90), 125)
        self.assertGreater(np.percentile(levels, 90) - np.percentile(levels, 10), 25)

    def test_real_pigments_keep_distinct_colored_cores_and_large_quiet_dark_regions(self):
        colors = (QColor(0, 0, 0), QColor(222, 25, 45), QColor(30, 80, 220), QColor(220, 220, 220))
        renderer = AlbumFlowBackgroundRenderer()
        slots = renderer._palette_slots(colors)
        reds, blues, darks = [], [], []
        for second in range(0, 61, 3):
            renderer._geometry_seconds = second * .74
            rgb = np.asarray(_pixels(renderer._paint_field(QSize(256, 144), slots))).astype(float)
            high, low = rgb.max(2), rgb.min(2)
            saturation = np.divide(high-low, high, out=np.zeros_like(high), where=high>0)
            # Highly pure cores now soften under the requested presentation
            # policy while remaining clearly colored over the quiet base.
            reds.append(np.mean((rgb[:,:,0] > rgb[:,:,2]*1.8) & (saturation>.52) & (high>100)))
            blues.append(np.mean((rgb[:,:,2] > rgb[:,:,0]*1.8) & (saturation>.52) & (high>100)))
            darks.append(np.mean(high<40))
        self.assertGreater(max(reds), .03)
        self.assertGreater(max(blues), .03)
        self.assertGreater(np.median(darks), .35)
        self.assertGreater(max(darks)-min(darks), .15)

    def test_sparse_detail_support_controls_area_without_gray_washing_core(self):
        colors = (QColor(0,0,0), QColor(220,30,50))
        slots = AlbumFlowBackgroundRenderer._palette_slots(colors)
        renderer = AlbumFlowBackgroundRenderer()
        # Choose a phase with the red domain well inside the screen.
        renderer._geometry_seconds = 50.0
        normal = np.asarray(_pixels(renderer._paint_field(QSize(256,144), slots)))
        small = np.asarray(_pixels(renderer._paint_field(QSize(256,144), slots,
                           renderer._domain_strengths(colors, (.99,.01)))))
        normal_area = np.mean(normal[:,:,0]>70)
        small_area = np.mean(small[:,:,0]>70)
        self.assertLess(small_area, normal_area*.4)
        self.assertGreater(small[:,:,0].max(), 100)

    def test_color_and_support_changes_start_from_last_complete_displayed_field(self):
        renderer=AlbumFlowBackgroundRenderer()
        colors=(QColor(2,2,2), QColor(30,120,220))
        before=renderer.render(QSize(1920,1080),colors,0,weights=(.65,.35))
        changed=renderer.render(QSize(1920,1080),colors,0,weights=(.99,.01))
        self.assertEqual(_difference(before,changed),(0.0,0))
        for index in range(1,19):
            intermediate=renderer.render(QSize(1920,1080),colors,index/30,weights=(.99,.01))
        interrupted=renderer.render(QSize(1920,1080),colors,.6,weights=(.5,.5))
        self.assertEqual(_difference(intermediate,interrupted),(0.0,0))

    def test_invalid_color_does_not_displace_primary_or_its_cover_support(self):
        colors=(QColor(),QColor(222,25,40),QColor(30,80,220))
        actual=AlbumFlowBackgroundRenderer().render(QSize(1920,1080),colors,0,weights=(.1,.6,.3))
        expected=AlbumFlowBackgroundRenderer().render(QSize(1920,1080),colors[1:],0,weights=(.6,.3))
        self.assertEqual(_difference(actual,expected),(0.0,0))

    def test_single_dark_chromatic_cover_keeps_its_real_hue(self):
        for source in (QColor(30,3,8),QColor(4,7,40)):
            renderer=AlbumFlowBackgroundRenderer()
            slots=renderer._palette_slots((source,))
            saturation=[]
            for second in range(0,61,3):
                renderer._geometry_seconds=second*.74
                rgb=np.asarray(_pixels(renderer._paint_field(QSize(256,144),slots))).astype(float)
                high,low=rgb.max(2),rgb.min(2)
                saturation.append(np.mean((high-low)/np.maximum(high,1)>.6))
            self.assertGreater(max(saturation),.02)

    def test_all_pigments_and_interpolated_fields_respect_white_ink_envelope(self):
        colors=tuple(QColor.fromHsv(hue,saturation,255) for hue in range(0,360,45)
                     for saturation in (0,100,255))
        for color in colors:
            self.assertLessEqual(relative_luminance(AlbumFlowBackgroundRenderer._ambient_color(color)),
                                 AlbumFlowBackgroundRenderer.MAX_LUMINANCE)
        renderer=AlbumFlowBackgroundRenderer()
        for second, pigments in [(0,colors[:6]),(0,colors[-6:]),(.4,colors[-6:]),(.4,colors[6:12])]:
            frame=renderer.render(QSize(256,144),pigments,second)
            for x in range(0,frame.width(),5):
                for y in range(0,frame.height(),5):
                    self.assertLessEqual(relative_luminance(frame.pixelColor(x,y)),
                                         renderer.MAX_LUMINANCE+.003)


if __name__ == "__main__":
    unittest.main()
