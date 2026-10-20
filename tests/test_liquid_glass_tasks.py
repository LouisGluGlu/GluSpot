"""Bounded pixel preparation, strict scene ownership and real Qt captures."""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, QPoint, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from local import liquid_glass as glass
from local.liquid_glass_tasks import GlassPixelTasks
from local.audio_output_panel import AudioOutputPanel, _render_panel_material


class ControlledTasks(QObject):
    completed = Signal(object, object, object)

    def __init__(self):
        super().__init__()
        self.actions = []
        self.closed = False

    def submit(self, token, action):
        self.actions.append((token, action))
        return not self.closed

    def finish(self):
        token, action = self.actions.pop(0)
        self.completed.emit(token, action(), None)

    def close(self):
        self.closed = True
        self.actions.clear()


class Owner(glass.GlassCaptureMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.scene = 'song-a'
        self.paint_glass = False
        self.live_modes = []
        self.scene_image = QImage(100, 60, QImage.Format.Format_RGB32)
        self.scene_image.fill(QColor('#e44b71'))
        self.resize(100, 60)

    def current_scene(self):
        return self.scene

    def paintEvent(self, event):
        if self.paint_glass:
            with QPainter(self) as painter:
                self.live_modes.append(glass.live_glass_paint(painter, self))
                glass.paint_liquid_glass(
                    painter, QRectF(5, 5, 90, 50), radius=15, refractive=True,
                    backdrop=self.scene_image, cache_token=id(self), material_owner=self,
                    material_role='face', material_scene=self.current_scene,
                )


class GlassPixelTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        glass.clear_glass_cache()
        self.owner = Owner()
        self.owner.show()
        self.app.processEvents()
        self.store = GlassPixelTasks(tasks=ControlledTasks())

    def tearDown(self):
        self.store.close()
        self.owner.close()
        self.owner.deleteLater()
        self.app.processEvents()
        glass.clear_glass_cache()

    def image(self, color='red', side=20):
        image = QImage(side, side, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        return image

    def request(self, *, role='face', domain='geometry', stamp=None, image=None):
        image = self.image() if image is None else image
        snapshot = QImage(image)
        return self.store.request(
            self.owner, role, self.owner.current_scene, domain,
            image.cacheKey() if stamp is None else stamp, snapshot, lambda value: value,
            QRectF(2, 3, 20, 20), byte_count=int(snapshot.sizeInBytes()), interval=0.,
        )

    def test_snapshot_wrapper_detaches_from_later_source_writes_and_result_is_reused(self):
        image = self.image('red')
        stamp = image.cacheKey()
        self.assertIsNone(self.request(image=image, stamp=stamp))
        image.fill(QColor('blue'))
        self.store.tasks.finish()
        ready = self.request(image=image, stamp=stamp)
        self.assertEqual(ready.pixelColor(5, 5), QColor('red'))
        self.assertEqual(self.store.submitted, 1)
        self.assertFalse(self.store.pending)

    def test_completed_action_releases_input_even_if_worker_keeps_last_closure(self):
        self.request()
        token, action = self.store.tasks.actions.pop()
        value = action()
        retained = dict(zip(action.__code__.co_freevars,
                            (cell.cell_contents for cell in action.__closure__)))
        self.assertIsNone(retained['payload'])
        self.store._completed(token, value, None)
        self.assertEqual(self.store.input_bytes, 0)
        self.assertEqual(len(self.store.cache), 1)

    def test_only_latest_pending_survives_rapid_song_changes(self):
        self.request()
        for index in range(40):
            self.owner.scene = f'song-{index}'
            self.request(stamp=index, image=self.image('blue'))
        self.assertEqual(len(self.store.tasks.actions), 1)
        self.assertEqual(len(self.store.pending), 1)
        self.store.tasks.finish()
        self.assertFalse(self.store.cache)
        self.store.tasks.finish()
        self.assertEqual(self.store.submitted, 2)
        self.assertEqual(self.store.discarded, 1)
        self.assertEqual(len(self.store.cache), 1)
        self.assertEqual(next(iter(self.store.cache.values())).stamp, 39)

    def test_scene_change_without_another_request_is_checked_on_gui_delivery(self):
        self.request()
        self.owner.scene = 'different-without-another-paint'
        self.store.tasks.finish()
        self.assertFalse(self.store.cache)
        self.assertEqual(self.store.discarded, 1)

    def test_new_geometry_rejects_active_old_crop_but_live_backdrop_frames_do_not_starve(self):
        self.request(domain='old', stamp=1)
        self.request(domain='new', stamp=2)
        self.store.tasks.finish()
        self.assertFalse(self.store.cache)
        self.request(domain='new', stamp=3)
        self.store.tasks.finish()
        self.assertEqual(next(iter(self.store.cache.values())).stamp, 2)
        self.store.tasks.finish()
        self.assertEqual(next(iter(self.store.cache.values())).stamp, 3)
        self.assertEqual(self.store.discarded, 1)

    def test_pending_count_input_bytes_ready_bytes_and_slots_remain_bounded(self):
        self.store.MAX_PENDING = 3
        self.store.MAX_SLOTS = 4
        self.store.MAX_INPUT_BYTES = 3200
        self.store.MAX_CACHE_BYTES = 3200
        for index in range(40):
            self.request(role=f'role-{index}', stamp=index)
            self.assertLessEqual(len(self.store.pending), 3)
            self.assertLessEqual(len(self.store.latest), 4)
            self.assertLessEqual(self.store.input_bytes, 3200)
        while self.store.tasks.actions:
            self.store.tasks.finish()
        self.assertLessEqual(self.store.cache_bytes, 3200)
        self.assertEqual(self.store.cache_bytes,
                         sum(int(ready.image.sizeInBytes()) for ready in self.store.cache.values()))
        self.assertTrue(all(not hasattr(desired, 'payload') for desired in self.store.latest.values()))
        self.assertFalse(self.store.findChildren(QTimer))

    def test_oversized_pending_cannot_accumulate_behind_active_job(self):
        self.store.MAX_INPUT_BYTES = 2000
        self.request(stamp=1)
        for index in range(10):
            self.request(role=f'oversized-{index}', image=self.image(side=40))
            self.assertFalse(self.store.pending)
            self.assertLessEqual(self.store.input_bytes, 2000)
        self.assertEqual(self.store.submitted, 1)

    def test_owner_lru_recycles_idle_rows_and_allows_a_retired_live_row_to_return(self):
        self.store.MAX_OWNERS = 3
        widgets = [Owner() for _ in range(8)]
        try:
            image = self.image()
            for widget in widgets:
                widget.show()
                self.store.request(widget, 'row', widget.current_scene, 'geometry', 1,
                                   QImage(image), lambda value: value, QRectF(widget.rect()),
                                   byte_count=int(image.sizeInBytes()))
                self.store.tasks.finish()
                self.assertLessEqual(len(self.store.owners), 3)
                self.assertLessEqual(len(self.store.owner_ids), 3)
            self.assertNotIn(widgets[0], self.store.owner_ids)
            self.store.request(widgets[0], 'row', widgets[0].current_scene, 'geometry', 1,
                               QImage(image), lambda value: value, QRectF(widgets[0].rect()),
                               byte_count=int(image.sizeInBytes()))
            self.store.tasks.finish()
            self.assertIn(widgets[0], self.store.owner_ids)
            self.assertEqual(len(self.store.owners), 3)
            self.assertEqual(self.store.submitted, 9)
        finally:
            for widget in widgets:
                widget.close()
                widget.deleteLater()
            self.app.processEvents()

    def test_hide_and_close_stop_new_work_and_late_results(self):
        self.request(stamp=1)
        self.request(stamp=2)
        self.owner.hide()
        self.assertFalse(self.store.pending)
        self.store.tasks.finish()
        self.assertFalse(self.store.cache)
        self.assertIsNone(self.request(stamp=3))
        self.owner.show()
        self.request(stamp=4)
        self.assertEqual(self.store.submitted, 2)
        self.store.close()
        self.assertTrue(self.store.tasks.closed)
        self.assertIsNone(self.store.active)
        self.assertFalse(self.store.cache)
        self.assertFalse(self.store.pending)

    def test_worker_pixels_equal_synchronous_glass_and_panel_renderers(self):
        source = self.owner.scene_image
        payload = (QImage(source), (7., 3., 74., 42.), 90., 50., 15., 70)
        expected = glass._render_glass_material(source, QRectF(*payload[1]), *payload[2:])
        self.store.request(self.owner, 'glass', self.owner.current_scene, 'glass', 1, payload,
                           glass._render_glass_request, QRectF(self.owner.rect()),
                           byte_count=int(source.sizeInBytes()))
        self.store.tasks.finish()
        self.assertEqual(next(iter(self.store.cache.values())).image, expected)
        panel_payload = (QImage(source), 7., False, (20, 22, 27, 58), (4, 3, 70, 40))
        expected_panel = _render_panel_material(panel_payload)
        self.store.request(self.owner, 'panel', self.owner.current_scene, 'panel', 1, panel_payload,
                           _render_panel_material, QRectF(self.owner.rect()),
                           byte_count=int(source.sizeInBytes()))
        self.store.tasks.finish()
        self.assertEqual(self.store.cache[(1, 'panel')].image, expected_panel)

    def test_visible_widget_render_and_grab_are_sync_despite_qt_redirecting_the_same_device(self):
        with patch('local.liquid_glass_tasks.glass_pixel_tasks', return_value=self.store):
            self.owner.paint_glass = True
            self.owner.update()
            self.app.processEvents()
            self.assertIn(True, self.owner.live_modes)
            self.assertEqual(self.store.submitted, 1)
            before = self.store.submitted
            with patch.object(glass, '_render_glass_material', wraps=glass._render_glass_material) as raster:
                self.owner.scene_image.fill(QColor('blue'))
                self.owner.live_modes.clear()
                target = self.image(side=100)
                self.owner.render(target)
                self.assertEqual(self.owner.live_modes, [False])
                self.assertGreaterEqual(raster.call_count, 1)
                self.owner.live_modes.clear()
                self.owner.grab()
                self.assertEqual(self.owner.live_modes, [False])
                self.assertEqual(self.store.submitted, before)
                self.assertFalse(self.owner._glass_capture_depth)

    def test_qimage_target_and_explicit_capture_flags_never_submit_async(self):
        with patch('local.liquid_glass_tasks.glass_pixel_tasks', return_value=self.store):
            target = self.image(side=100)
            with QPainter(target) as painter:
                glass.paint_liquid_glass(
                    painter, QRectF(5, 5, 90, 50), refractive=True, backdrop=self.owner.scene_image,
                    material_owner=self.owner, material_role='face', material_scene=self.owner.current_scene,
                )
            self.assertEqual(self.store.submitted, 0)
            self.owner._capturing_transition_snapshot = True
            self.owner.paint_glass = True
            self.owner.update()
            self.app.processEvents()
            self.assertNotIn(True, self.owner.live_modes)
            self.assertEqual(self.store.submitted, 0)

    def test_real_worker_is_off_gui_and_result_callback_is_on_gui(self):
        self.store.close()
        self.store = GlassPixelTasks()
        gui = threading.get_ident()
        workers, deliveries = [], []
        original = self.owner.update
        def update(*args):
            deliveries.append(threading.get_ident())
            return original(*args)
        def render(value):
            workers.append(threading.get_ident())
            return value
        image = self.image()
        with patch.object(self.owner, 'update', side_effect=update):
            self.store.request(self.owner, 'face', self.owner.current_scene, 'geometry', 1,
                               QImage(image), render, QRectF(self.owner.rect()),
                               byte_count=int(image.sizeInBytes()))
            deadline = time.monotonic() + 3.
            while not deliveries and time.monotonic() < deadline:
                QTest.qWait(5)
        self.assertEqual(len(workers), 1)
        self.assertNotEqual(workers[0], gui)
        self.assertEqual(deliveries, [gui])


class AudioPanelPixelIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from tests.test_audio_output_panel import _RouteProvider
        from tests.test_audio_output_panel_glass import _SongPage
        self.page = _SongPage()
        self.page.show()
        self.panel = AudioOutputPanel(_RouteProvider(), self.page)
        self.store = GlassPixelTasks(tasks=ControlledTasks())

    def tearDown(self):
        self.store.close()
        self.panel.dismiss(immediate=True)
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()

    def open_panel(self):
        self.panel.open_at(QPoint(920, 60))
        self.panel._animation_timer.stop()
        self.panel._poll_timer.stop()

    def test_first_open_is_complete_and_live_refresh_preserves_frame_until_pixel_result(self):
        with patch('local.liquid_glass_tasks.glass_pixel_tasks', return_value=self.store), \
                patch('local.audio_output_panel._render_panel_material', wraps=_render_panel_material) as render:
            self.open_panel()
            initial = QImage(self.panel._material)
            self.assertFalse(initial.isNull())
            self.assertEqual(self.store.submitted, 0)
            before = render.call_count
            self.page._background_cache.fill(QColor('#8f55ba'))
            self.panel._material_at = -float('inf')
            self.panel._capture_material()
            self.assertEqual(self.store.submitted, 1)
            self.assertEqual(render.call_count, before)
            self.assertEqual(self.panel._material, initial)
            expected = _render_panel_material(self.store.active.payload)
            self.store.tasks.finish()
            self.assertEqual(self.panel._material, expected)
            self.assertNotEqual(self.panel._material, initial)
            self.assertEqual(self.panel._material_key[-1], self.page._background_cache.cacheKey())

    def test_force_capture_cannot_be_overwritten_by_older_live_refresh(self):
        with patch('local.liquid_glass_tasks.glass_pixel_tasks', return_value=self.store):
            self.open_panel()
            self.page._background_cache.fill(QColor('#8f55ba'))
            self.panel._material_at = -float('inf')
            self.panel._capture_material()
            self.assertEqual(self.store.submitted, 1)
            self.page._background_cache.fill(QColor('#cc6738'))
            self.panel._capture_material(force=True)
            expected, key = QImage(self.panel._material), self.panel._material_key
            self.store.tasks.finish()
            self.assertEqual(self.panel._material, expected)
            self.assertEqual(self.panel._material_key, key)


if __name__ == '__main__':
    unittest.main()
