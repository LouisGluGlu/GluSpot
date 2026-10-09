"""Full-size radial light and rapid switch erase/cover cache regressions."""

import unittest
import threading
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
from local.music_surface_palette import artwork_surface_palette
from tests.benchmark_music_surface import SyntheticOwner


class MusicRippleQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = SyntheticOwner("unit-slow")
        self.owner.album_art_source = "original"
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(1920, 1080)
        self.window._fullscreen = True
        self.window._reset_control_idle(now=20.0)
        self.window._media_button_rects["next"] = QRectF(150, 900, 44, 44)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()
        self.app.processEvents()

    def _image(self, dpr):
        image = QImage(round(self.window.width() * dpr), round(self.window.height() * dpr),
                       QImage.Format.Format_RGB32)
        image.setDevicePixelRatio(dpr)
        image.fill(QColor(40, 40, 40))
        return image

    def _paint_light(self, image, now, region=None):
        with QPainter(image) as painter:
            if region is not None:
                painter.setClipRegion(region)
                painter.fillRect(self.window.rect(), QColor(40, 40, 40))
            self.window._paint_transport_spatial_light(painter, QRectF(self.window.rect()), now)

    def test_large_high_dpi_waves_never_expand_the_small_reference_bitmap(self):
        for dpr in (1.0, 1.5, 2.0):
            for command in ("next", "obo_reveal"):
                self.window._transport_feedback = {command: (20.0, QPointF(), 1.0, 1.72)}
                image = self._image(dpr)
                with patch.object(self.window, "_cached_transport_light_sprite", side_effect=AssertionError("fixed bitmap upscale")):
                    self._paint_light(image, 21.35)
                origin = self.window._transport_light_origin(21.35)
                frame = self.window._transport_feedback_frame(1.35, 1.72)
                crest = self.window._transport_light_max_radius(origin, 21.35) * frame[1] * .79
                self.assertGreater(image.pixelColor(round((origin.x() + crest) * dpr),
                                                    round(origin.y() * dpr)).red(), 60)

    def test_rapid_wave_restart_and_track_change_clear_all_previous_ink_at_fractional_dpi(self):
        for dpr in (1.0, 1.5, 2.0):
            self.window._reset_control_idle(now=20.0)
            self.window._start_transport_feedback("next", now=20.0)
            self.window._update_transport_feedback_regions(20.85)
            partial = self._image(dpr)
            self._paint_light(partial, 20.85)
            with patch.object(self.window, "update") as update:
                self.window._start_transport_feedback("next", now=20.858)
                self.window._update_transport_feedback_regions(20.866)
            dirty = QRegion()
            for call in update.call_args_list:
                dirty = dirty.united(QRegion(call.args[0]))
            self._paint_light(partial, 20.866, dirty)
            full = self._image(dpr)
            self._paint_light(full, 20.866)
            self.assertEqual(bytes(partial.constBits()), bytes(full.constBits()))

    def test_current_previous_and_revisited_cover_share_full_quality_scaling(self):
        covers = []
        for hue in (0, 120, 240):
            source = QImage(1000, 1000, QImage.Format.Format_RGB32)
            source.fill(QColor.fromHsv(hue, 160, 220))
            covers.append(source)
        first = self.window._scaled_artwork_image(covers[0], 900, 1.5)
        for source in covers[1:]:
            self.window._scaled_artwork_image(source, 900, 1.5)
        repeated = self.window._scaled_artwork_image(covers[0], 900, 1.5)
        self.assertEqual(repeated.cacheKey(), first.cacheKey())
        self.assertEqual(repeated.width(), 900)
        self.assertEqual(repeated.devicePixelRatio(), 1.5)
        repeated.fill(QColor("white"))
        self.assertEqual(self.window._scaled_artwork_image(covers[0], 900, 1.5).cacheKey(), first.cacheKey())

    def test_cover_cache_memory_budget_and_explicit_cleanup(self):
        self.window.ARTWORK_SCALE_CACHE_BYTES = 512 * 512 * 4 * 2
        for hue in range(0, 360, 30):
            source = QImage(1000, 1000, QImage.Format.Format_RGB32)
            source.fill(QColor.fromHsv(hue, 160, 220))
            self.window._scaled_artwork_image(source, 512, 2.0)
        self.assertLessEqual(self.window._artwork_scale_cache_bytes, self.window.ARTWORK_SCALE_CACHE_BYTES)
        self.assertLessEqual(len(self.window._artwork_scale_cache), 2)
        self.window.invalidate_artwork_cache()
        self.assertEqual(self.window._artwork_scale_cache_bytes, 0)
        self.assertFalse(self.window._artwork_scale_cache)

    def _queued_palette_owner(self):
        self.jobs = []
        self.owner._run_background = lambda _name, callback: self.jobs.append(callback)

    def _change_cover(self, hue):
        image = QImage(72, 72, QImage.Format.Format_RGB32)
        image.fill(QColor.fromHsv(hue, 160, 220))
        self.owner.album_art = image
        self.owner.album_color = QColor.fromHsv(hue, 160, 220)
        return self.window._art_palette()

    def test_palette_worker_keeps_one_job_and_latest_pending_cover(self):
        self._queued_palette_owner()
        first = self._change_cover(0)
        self._change_cover(90)
        self._change_cover(180)
        self.assertEqual(len(self.jobs), 1)
        self.jobs[0]()
        self.assertEqual(len(self.jobs), 2)
        # The old job may populate the shared value cache but cannot replace
        # the displayed palette for a different current cover.
        self.assertEqual(self.window._art_palette_cache, first)
        self.jobs[1]()
        expected = artwork_surface_palette(self.owner.album_art, self.owner.album_color, self.owner.secondary_color)
        self.assertEqual(self.window._art_palette_cache, expected)
        self.assertIsNone(self.window._art_palette_pending)
        self.assertIsNone(self.window._art_palette_worker_key)

    def test_same_cover_invalidation_reuses_completed_cache_without_second_job(self):
        self._queued_palette_owner()
        self._change_cover(10)
        old_token = self.window._art_palette_worker_token
        self.window.invalidate_artwork_cache()
        self.window._art_palette()
        self.assertEqual(len(self.jobs), 1)
        self.jobs[0]()
        self.assertEqual(len(self.jobs), 1)
        self.assertGreater(self.window._art_palette_worker_token, old_token)
        self.assertIsNone(self.window._art_palette_worker_key)
        self.assertEqual(self.window._art_palette_cache_key[0], self.owner.album_art.cacheKey())

    def test_shutdown_then_reopen_waits_for_old_job_and_rejects_old_result(self):
        self._queued_palette_owner()
        first = self._change_cover(20)
        self.window.shutdown()
        # Exercise the actual reopen lifecycle without exposing a test window
        # on the desktop or involving the user's player/audio devices.
        with (patch.object(self.window, "showFullScreen"), patch.object(self.window, "raise_"),
              patch.object(self.window, "activateWindow")):
            self.window.show_from_island(fullscreen=True)
        self._change_cover(200)
        self.assertEqual(len(self.jobs), 1)
        self.jobs[0]()
        self.assertEqual(self.window._art_palette_cache, first)
        self.assertEqual(len(self.jobs), 2)
        self.jobs[1]()
        expected = artwork_surface_palette(self.owner.album_art, self.owner.album_color, self.owner.secondary_color)
        self.assertEqual(self.window._art_palette_cache, expected)

    def test_shutdown_worker_completion_cannot_change_displayed_palette(self):
        self._queued_palette_owner()
        first = self._change_cover(40)
        self.window.shutdown()
        self.jobs[0]()
        self.assertEqual(self.window._art_palette_cache, first)
        self.assertIsNone(self.window._art_palette_worker_key)

    def test_real_thread_result_is_queued_until_gui_events_are_delivered(self):
        completed = threading.Event()
        workers = []

        def start(_name, callback):
            def prepare():
                try:
                    callback()
                finally:
                    completed.set()
            worker = threading.Thread(target=prepare)
            workers.append(worker)
            worker.start()

        self.owner._run_background = start
        self._change_cover(55)
        self.assertTrue(completed.wait(2.0))
        self.assertNotEqual(self.window._art_palette_cache_key,
                            (self.owner.album_art.cacheKey(), self.owner.album_color.rgba(),
                             self.owner.secondary_color.rgba()))
        self.app.processEvents()
        self.assertEqual(self.window._art_palette_cache_key[0], self.owner.album_art.cacheKey())
        self.assertIsNone(self.window._art_palette_worker_key)
        for worker in workers:
            worker.join(timeout=2.0)
            self.assertFalse(worker.is_alive())

    def test_failed_palette_job_holds_display_and_does_not_retry_every_paint(self):
        first = self._change_cover(65)
        self._queued_palette_owner()
        with patch("local.music_surface_window.artwork_surface_palette", side_effect=RuntimeError("fixture extraction failure")):
            self._change_cover(245)
            self.jobs[0]()
            for _ in range(5):
                self.assertEqual(self.window._art_palette(), first)
        self.assertEqual(len(self.jobs), 1)
        self.assertIsNone(self.window._art_palette_worker_key)
        self.assertEqual(self.window._art_palette_failed_key[0], self.owner.album_art.cacheKey())


if __name__ == "__main__":
    unittest.main()
