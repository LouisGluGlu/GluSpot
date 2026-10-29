"""Exercise real Qt pointer/touch delivery and exact transport hit targets."""
from __future__ import annotations

import time
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QInputDevice, QMouseEvent, QPaintEvent, QTouchEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from layout import NarrowLayout
from narrow_window import MusicSurfaceWindow, NarrowStudyWindow, make_owner


TOUCH_TYPES = (QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate,
               QEvent.Type.TouchEnd, QEvent.Type.TouchCancel)


class NarrowInputChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = make_owner()
        self.window = NarrowStudyWindow(self.owner, "B", "left")
        self.window._timer.stop()
        self.window.study_timer.stop()
        self.window.freeze_clock = True
        self.window.hints = False
        self.window.show()
        self.app.processEvents()
        self.device = QTest.createTouchDevice(QInputDevice.DeviceType.TouchScreen)

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.owner.close()
        self.app.processEvents()

    def visible(self):
        fade = self.window.visibility.opacity
        fade.start = fade.target = 1.0
        fade.duration = 0
        self.window.visibility.progress = 1.0

    def touch(self, action, position):
        sequence = QTest.touchEvent(self.window, self.device)
        getattr(sequence, action)(0, position.toPoint(), self.window)
        sequence.commit()
        self.app.processEvents()

    def test_pointer_entry_reveals_without_content_relayout(self):
        window = self.window
        window.shelf = window.footer_hover = False
        fade = window.visibility.opacity
        fade.start = fade.target = 0.0
        fade.duration = 0
        before = NarrowLayout(window.width(), window.height(), 0)
        point = QPointF(180, 300)
        event = QEnterEvent(point, point, QPointF(window.mapToGlobal(point.toPoint())))
        QCoreApplication.sendEvent(window, event)
        window.tick()
        self.assertTrue(window.shelf)
        self.assertEqual(fade.target, 1)
        after = NarrowLayout(window.width(), window.height(), 1)
        self.assertEqual(before.viewport, after.viewport)
        self.assertEqual(before.lyric_anchor, after.lyric_anchor)
        QCoreApplication.sendEvent(window, QEvent(QEvent.Type.Leave))
        self.assertFalse(window.shelf)
        self.assertFalse(window.footer_hover)
        now = time.monotonic()
        window.visibility.advance(now, False, False, False)
        window.visibility.advance(now + 1.199, False, False, False)
        self.assertEqual(fade.target, 1)
        window.visibility.advance(now + 1.2, False, False, False)
        self.assertEqual(fade.target, 0)

    def test_touch_sequence_is_accepted_and_released(self):
        window = self.window
        delivered = []
        dispatch = window.event

        def observe(event):
            result = dispatch(event)
            if event.type() in TOUCH_TYPES:
                delivered.append((event.type(), event.isAccepted(), result))
            return result

        point = QPointF(180, 300)
        with patch.object(window, "event", side_effect=observe):
            self.touch("press", point)
            self.assertTrue(window.touch_active)
            self.assertEqual(window.visibility.opacity.target, 1)
            self.touch("move", point + QPointF(1, 1))
            self.assertTrue(window.touch_active)
            self.touch("release", point)
            self.assertFalse(window.touch_active)
            self.touch("press", point)
            self.assertTrue(window.touch_active)
            cancel = QTouchEvent(QEvent.Type.TouchCancel, self.device)
            self.assertTrue(QCoreApplication.sendEvent(window, cancel))
            self.assertTrue(cancel.isAccepted())
            self.assertFalse(window.touch_active)
        self.assertEqual([kind for kind, _, _ in delivered], [
            QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd,
            QEvent.Type.TouchBegin, QEvent.Type.TouchCancel,
        ])
        self.assertTrue(all(accepted and result for _, accepted, result in delivered))

    def test_touch_transport_dispatches_once_at_actual_44px_button(self):
        self.visible()
        layout = NarrowLayout(self.window.width(), self.window.height())
        self.assertEqual((layout.play.width(), layout.play.height()), (44, 44))
        playing = self.owner.media_is_playing
        self.touch("press", layout.play.center())
        self.assertEqual(self.owner.media_is_playing, not playing)
        self.touch("release", layout.play.center())
        self.assertEqual(self.owner.media_is_playing, not playing)
        outside = QPointF(layout.play.right() + 1, layout.play.center().y())
        self.touch("press", outside)
        self.touch("release", outside)
        self.assertEqual(self.owner.media_is_playing, not playing)
        self.touch("press", layout.next.center())
        self.touch("release", layout.next.center())
        self.assertEqual(self.window.song_index, 1)
        self.touch("press", layout.previous.center())
        self.touch("release", layout.previous.center())
        self.assertEqual(self.window.song_index, 0)

    def test_mouse_transport_has_no_old_100x60_hit_slot(self):
        self.visible()
        rect = NarrowLayout(self.window.width(), self.window.height()).play
        playing = self.owner.media_is_playing
        outside = (
            QPointF(rect.left() - 1, rect.center().y()),
            QPointF(rect.right() + 1, rect.center().y()),
            QPointF(rect.center().x(), rect.top() - 1),
            QPointF(rect.center().x(), rect.bottom() + 1),
        )
        for point in outside:
            QTest.mouseClick(self.window, Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.NoModifier, point.toPoint())
            self.assertEqual(self.owner.media_is_playing, playing, point)
        QTest.mouseClick(self.window, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, rect.center().toPoint())
        self.assertEqual(self.owner.media_is_playing, not playing)

    def test_wide_mouse_and_paint_still_delegate_to_production(self):
        window = self.window
        window.resize(960, 540)
        self.app.processEvents()
        self.assertFalse(window.narrow)
        point = QPointF(180, 300)
        event = QMouseEvent(QEvent.Type.MouseButtonPress, point, point,
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        with patch.object(MusicSurfaceWindow, "mousePressEvent", autospec=True) as production:
            window.mousePressEvent(event)
            production.assert_called_once_with(window, event)
        event = QPaintEvent(window.rect())
        with patch.object(MusicSurfaceWindow, "paintEvent", autospec=True) as production:
            window.paintEvent(event)
            production.assert_called_once_with(window, event)


if __name__ == "__main__":
    unittest.main(verbosity=2)
