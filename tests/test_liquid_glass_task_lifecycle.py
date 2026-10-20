"""Independent lifecycle review of bounded, weakly owned pixel jobs."""
from __future__ import annotations

import gc
import unittest
import weakref

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from shiboken6 import delete

from local.liquid_glass_tasks import GlassPixelTasks
from tests.test_liquid_glass_tasks import ControlledTasks, Owner


def _identity_image(value):
    return value


class _CallbackOwner(Owner):
    def delete_on_ready(self, _image, _domain, _stamp):
        delete(self)

    def fail_on_ready(self, _image, _domain, _stamp):
        raise ValueError('callback fixture error')


class GlassPixelLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = Owner()
        self.owner.show()
        self.app.processEvents()
        self.store = GlassPixelTasks(tasks=ControlledTasks())

    def tearDown(self):
        self.store.close()
        self.owner.close()
        self.owner.deleteLater()
        self.app.processEvents()

    def image(self, side=20, color='red'):
        image = QImage(side, side, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        return image

    def request(self, stamp, *, payload=None, render=_identity_image):
        image = self.image() if payload is None else payload
        return self.store.request(self.owner, 'face', self.owner.current_scene,
            'stable-geometry', stamp, QImage(image), render, QRectF(self.owner.rect()),
            byte_count=int(image.sizeInBytes()), interval=0.)

    def test_oversized_result_keeps_compatible_picture_and_does_not_retry_forever(self):
        self.store.MAX_CACHE_BYTES = 2000
        self.request(1)
        self.store.tasks.finish()
        original = self.store.cache[(1, 'face')].image
        oversized = self.image(40, 'blue')
        self.request(2, render=lambda _value: oversized)
        self.store.tasks.finish()
        self.assertEqual(self.store.cache[(1, 'face')].image, original)
        self.assertEqual(self.store.cache_bytes, original.sizeInBytes())
        self.assertEqual(self.store.discarded, 1)
        self.assertEqual(self.request(2), original)
        self.assertEqual(self.store.submitted, 2)

    def test_failed_renderer_keeps_picture_and_same_failed_stamp_is_bounded(self):
        self.request(1)
        self.store.tasks.finish()
        original = self.store.cache[(1, 'face')].image
        self.request(2)
        serial, _action = self.store.tasks.actions.pop(0)
        self.store.tasks.completed.emit(serial, None, 'request_failed')
        self.assertEqual(self.request(2), original)
        self.assertEqual(self.store.submitted, 2)
        self.assertEqual(self.store.cache_bytes, original.sizeInBytes())

    def test_destroyed_owner_is_not_retained_by_scene_getter_or_active_job(self):
        owner = Owner()
        owner.show()
        image = self.image()
        self.store.request(owner, 'face', owner.current_scene, 'geometry', 1,
            QImage(image), _identity_image, QRectF(owner.rect()), byte_count=int(image.sizeInBytes()))
        reference = weakref.ref(owner)
        delete(owner)
        del owner
        gc.collect()
        self.assertIsNone(reference())
        self.assertFalse(self.store.owners)
        self.store.tasks.finish()
        self.assertFalse(self.store.cache)
        self.assertIsNone(self.store.active)

    def test_callback_deleting_owner_does_not_stall_other_owner(self):
        owner = _CallbackOwner()
        owner.show()
        image = self.image()
        self.store.request(owner, 'face', owner.current_scene, 'geometry', 1,
            QImage(image), _identity_image, QRectF(owner.rect()), byte_count=int(image.sizeInBytes()),
            ready_callback=owner.delete_on_ready)
        self.request(2)
        self.store.tasks.finish()
        self.assertEqual(self.store.submitted, 2)
        self.assertIsNotNone(self.store.active)
        self.store.tasks.finish()
        self.assertIsNone(self.store.active)
        self.assertEqual(len(self.store.cache), 1)

    def test_callback_error_is_observable_and_pending_other_job_still_starts(self):
        owner = _CallbackOwner()
        owner.show()
        try:
            image = self.image()
            self.store.request(owner, 'face', owner.current_scene, 'geometry', 1,
                QImage(image), _identity_image, QRectF(owner.rect()), byte_count=int(image.sizeInBytes()),
                ready_callback=owner.fail_on_ready)
            self.request(2)
            token, action = self.store.tasks.actions.pop(0)
            with self.assertRaisesRegex(ValueError, 'callback fixture'):
                self.store._completed(token, action(), None)
            self.assertEqual(self.store.submitted, 2)
            self.assertIsNotNone(self.store.active)
            self.store.tasks.finish()
            self.assertIsNone(self.store.active)
        finally:
            owner.close()
            delete(owner)


if __name__ == '__main__':
    unittest.main()
