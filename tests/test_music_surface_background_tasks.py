from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from local.liquid_glass_tasks import GlassPixelTasks
from local.music_surface_background import AlbumFlowBackgroundRenderer, _render_background_snapshot


class CapturedPixelRequests:
    def __init__(self):
        self.requests = []

    def request(self, owner, role, scene, domain, stamp, payload, render, dirty,
                *, byte_count, interval, ready_callback):
        self.requests.append(dict(domain=domain, stamp=stamp, payload=payload, render=render,
                                  callback=ready_callback, bytes=byte_count))

    def finish(self, index=-1):
        request = self.requests[index]
        image = request["render"](request["payload"])
        request["callback"](image, request["domain"], request["stamp"])
        return image


class SceneOwner(QWidget):
    scene = "song-a"

    def scene_identity(self):
        return self.scene


class MusicBackgroundTaskTests(unittest.TestCase):
    COLORS = (QColor(156, 67, 42), QColor(40, 81, 152), QColor(86, 113, 42))

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = SceneOwner()
        self.owner.resize(960, 540)
        self.renderer = AlbumFlowBackgroundRenderer()
        self.tasks = CapturedPixelRequests()
        self.factory = patch("local.liquid_glass_tasks.glass_pixel_tasks", return_value=self.tasks)
        self.factory.start()

    def tearDown(self):
        self.factory.stop()
        self.owner.close()

    def render(self, now, *, size=None, colors=None):
        return self.renderer.render(size or self.owner.size(), colors or self.COLORS, now,
                                    material_owner=self.owner, material_scene=self.owner.scene_identity)

    @staticmethod
    def pixels(image):
        return image.convertToFormat(QImage.Format.Format_RGBA8888).constBits().tobytes()

    def test_first_frame_is_complete_and_next_snapshot_matches_sync_pixels(self):
        first = self.render(0.)
        self.assertFalse(first.isNull())
        self.assertEqual(len(self.tasks.requests), 0)
        previous_colors = self.renderer._painted_colors
        with patch.object(self.renderer, "_paint_field", side_effect=AssertionError("live sync raster")):
            waiting = self.render(.04)
        self.assertEqual(waiting.cacheKey(), first.cacheKey())
        self.assertEqual(self.renderer._painted_colors, previous_colors)
        self.assertEqual(len(self.tasks.requests), 1)
        request = self.tasks.requests[0]
        self.assertIs(request["render"], _render_background_snapshot)

        def immutable_numeric(value):
            return all(immutable_numeric(item) for item in value) if isinstance(value, tuple) else isinstance(value, (int, float))

        self.assertTrue(immutable_numeric(request["payload"]))
        self.assertGreater(request["bytes"], 0)
        actual = self.tasks.finish()
        reference = AlbumFlowBackgroundRenderer()
        reference.render(self.owner.size(), self.COLORS, 0.)
        expected = reference.render(self.owner.size(), self.COLORS, .04)
        self.assertEqual(self.pixels(actual), self.pixels(expected))
        self.assertEqual(self.renderer._painted_colors, reference._painted_colors)
        self.assertEqual(self.renderer._painted_strengths, reference._painted_strengths)
        self.assertEqual(self.render(.04).cacheKey(), actual.cacheKey())
        self.assertIsNotNone(self.renderer.contrast_backdrop(actual))
        self.assertIsNone(self.renderer.contrast_backdrop(first))

    def test_retarget_uses_displayed_palette_and_rejects_old_palette_result(self):
        first = self.render(0.)
        colors = self.renderer._painted_colors
        self.render(.04)
        self.render(.05, colors=(QColor(43, 163, 113), QColor(170, 44, 100)))
        self.assertEqual(self.renderer._from_colors, colors)
        self.tasks.finish(0)
        self.assertEqual(self.renderer._frame.cacheKey(), first.cacheKey())
        latest = self.tasks.finish(1)
        self.assertEqual(self.renderer._frame.cacheKey(), latest.cacheKey())
        self.assertIsNotNone(self.renderer.contrast_backdrop(latest))

    def test_same_raster_size_but_changed_logical_size_rejects_old_result(self):
        first = self.render(0.)
        self.render(.04)
        self.render(.05, size=QSize(1920, 1080))
        old, new = self.tasks.requests
        self.assertEqual(old["payload"][:2], new["payload"][:2])
        self.assertNotEqual(old["domain"], new["domain"])
        self.tasks.finish(0)
        self.assertEqual(self.renderer._frame.cacheKey(), first.cacheKey())
        latest = self.tasks.finish(1)
        self.assertEqual(self.renderer._frame.cacheKey(), latest.cacheKey())

    def test_synchronous_snapshot_fences_pending_live_result(self):
        self.render(0.)
        self.render(.04)
        snapshot = self.renderer.render(self.owner.size(), self.COLORS, .05)
        self.assertIsNone(self.renderer._async_domain)
        self.tasks.finish(0)
        self.assertEqual(self.renderer._frame.cacheKey(), snapshot.cacheKey())
        self.assertIsNotNone(self.renderer.contrast_backdrop(snapshot))

    def test_real_pixel_worker_uses_background_thread_and_gui_delivery(self):
        self.owner.show()
        self.app.processEvents()
        gui = threading.get_ident()
        workers, deliveries = [], []

        class ObservedRenderer(AlbumFlowBackgroundRenderer):
            def _accept_background_frame(self, image, domain, stamp):
                deliveries.append(threading.get_ident())
                super()._accept_background_frame(image, domain, stamp)

        self.renderer = ObservedRenderer()
        service = GlassPixelTasks(self.owner)
        original = _render_background_snapshot

        def render(snapshot):
            workers.append(threading.get_ident())
            return original(snapshot)

        try:
            with patch("local.liquid_glass_tasks.glass_pixel_tasks", return_value=service), \
                    patch("local.music_surface_background._render_background_snapshot", side_effect=render):
                first = self.render(0.)
                self.render(.04)
                deadline = time.monotonic() + 3.
                while not deliveries and time.monotonic() < deadline:
                    QTest.qWait(5)
                self.assertEqual(deliveries, [gui])
                self.assertEqual(len(workers), 1)
                self.assertNotEqual(workers[0], gui)
                self.assertNotEqual(self.renderer._frame.cacheKey(), first.cacheKey())
                self.assertEqual(service.submitted, 1)
                self.assertIsNone(service.active)
                self.assertEqual(len(service.pending), 0)
        finally:
            service.close()

    def test_real_worker_scene_change_without_new_render_cannot_publish_old_frame(self):
        self.owner.show()
        self.app.processEvents()
        service = GlassPixelTasks(self.owner)
        entered, release = threading.Event(), threading.Event()
        original = _render_background_snapshot

        def slow(snapshot):
            entered.set()
            release.wait(2.)
            return original(snapshot)

        try:
            with patch("local.liquid_glass_tasks.glass_pixel_tasks", return_value=service), \
                    patch("local.music_surface_background._render_background_snapshot", side_effect=slow):
                first = self.render(0.)
                self.render(.04)
                deadline = time.monotonic() + 1.
                while not entered.is_set() and time.monotonic() < deadline:
                    QTest.qWait(5)
                self.assertTrue(entered.is_set())
                self.owner.scene = "song-b"
                release.set()
                deadline = time.monotonic() + 2.
                while service.active is not None and time.monotonic() < deadline:
                    QTest.qWait(5)
                self.assertIsNone(service.active)
                self.assertEqual(service.discarded, 1)
                self.assertEqual(self.renderer._frame.cacheKey(), first.cacheKey())
        finally:
            release.set()
            service.close()


if __name__ == "__main__":
    unittest.main()
