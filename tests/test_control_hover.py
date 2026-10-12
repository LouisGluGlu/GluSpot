from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QListWidget, QWidget
from shiboken6 import isValid

from local.control_hover import (
    HOVER_SCALE, HoverMotion, WidgetHoverGroup, advance_neighbor_spring, install_container_hover, install_control_hover,
    install_item_hover, neighbor_displacement,
)
from local.liquid_glass import GlassPushButton
from local.no_wheel_widgets import NoWheelComboBox, NoWheelSlider, NoWheelSpinBox


class ControlHoverMathTests(unittest.TestCase):
    def test_growth_decelerates_and_finishes_at_half_second(self):
        motion = HoverMotion()
        motion.set_hovered(True, 10.)
        samples = [motion.value(10. + index * .1) for index in range(6)]
        increments = [right - left for left, right in zip(samples, samples[1:])]
        self.assertTrue(all(value > 0 for value in increments))
        self.assertTrue(all(left > right for left, right in zip(increments, increments[1:])))
        self.assertEqual(samples[0], 0.)
        self.assertEqual(samples[-1], 1.)
        self.assertFalse(motion.is_animating(10.5))

    def test_reversing_or_reentering_preserves_the_current_pose(self):
        motion = HoverMotion()
        motion.set_hovered(True, 10.)
        pose = motion.value(10.12)
        motion.set_hovered(False, 10.12)
        self.assertEqual(motion.value(10.12), pose)
        smaller = motion.value(10.22)
        self.assertLess(smaller, pose)
        motion.set_hovered(True, 10.22)
        self.assertEqual(motion.value(10.22), smaller)
        self.assertEqual(motion.value(10.72), 1.)

    def test_equal_target_does_not_restart_or_accumulate_growth(self):
        motion = HoverMotion()
        self.assertTrue(motion.set_hovered(True, 20.))
        self.assertFalse(motion.set_hovered(True, 20.2))
        self.assertEqual(motion.value(20.5), 1.)
        self.assertEqual(motion.value(200.), 1.)

    def test_near_neighbors_move_outward_and_far_neighbors_move_less(self):
        active = QRectF(100., 100., 44., 44.)
        left = neighbor_displacement(QRectF(46., 100., 44., 44.), active, 1.)
        right = neighbor_displacement(QRectF(154., 100., 44., 44.), active, 1.)
        far = neighbor_displacement(QRectF(244., 100., 44., 44.), active, 1.)
        self.assertLess(left.x(), 0.)
        self.assertGreater(right.x(), far.x())
        self.assertGreater(far.x(), 0.)
        self.assertAlmostEqual(left.x(), -right.x())
        self.assertEqual(left.y(), 0.)
        self.assertLessEqual(right.x(), 12.)
        self.assertEqual(neighbor_displacement(active, active, 1.), QPointF())
        self.assertEqual(neighbor_displacement(QRectF(154, 100, 44, 44), active, 0.), QPointF())

    def test_neighbor_spring_retains_pose_on_retarget_and_settles(self):
        offset, velocity = QPointF(), QPointF()
        for _ in range(50):
            offset, velocity = advance_neighbor_spring(offset, velocity, QPointF(8., 0.), .016)
        self.assertEqual(offset, QPointF(8., 0.))
        self.assertEqual(velocity, QPointF())
        reverse, velocity = advance_neighbor_spring(offset, velocity, QPointF(), .008)
        self.assertGreater(reverse.x(), 0.)
        self.assertLess(reverse.x(), 8.)
        for _ in range(50):
            reverse, velocity = advance_neighbor_spring(reverse, velocity, QPointF(), .016)
        self.assertEqual(reverse, QPointF())
        self.assertEqual(velocity, QPointF())


class ControlHoverWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.owner.resize(420, 220)
        self.owner.setStyleSheet("QWidget { background: #1c202a; }")
        self.first = GlassPushButton("", self.owner)
        self.second = GlassPushButton("", self.owner)
        for button, x in ((self.first, 50), (self.second, 180)):
            button.setGeometry(x, 70, 100, 40)
            button.setStyleSheet("QPushButton { background: #a2294f; border: none; border-radius: 12px; }")
        self.owner.show()
        self.app.processEvents()
        self.group = self.first._control_hover_registration.group
        self.record = self.first._control_hover_registration.record
        self.group._set_active(None, 100.)

    def tearDown(self):
        self.owner.close()
        self.owner.deleteLater()
        self.app.processEvents()

    def settle_hover(self):
        with patch("local.control_hover.time.monotonic", return_value=100.):
            self.group._set_active(self.record, 100.)
        for step in range(1, 61):
            with patch("local.control_hover.time.monotonic", return_value=100. + step / 60):
                self.group._tick()

    def test_visual_growth_and_neighbor_motion_preserve_real_geometry_and_effects(self):
        geometries = self.first.geometry(), self.second.geometry()
        self.settle_hover()
        self.assertEqual((self.first.geometry(), self.second.geometry()), geometries)
        self.assertAlmostEqual(self.group.visual_rect(self.record, 101.).width(), 100 * HOVER_SCALE)
        second = self.second._control_hover_registration.record
        self.assertGreater(second.offset.x(), 0.)
        self.assertIsNone(self.first.graphicsEffect())
        self.assertIsNone(self.second.graphicsEffect())

    def test_native_pixels_extend_beyond_original_widget_without_an_extra_plate(self):
        self.settle_hover()
        with patch("local.control_hover.time.monotonic", return_value=101.):
            image = QImage(self.owner.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            self.owner.render(image)
        background = QColor("#1c202a")
        self.assertNotEqual(image.pixelColor(47, 90), background)
        self.assertEqual(image.pixelColor(38, 90), background)
        self.assertEqual(image.pixelColor(100, 130), background)

    def test_explicit_native_render_remains_auditable_during_hover(self):
        before = QImage(self.first.size(), QImage.Format.Format_ARGB32_Premultiplied)
        before.fill(Qt.GlobalColor.transparent)
        self.first.render(before)
        self.settle_hover()
        after = QImage(self.first.size(), QImage.Format.Format_ARGB32_Premultiplied)
        after.fill(Qt.GlobalColor.transparent)
        self.first.render(after)
        self.assertGreater(after.pixelColor(50, 20).alpha(), 0)
        self.assertEqual(before.pixelColor(50, 20), after.pixelColor(50, 20))

    def test_dynamic_host_deletion_retires_its_window_level_overlay(self):
        host = QWidget(self.owner)
        host.setGeometry(20, 125, 250, 70)
        button = GlassPushButton("Dynamic", host)
        button.setGeometry(15, 15, 100, 40)
        host.show()
        group = button._control_hover_registration.group
        overlay = group.overlay
        with patch("local.control_hover.time.monotonic", return_value=100.):
            group._set_active(button._control_hover_registration.record, 100.)
        with patch("local.control_hover.time.monotonic", return_value=100.5):
            group._tick()
        self.assertIs(overlay.parentWidget(), self.owner)
        host.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(isValid(overlay))

    def test_parent_child_added_can_reenter_filter_before_qobject_construction_returns(self):
        calls, failures = [], []

        class ReentrantHost(QWidget):
            def childEvent(host, event):
                child = getattr(host, "constructing_group", None)
                if event.added() and child is not None:
                    try:
                        calls.append(child.eventFilter(host, QEvent(QEvent.Type.User)))
                    except Exception as error:
                        failures.append(error)
                super().childEvent(event)

        class ReentrantGroup(WidgetHoverGroup):
            def __init__(group, host):
                # The ChildAdded event still exposes a base QObject wrapper;
                # retain the Python filter that a parent callback can reach.
                host.constructing_group = group
                try:
                    super().__init__(host)
                finally:
                    host.constructing_group = None

        host = ReentrantHost(self.owner)
        group = ReentrantGroup(host)
        self.assertEqual(failures, [])
        self.assertEqual(calls, [False])
        self.assertFalse(group._capturing)

    def test_qt_filter_without_complete_python_state_leaves_native_events_untouched(self):
        group = WidgetHoverGroup.__new__(WidgetHoverGroup)
        QObject.__init__(group)
        host = QWidget(self.owner)
        host.installEventFilter(group)
        try:
            # Qt's native filter may outlive its Python animation state briefly
            # during construction/GC. Neither guard nor timer may be assumed.
            self.assertFalse(group.eventFilter(host, QEvent(QEvent.Type.User)))
            self.app.sendEvent(host, QEvent(QEvent.Type.User))
        finally:
            host.removeEventFilter(group)
            group.deleteLater()

    def test_settled_hover_reuses_native_pixels_without_a_running_timer(self):
        self.settle_hover()
        image = QImage(self.owner.size(), QImage.Format.Format_ARGB32_Premultiplied)
        with patch("local.control_hover.time.monotonic", return_value=101.), patch.object(
            self.first, "render", wraps=self.first.render,
        ) as capture:
            self.owner.render(image)
            self.owner.render(image)
            self.owner.render(image)
        self.assertEqual(capture.call_count, 1)
        self.assertFalse(self.group.timer.isActive())
        self.first.setText("New label")
        with patch("local.control_hover.time.monotonic", return_value=101.), patch.object(
            self.first, "render", wraps=self.first.render,
        ) as capture:
            self.owner.render(image)
        self.assertEqual(capture.call_count, 1)

    def test_idle_return_clears_images_and_hides_overlay(self):
        self.settle_hover()
        image = QImage(self.owner.size(), QImage.Format.Format_ARGB32_Premultiplied)
        with patch("local.control_hover.time.monotonic", return_value=101.):
            self.owner.render(image)
            self.group._set_active(None, 101.)
        for index in range(1, 61):
            with patch("local.control_hover.time.monotonic", return_value=101. + index / 60):
                self.group._tick()
        self.assertFalse(self.group.timer.isActive())
        self.assertFalse(self.group.overlay.isVisible())
        self.assertTrue(all(record.image is None for record in self.group.controls))

    def test_click_on_grown_overflow_maps_to_native_button_and_keyboard_still_works(self):
        self.settle_hover()
        spy = QSignalSpy(self.first.clicked)
        with patch("local.control_hover.time.monotonic", return_value=101.):
            QTest.mouseClick(self.owner, Qt.MouseButton.LeftButton, pos=QPoint(47, 90))
        self.assertEqual(spy.count(), 1)
        QTest.keyClick(self.first, Qt.Key.Key_Space)
        self.assertEqual(spy.count(), 2)

    def test_menu_opens_once_only_after_half_second_and_cancels_on_leave(self):
        opened = Mock()
        install_control_hover(self.first, menu_callback=opened)
        with patch("local.control_hover.time.monotonic", return_value=100.):
            self.group._set_active(self.record, 100.)
        with patch("local.control_hover.time.monotonic", return_value=100.499):
            self.group._tick()
        opened.assert_not_called()
        with patch("local.control_hover.time.monotonic", return_value=100.5):
            self.group._tick()
            self.group._tick()
        opened.assert_called_once()
        with patch("local.control_hover.time.monotonic", return_value=101.):
            self.group._set_active(None, 101.)
            self.group._set_active(self.record, 101.)
        with patch("local.control_hover.time.monotonic", return_value=101.2):
            self.group._set_active(None, 101.2)
        with patch("local.control_hover.time.monotonic", return_value=102.):
            self.group._tick()
        opened.assert_called_once()

    def test_hiding_and_disabling_cancel_motion_and_delayed_menu(self):
        opened = Mock()
        install_control_hover(self.first, menu_callback=opened)
        self.settle_hover()
        opened.reset_mock()
        self.first.setEnabled(False)
        with patch("local.control_hover.time.monotonic", return_value=101.1):
            self.group._tick()
        self.assertIsNone(self.group.active)
        opened.assert_not_called()
        self.owner.hide()
        self.assertFalse(self.group.timer.isActive())
        self.assertFalse(self.group.overlay.isVisible())

    def test_container_covers_native_controls_and_skips_internal_line_edit(self):
        combo = NoWheelComboBox(self.owner)
        combo.addItems(["One", "Two"])
        slider = NoWheelSlider(Qt.Orientation.Horizontal, self.owner)
        spin = NoWheelSpinBox(self.owner)
        editor = QLineEdit(self.owner)
        install_container_hover(self.owner)
        for widget in (combo, slider, spin, editor):
            self.assertIsNotNone(widget._control_hover_registration)
        self.assertIsNone(getattr(spin.lineEdit(), "_control_hover_registration", None))
        self.assertIsNotNone(combo._control_hover_registration.menu_callback)

    def test_slider_drag_during_visual_growth_preserves_absolute_value_mapping(self):
        slider = NoWheelSlider(Qt.Orientation.Horizontal, self.owner)
        slider.setGeometry(50, 145, 200, 28)
        slider.setRange(0, 100)
        slider.setValue(50)
        slider.show()
        record = slider._control_hover_registration.record
        group = slider._control_hover_registration.group
        with patch("local.control_hover.time.monotonic", return_value=100.):
            group._set_active(record, 100.)
        with patch("local.control_hover.time.monotonic", return_value=100.5):
            group._tick()
            QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=QPoint(100, 14))
            QTest.mouseMove(slider, QPoint(160, 14))
            QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=QPoint(160, 14))
        self.assertGreater(slider.value(), 70)
        self.assertLess(slider.value(), 90)
        self.assertEqual(slider.geometry().width(), 200)

    def test_sidebar_keeps_model_selection_and_original_item_font(self):
        view = QListWidget(self.owner)
        view.setGeometry(285, 15, 130, 190)
        view.addItems(["General", "Media", "Reading", "Diagnostics"])
        original_font = view.item(1).font()
        delegate = install_item_hover(view)
        delegate._retarget(1)
        self.assertEqual(view.item(1).font(), original_font)
        view.setCurrentRow(2)
        self.assertEqual(view.currentRow(), 2)
        self.assertIs(install_item_hover(view), delegate)


if __name__ == "__main__":
    unittest.main()
