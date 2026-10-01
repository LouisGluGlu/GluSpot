from __future__ import annotations

import unittest

from PIL import Image, ImageChops, ImageStat
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage

from local.music_surface_background import AlbumFlowBackgroundRenderer


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

    def test_heavy_softening_has_no_sharp_color_boundary(self):
        frame = AlbumFlowBackgroundRenderer().render(QSize(1920, 1080), self.COLORS, 0.0)
        pixels = _pixels(frame)
        horizontal = ImageChops.difference(pixels.crop((0, 0, pixels.width - 1, pixels.height)),
                                          pixels.crop((1, 0, pixels.width, pixels.height)))
        vertical = ImageChops.difference(pixels.crop((0, 0, pixels.width, pixels.height - 1)),
                                        pixels.crop((0, 1, pixels.width, pixels.height)))
        self.assertLessEqual(max(high for _low, high in horizontal.getextrema() + vertical.getextrema()), 3)
        self.assertGreater(max(high - low for low, high in pixels.getextrema()), 15)

    def test_field_drifts_and_morphs_continuously(self):
        renderer = AlbumFlowBackgroundRenderer()
        first = renderer.render(QSize(1920, 1080), self.COLORS, 0.0)
        next_frame = _advance(renderer, self.COLORS, 1.0 / 30.0)
        self.assertLessEqual(_difference(first, next_frame)[1], 2)
        later = _advance(renderer, self.COLORS, 12.0, start=1.0 / 30.0)
        self.assertGreater(_difference(first, later)[0], 2.0)
        # Shape and size change independently of position, rather than a
        # static field merely translating across the surface.
        early_bounds = renderer._blob_path(QSize(256, 144), 0, 0.0).boundingRect()
        later_bounds = renderer._blob_path(QSize(256, 144), 0, 12.0 * 0.74).boundingRect()
        self.assertGreater(abs(early_bounds.width() - later_bounds.width()), 5.0)
        self.assertGreater(abs(early_bounds.height() - later_bounds.height()), 5.0)

    def test_long_stall_and_backwards_wall_clock_do_not_jump_motion(self):
        renderer = AlbumFlowBackgroundRenderer()
        renderer.render(QSize(1920, 1080), self.COLORS, 0.0)
        before = _advance(renderer, self.COLORS, 1.0)
        resumed = renderer.render(QSize(1920, 1080), self.COLORS, 120.0)
        self.assertLessEqual(_difference(before, resumed)[1], 2)
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
        self.assertGreater(green_mean, red_mean + 40)
        self.assertGreater(green_mean, blue_mean + 40)

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


if __name__ == "__main__":
    unittest.main()
