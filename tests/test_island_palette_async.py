from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QColor, QImage, QShowEvent
from PySide6.QtWidgets import QApplication

from api.providers import EmptyIslandDataProvider, empty_visual_data
from local.island_stack_visual import IslandStackVisual
from local.music_surface_palette import artwork_surface_palette, solid_album_background_color


class IslandPaletteAsyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.visual = IslandStackVisual(EmptyIslandDataProvider(empty_visual_data()))
        self.visual.timer.stop()
        self.jobs = []
        self.visual._run_background = lambda name, callback: self.jobs.append((name, callback))
        self.visual.music_ambience_style = "solid_album"
        self.visual.album_color = QColor("#345678")
        self.visual.secondary_color = QColor("#56789A")

    def tearDown(self):
        self.visual.close()
        self.visual.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def _cover(self, tone):
        image = QImage(72, 72, QImage.Format.Format_RGB32)
        image.fill(QColor(tone))
        self.visual.album_art = image
        return image

    def _complete(self):
        _name, callback = self.jobs.pop(0)
        thread = threading.Thread(target=callback)
        thread.start()
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.app.processEvents()

    def _settle(self):
        with patch("local.island_stack_visual.time.monotonic", return_value=time.monotonic() + 1.0):
            return self.visual._solid_album_surface_palette()

    def test_cold_request_is_nonblocking_and_ready_only_writes_from_gui(self):
        image = self._cover("#C02849")
        with patch("local.island_stack_visual.artwork_surface_palette", side_effect=AssertionError("GUI extraction")):
            first = self.visual._solid_album_surface_palette()
        self.assertEqual(solid_album_background_color(first).name(), "#345678")
        self.assertEqual(len(self.jobs), 1)
        self.assertIsNotNone(self.visual._solid_album_palette_active_token)
        callback = self.jobs.pop()[1]
        thread = threading.Thread(target=callback)
        thread.start()
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        # Queued worker result cannot mutate QWidget state before GUI delivery.
        self.assertIsNone(self.visual._solid_album_palette_cache_key)
        self.app.processEvents()
        self.assertEqual(self.visual._solid_album_palette_cache_key[0], image.cacheKey())
        self.assertEqual(solid_album_background_color(self._settle()).name(), "#c02849")

    def test_fast_arrivals_keep_one_active_job_and_only_latest_successor(self):
        first = self._cover("#C02849")
        self.visual._solid_album_surface_palette()
        self._cover("#38B75E")
        self.visual._solid_album_surface_palette()
        final = self._cover("#F1C34A")
        self.visual._solid_album_surface_palette()
        for _ in range(30):
            self.visual._solid_album_surface_palette()
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual(self.visual._solid_album_palette_worker_key[0], first.cacheKey())
        self.assertEqual(self.visual._solid_album_palette_pending[0][0], final.cacheKey())
        self._complete()
        self.assertEqual(len(self.jobs), 1)
        self.assertNotEqual(self.visual._solid_album_palette_cache_key, self.visual._solid_album_palette_worker_key)
        self.assertEqual(self.visual._solid_album_palette_worker_key[0], final.cacheKey())
        self._complete()
        self.assertFalse(self.jobs)
        self.assertEqual(solid_album_background_color(self._settle()).name(), "#f1c34a")

    def test_old_provider_colors_are_rejected_when_changed_before_queued_delivery(self):
        self._cover("#C02849")
        self.visual._solid_album_surface_palette()
        old_key = self.visual._solid_album_palette_worker_key
        self.visual.album_color = QColor("#192A3B")
        self.visual.secondary_color = QColor("#293A4B")
        self.visual.solid_album_background_fallback = "#445566"
        self._complete()
        self.assertNotEqual(self.visual._solid_album_palette_cache_key, old_key)
        self.visual._solid_album_surface_palette()
        self.assertEqual(len(self.jobs), 1)
        self.assertNotEqual(self.visual._solid_album_palette_worker_key, old_key)

    def test_stale_artwork_between_request_and_delivery_cannot_overwrite_current(self):
        self._cover("#C02849")
        self.visual._solid_album_surface_palette()
        old_key = self.visual._solid_album_palette_worker_key
        self._cover("#38B75E")
        self._complete()
        self.assertNotEqual(self.visual._solid_album_palette_cache_key, old_key)

    def test_failure_does_not_restart_each_paint_but_new_cover_can_retry(self):
        self._cover("#C02849")
        self.visual._solid_album_surface_palette()
        with patch("local.island_stack_visual.artwork_surface_palette", side_effect=ValueError("fixture failure")):
            self._complete()
        for _ in range(20):
            self.visual._solid_album_surface_palette()
        self.assertFalse(self.jobs)
        self._cover("#38B75E")
        self.visual._solid_album_surface_palette()
        self.assertEqual(len(self.jobs), 1)

    def test_close_show_keeps_active_bound_and_old_completion_is_safe(self):
        self._cover("#C02849")
        self.visual._solid_album_surface_palette()
        old_token = self.visual._solid_album_palette_active_token
        self.visual.close()
        with patch.object(self.visual, "_center_on_screen"), patch.object(
            self.visual, "_refresh_top_notch_position",
        ), patch.object(self.visual, "_force_super_topmost"):
            self.visual.showEvent(QShowEvent())
        final = self._cover("#38B75E")
        self.visual._solid_album_surface_palette()
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual(self.visual._solid_album_palette_active_token, old_token)
        self._complete()
        self.assertEqual(len(self.jobs), 1)
        self._complete()
        self.assertFalse(self.jobs)
        self.assertEqual(self.visual._solid_album_palette_cache_key[0], final.cacheKey())
        self.assertEqual(solid_album_background_color(self._settle()).name(), "#38b75e")

    def test_completion_after_close_or_qobject_deletion_is_harmless(self):
        self._cover("#C02849")
        self.visual._solid_album_surface_palette()
        self.visual.close()
        self._complete()
        self.assertIsNone(self.visual._solid_album_palette_cache_key)
        self.assertIsNone(self.visual._solid_album_palette_active_token)
        self.visual._resume_solid_album_palette_worker()
        self._cover("#38B75E")
        self.visual._solid_album_surface_palette()
        self.visual.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self._complete()
        # Keep tearDown from touching an already deleted Qt wrapper.
        self.visual = IslandStackVisual(EmptyIslandDataProvider(empty_visual_data()))
        self.visual.timer.stop()

    def test_shared_window_cache_hit_avoids_any_island_job(self):
        image = self._cover("#C02849")
        prepared = artwork_surface_palette(image, self.visual.album_color, self.visual.secondary_color)
        with patch("local.island_stack_visual.artwork_surface_palette", side_effect=AssertionError("duplicate extraction")):
            result = self.visual._solid_album_surface_palette()
        self.assertFalse(self.jobs)
        self.assertEqual(result.primary, prepared.primary)
        self.assertEqual(solid_album_background_color(result).name(), "#c02849")

    def test_production_runner_extracts_on_worker_and_never_blocks_gui_request(self):
        self._cover("#C02849")
        self.visual._run_background = IslandStackVisual._run_background.__get__(self.visual)
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        gui_thread = threading.get_ident()
        worker_threads = []

        def extract(image, primary, secondary):
            worker_threads.append(threading.get_ident())
            entered.set()
            if not release.wait(timeout=3.0):
                raise AssertionError("GUI request waited on extraction")
            result = artwork_surface_palette(image, primary, secondary)
            finished.set()
            return result

        with patch("local.island_stack_visual.artwork_surface_palette", side_effect=extract):
            self.visual._solid_album_surface_palette()
            try:
                self.assertTrue(entered.wait(timeout=2.0))
                self.assertNotEqual(worker_threads, [gui_thread])
                self.assertIsNone(self.visual._solid_album_palette_cache_key)
            finally:
                release.set()
            self.assertTrue(finished.wait(timeout=2.0))
            deadline = time.monotonic() + 2.0
            while self.visual._solid_album_palette_active_token is not None and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.001)
        self.assertIsNotNone(self.visual._solid_album_palette_cache_key)

    def test_visible_large_surface_is_allowed_to_fill_shared_cache_first(self):
        image = self._cover("#C02849")
        self.visual.music_fullscreen_overlay = SimpleNamespace(isVisible=lambda: True)
        first = self.visual._solid_album_surface_palette()
        self.assertFalse(self.jobs)
        self.assertEqual(solid_album_background_color(first).name(), "#345678")
        artwork_surface_palette(image, self.visual.album_color, self.visual.secondary_color)
        self.visual._solid_album_surface_palette()
        self.assertFalse(self.jobs)
        self.assertEqual(solid_album_background_color(self._settle()).name(), "#c02849")
        self.visual.music_fullscreen_overlay = None

    def test_smooth_handoff_holds_displayed_color_and_retargets_without_default_flash(self):
        self._cover("#C02849")
        self.visual._solid_album_surface_palette()
        self._complete()
        self._settle()
        previous = self.visual._solid_album_background_color()
        self._cover("#38B75E")
        waiting = self.visual._solid_album_background_color()
        self.assertEqual(waiting, previous)
        before = time.monotonic()
        self._complete()
        colors = []
        for elapsed in (0.0, 0.105, 0.21, 0.315, 0.5):
            with patch("local.island_stack_visual.time.monotonic", return_value=before + elapsed):
                colors.append(self.visual._solid_album_background_color())
        self.assertEqual(colors[0], previous)
        self.assertEqual(colors[-1].name(), "#38b75e")
        self.assertTrue(all(colors[index + 1].green() >= colors[index].green() for index in range(4)))
        self.assertTrue(all(color.name() != "#345678" for color in colors))

    def test_cover_fetch_gap_holds_last_color_until_new_art_arrives(self):
        self._cover("#C02849")
        self.visual._solid_album_surface_palette()
        self._complete()
        self._settle()
        previous = self.visual._solid_album_background_color()
        self.visual.album_art = QImage()
        self.visual.album_art_lookup_state = "pending"
        self.visual.album_color = QColor("#103040")
        self.assertEqual(self.visual._solid_album_background_color(), previous)
        self.assertFalse(self.jobs)
        self._cover("#38B75E")
        self.visual.album_art_lookup_state = "ready"
        self.assertEqual(self.visual._solid_album_background_color(), previous)
        self.assertEqual(len(self.jobs), 1)


if __name__ == "__main__":
    unittest.main()
