"""The moving light should repaint its visible annulus and erase its old one."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from PIL import Image, ImageChops
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
from tests.benchmark_music_surface import SyntheticOwner


class MusicRippleDirtyRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = SyntheticOwner("unit-slow")
        self.owner.music_surface_reading_strategy = "gold"
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.window._fullscreen = True
        self.window._reset_control_idle(now=20.0)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()
        self.app.processEvents()

    def _activate(self, command: str, start: float, duration: float):
        self.window._transport_feedback.clear()
        self.window._transport_light_dirty_regions.clear()
        self.window._transport_light_ink_regions.clear()
        self.window._transport_light_origins.clear()
        self.window._transport_feedback[command] = (
            start, self.window._transport_light_origin(start),
            self.window._surface_scale(), duration,
        )
        rect = self.window._transport_light_dirty_rect(0.0, start)
        self.window._transport_light_dirty_regions[command] = rect
        self.window._transport_light_ink_regions[command] = (
            self.window._transport_light_ink_region(command, 0.0, start)
        )
        self.window._transport_light_origins[command] = self.window._transport_light_origin(start)

    def _light_image(self, at: float, dpr: int) -> QImage:
        image = QImage(self.window.width() * dpr, self.window.height() * dpr,
                       QImage.Format.Format_RGB32)
        image.setDevicePixelRatio(dpr)
        image.fill(QColor(40, 40, 40))
        with QPainter(image) as painter:
            self.window._paint_transport_spatial_light(painter, QRectF(self.window.rect()), at)
        return image

    def test_gold_and_plain_annuli_cover_every_changed_ripple_pixel_at_1x_and_2x(self):
        for command, duration in (("obo_reveal", 3.0), ("next", 1.72)):
            for dpr in (1, 2):
                for elapsed in (0.25, duration * 0.55, duration * 0.80):
                    with self.subTest(command=command, dpr=dpr, elapsed=elapsed):
                        start = 20.0
                        self._activate(command, start, duration)
                        old_at, new_at = start + elapsed, start + elapsed + 0.008
                        old_frame = self.window._transport_feedback_frame(elapsed, duration)
                        new_frame = self.window._transport_feedback_frame(elapsed + 0.008, duration)
                        region = self.window._transport_light_ink_region(
                            command, old_frame[1], old_at,
                        ).united(self.window._transport_light_ink_region(
                            command, new_frame[1], new_at,
                        ))
                        previous = self._light_image(old_at, dpr)
                        current = self._light_image(new_at, dpr)
                        old_pil = Image.frombytes("RGBA", (previous.width(), previous.height()), bytes(previous.bits()))
                        new_pil = Image.frombytes("RGBA", (current.width(), current.height()), bytes(current.bits()))
                        changed = ImageChops.difference(old_pil.convert("RGB"), new_pil.convert("RGB"))
                        mask = QImage(previous.size(), QImage.Format.Format_RGB32)
                        mask.setDevicePixelRatio(dpr)
                        mask.fill(Qt.GlobalColor.black)
                        with QPainter(mask) as painter:
                            painter.setClipRegion(region)
                            painter.fillRect(self.window.rect(), Qt.GlobalColor.white)
                        mask_pil = Image.frombytes("RGBA", (mask.width(), mask.height()), bytes(mask.bits()))
                        missing = ImageChops.subtract(changed, mask_pil.convert("RGB"))
                        if elapsed >= duration * 0.55:
                            self.assertIsNotNone(changed.getbbox())
                        self.assertIsNone(missing.getbbox(), f"light leaked outside dirty region: {missing.getbbox()}")

    def test_large_ring_skips_transparent_center_and_clears_at_end(self):
        self._activate("obo_reveal", 20.0, 3.0)
        old_at, new_at = 21.5, 21.508
        frame = self.window._transport_feedback_frame(old_at - 20.0, 3.0)
        ring = self.window._transport_light_ink_region("obo_reveal", frame[1], old_at)
        origin = self.window._transport_light_origin(old_at)
        radius = self.window._transport_light_max_radius(origin, old_at) * frame[1]
        self.assertFalse(ring.contains(QPoint(round(origin.x()), round(origin.y()))))
        self.assertTrue(ring.contains(QPoint(round(origin.x() + 0.79 * radius), round(origin.y()))))
        self.window._update_transport_feedback_regions(old_at)
        with patch.object(self.window, "update") as update:
            self.window._update_transport_feedback_regions(new_at)
        dirty = update.call_args_list[0].args[0]
        self.assertIsInstance(dirty, QRegion)
        self.assertFalse(dirty.contains(QPoint(round(origin.x()), round(origin.y()))))
        self.assertLess(sum(rect.width() * rect.height() for rect in dirty),
                        self.window.width() * self.window.height())
        old_region = QRegion(self.window._transport_light_ink_regions["obo_reveal"])
        with patch.object(self.window, "update") as update:
            self.window._update_transport_feedback_regions(23.01)
        self.assertNotIn("obo_reveal", self.window._transport_feedback)
        self.assertNotIn("obo_reveal", self.window._transport_light_ink_regions)
        old_pixel = next(iter(old_region)).center()
        self.assertTrue(any(
            isinstance(call.args[0], QRegion) and call.args[0].contains(old_pixel)
            for call in update.call_args_list
        ))

    def test_repeating_artwork_ring_erases_prior_outer_crest(self):
        self._activate("art_loading", 20.0, 1.72)
        self.window._art_loading_waiting = True
        self.window._track_visual = ("song",)
        self.window._art_loading_track_key = "song"
        near_edge = 21.49
        self.window._update_transport_feedback_regions(near_edge)
        old_region = QRegion(self.window._transport_light_ink_regions["art_loading"])
        old_start = self.window._transport_feedback["art_loading"][0]
        with patch.object(self.window, "update") as update:
            self.window._update_transport_feedback_regions(21.52)
        self.assertGreater(self.window._transport_feedback["art_loading"][0], old_start)
        dirty = update.call_args_list[0].args[0]
        self.assertIsInstance(dirty, QRegion)
        self.assertTrue(dirty.contains(next(iter(old_region)).center()))


if __name__ == "__main__":
    unittest.main()
