from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from PySide6.QtCore import QAbstractAnimation, QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QCursor, QEnterEvent, QFocusEvent, QImage, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

from local.mac_style import (
    MacTrafficCapsule,
    _window_icon_coverage,
    _window_icon_mask,
    _window_icon_renderer,
    _window_icon_tint,
    mac_preferences_stylesheet,
    mac_preferences_theme,
    mac_traffic_lights,
)
from local.settings_window import SettingsWindow
from tests.test_mac_preferences_shell import FakeIslandWidget, FakeSettingsManager


ROOT = Path(__file__).resolve().parents[1]


class SettingsWindowControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.app_font = self.app.font()
        self.cursor_position = QCursor.pos()
        self.owner = QWidget()
        self.owner.resize(300, 120)
        self.sink = QLineEdit(self.owner)
        self.sink.setGeometry(165, 20, 110, 30)
        self.window_actions = mock.Mock(
            spec=["close", "showMinimized", "showNormal", "showMaximized", "isMaximized"]
        )
        self.window_actions.isMaximized.return_value = False
        self.traffic = mac_traffic_lights(self.window_actions)
        self.traffic.setParent(self.owner)
        self.traffic.move(15, 15)

    def tearDown(self):
        self.owner.close()
        self.owner.deleteLater()
        self.app.processEvents()
        self.app.setFont(self.app_font)
        if self.cursor_position is not None:
            QCursor.setPos(self.cursor_position)

    def _show(self):
        screen = self.app.primaryScreen()
        self.assertIsNotNone(screen)
        self.owner.move(screen.availableGeometry().topLeft() + QPoint(40, 40))
        self.owner.show()
        self.traffic.show()
        self.owner.raise_()
        self.owner.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(self.owner, 1000))
        self.app.processEvents()
        self.sink.setFocus(Qt.FocusReason.OtherFocusReason)
        for button in self.traffic.buttons:
            button.clearFocus()
        self.traffic._animation.stop()
        self.traffic._hovered = False
        self.traffic._set_progress(0.)

    def _enter(self):
        local = QPointF(self.traffic._capsule_rect().center())
        QApplication.sendEvent(
            self.traffic,
            QEnterEvent(local, local, QPointF(self.traffic.mapToGlobal(local.toPoint()))),
        )

    def _finish(self):
        self.traffic._animation.setCurrentTime(self.traffic._animation.duration())

    def test_red_closes_only_the_settings_owner(self):
        self.traffic.buttons[0].click()
        self.window_actions.close.assert_called_once_with()
        self.window_actions.showMinimized.assert_not_called()
        self.window_actions.showMaximized.assert_not_called()
        self.window_actions.showNormal.assert_not_called()

    def test_yellow_minimizes_without_closing_owner(self):
        self.traffic.buttons[1].click()
        self.window_actions.showMinimized.assert_called_once_with()
        self.window_actions.close.assert_not_called()
        self.window_actions.showMaximized.assert_not_called()
        self.window_actions.showNormal.assert_not_called()

    def test_green_toggles_maximize_and_restore_from_actual_window_state(self):
        self.traffic.buttons[2].click()
        self.window_actions.showMaximized.assert_called_once_with()
        self.window_actions.showNormal.assert_not_called()
        self.window_actions.isMaximized.return_value = True
        self.traffic.buttons[2].click()
        self.window_actions.showNormal.assert_called_once_with()
        self.window_actions.close.assert_not_called()
        self.window_actions.showMinimized.assert_not_called()

    def test_unbound_factory_retains_safe_clickable_controls(self):
        traffic = mac_traffic_lights()
        try:
            for button in traffic.buttons:
                button.click()
        finally:
            traffic.deleteLater()

    def test_group_entry_reveals_all_three_symbols_together(self):
        self._show()
        self._enter()
        self.traffic._animation.setCurrentTime(80)
        self.assertGreater(self.traffic._progress, 0.)
        self.assertLess(self.traffic._progress, 1.)
        self.assertEqual(
            [button._reveal for button in self.traffic.buttons],
            [self.traffic._progress] * 3,
        )
        self._finish()
        self.assertEqual(self.traffic._progress, 1.)
        self.assertEqual([button._reveal for button in self.traffic.buttons], [1.] * 3)

    def test_dispatched_child_and_gap_events_do_not_collapse_capsule(self):
        # Exercise the capsule's event and hit-mask contract without asking
        # the shared physical desktop to route a global cursor movement. The
        # former native fixture failed before its first group Enter arrived;
        # which desktop condition prevented delivery remains unproven.
        self.cursor_position = None
        self.owner.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.owner.show()
        self.traffic.show()
        self.app.processEvents()
        self.sink.setFocus(Qt.FocusReason.OtherFocusReason)
        for button in self.traffic.buttons:
            button.clearFocus()
        self.traffic._animation.stop()
        self.traffic._hovered = False
        self.traffic._set_progress(0.)

        def enter(widget, point):
            QApplication.sendEvent(widget, QEnterEvent(
                QPointF(point), QPointF(widget.mapTo(self.owner, point)),
                QPointF(widget.mapToGlobal(point))))

        def move(widget, point):
            QApplication.sendEvent(widget, QMouseEvent(
                QEvent.Type.MouseMove, QPointF(point), QPointF(widget.mapToGlobal(point)),
                Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

        self._enter()
        self.assertTrue(self.traffic._hovered)
        self._finish()
        for index, button in enumerate(self.traffic.buttons):
            enter(button, button.rect().center())
            move(button, button.rect().center())
            self.assertTrue(self.traffic._hovered)
            self.assertEqual(self.traffic._progress, 1.)
            QApplication.sendEvent(button, QEvent(QEvent.Type.Leave))
            if index < 2:
                right = button.geometry().right()
                left = self.traffic.buttons[index + 1].geometry().left()
                gap = QPoint((right + left) // 2, self.traffic.height() // 2)
                self.assertTrue(self.traffic.mask().contains(gap))
                self.assertFalse(any(child.geometry().contains(gap) for child in self.traffic.buttons))
                move(self.traffic, gap)
                self.assertTrue(self.traffic._hovered)
                self.assertEqual(self.traffic._progress, 1.)
        QApplication.sendEvent(self.traffic, QEvent(QEvent.Type.Leave))
        self.assertFalse(self.traffic._hovered)
        self._finish()
        self.assertEqual(self.traffic._progress, 0.)

    def test_animation_reversal_starts_at_current_pose_without_jump(self):
        self.traffic._set_expanded(True)
        self.traffic._animation.setCurrentTime(90)
        before = self.traffic._progress
        rect = self.traffic._capsule_rect()
        buttons = [button.geometry() for button in self.traffic.buttons]
        self.assertGreater(before, 0.)
        self.assertLess(before, 1.)
        self.traffic._set_expanded(False)
        self.assertAlmostEqual(self.traffic._progress, before)
        self.assertEqual(self.traffic._capsule_rect(), rect)
        self.assertEqual([button.geometry() for button in self.traffic.buttons], buttons)
        self.traffic._animation.setCurrentTime(max(1, self.traffic._animation.duration() // 3))
        decreased = self.traffic._progress
        self.assertLess(decreased, before)
        self.traffic._set_expanded(True)
        self.assertAlmostEqual(self.traffic._progress, decreased)
        self._finish()
        self.assertEqual(self.traffic._progress, 1.)

    def test_all_animation_poses_keep_circles_inside_capsule_and_separate(self):
        self.owner.setStyleSheet(mac_preferences_stylesheet(mac_preferences_theme(False)))
        self.traffic.ensurePolished()
        reserved = self.traffic.size()
        for step in range(21):
            with self.subTest(progress=step / 20.):
                self.traffic._set_progress(step / 20.)
                self.assertEqual(self.traffic.size(), reserved)
                self.assertTrue(QRectF(self.traffic.rect()).contains(self.traffic._capsule_rect()))
                previous = None
                for button in self.traffic.buttons:
                    rect = button.geometry()
                    self.assertEqual(rect.width(), rect.height())
                    self.assertGreaterEqual(rect.width(), 20)
                    self.assertLessEqual(rect.width(), 24)
                    self.assertTrue(self.traffic._capsule_rect().contains(QRectF(rect)))
                    self.assertTrue(self.traffic.mask().contains(rect.center()))
                    for point in (
                        QPoint(rect.left() + 1, rect.center().y()),
                        QPoint(rect.right() - 1, rect.center().y()),
                        QPoint(rect.center().x(), rect.top() + 1),
                        QPoint(rect.center().x(), rect.bottom() - 1),
                    ):
                        self.assertTrue(self.traffic.mask().contains(point))
                    if previous is not None:
                        self.assertLess(previous.right(), rect.left())
                    previous = rect
                self.assertFalse(self.traffic.mask().contains(QPoint(0, 0)))
                self.assertFalse(self.traffic.mask().contains(QPoint(self.traffic.width() - 1, 0)))

    def test_settings_search_and_sidebar_do_not_move_during_capsule_growth(self):
        island = FakeIslandWidget()
        window = SettingsWindow(island, FakeSettingsManager())
        try:
            window.resize(760, 580)
            window.show()
            self.app.processEvents()
            traffic = window.findChild(MacTrafficCapsule)
            self.assertIsNotNone(traffic)
            traffic._animation.stop()
            baseline = (window.search_input.geometry(), window.nav.geometry(), traffic.geometry())
            self.assertTrue(window.property("controlHoverDisabled"))
            for progress in (0., .25, .5, .75, 1., .5, 0.):
                traffic._set_progress(progress)
                window.layout().activate()
                self.app.processEvents()
                self.assertEqual(
                    (window.search_input.geometry(), window.nav.geometry(), traffic.geometry()),
                    baseline,
                )
                for button in traffic.buttons:
                    self.assertIsNone(getattr(button, "_control_hover_registration", None))
        finally:
            window.close()
            window.deleteLater()
            island.deleteLater()
            self.app.processEvents()

    def test_settings_first_show_keeps_search_focus_and_compact_controls(self):
        island = FakeIslandWidget()
        window = SettingsWindow(island, FakeSettingsManager())
        cursor = QCursor.pos()
        try:
            screen = self.app.primaryScreen()
            self.assertIsNotNone(screen)
            QCursor.setPos(screen.availableGeometry().bottomRight())
            window.resize(760, 580)
            window.show()
            window.activateWindow()
            self.assertTrue(QTest.qWaitForWindowActive(window, 1000))
            QTest.qWait(30)
            traffic = window.findChild(MacTrafficCapsule)
            self.assertIsNotNone(traffic)
            self.assertTrue(window.search_input.hasFocus())
            self.assertFalse(any(button.hasFocus() for button in traffic.buttons))
            self.assertEqual(traffic._progress, 0.)
            self.assertEqual([button._reveal for button in traffic.buttons], [0.] * 3)
        finally:
            window.close()
            window.deleteLater()
            island.deleteLater()
            self.app.processEvents()
            QCursor.setPos(cursor)

    def test_native_settings_focus_keeps_first_show_compact_and_click_does_not_lock_hover(self):
        self._show()
        self.assertEqual(self.traffic._progress, 0.)
        for button in self.traffic.buttons:
            self.assertEqual(button.focusPolicy(), Qt.FocusPolicy.NoFocus)
            self.assertFalse(button.hasFocus())
            self.assertFalse(button.autoDefault())
        self._enter()
        self._finish()
        QTest.mouseClick(self.traffic.buttons[1], Qt.MouseButton.LeftButton)
        self.window_actions.showMinimized.assert_called_once_with()
        self.assertFalse(self.traffic.buttons[1].hasFocus())
        QApplication.sendEvent(self.traffic, QEvent(QEvent.Type.Leave))
        self._finish()
        self.assertEqual(self.traffic._progress, 0.)

    def test_explicit_focus_event_reveals_controls_without_changing_callbacks(self):
        self._show()
        red = self.traffic.buttons[0]
        QApplication.sendEvent(red, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason))
        self._finish()
        self.assertEqual(self.traffic._progress, 1.)
        self.assertEqual(red.accessibleName(), "关闭")
        QTest.keyClick(red, Qt.Key.Key_Space)
        self.window_actions.close.assert_called_once_with()
        QApplication.sendEvent(red, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.TabFocusReason))
        self._finish()
        self.assertEqual(self.traffic._progress, 0.)

    def test_hiding_resets_hover_pose_and_stops_pending_animation(self):
        self._show()
        self._enter()
        self.traffic._animation.setCurrentTime(80)
        self.assertGreater(self.traffic._progress, 0.)
        self.traffic.hide()
        self.assertFalse(self.traffic._hovered)
        self.assertEqual(self.traffic._progress, 0.)
        self.assertEqual(self.traffic._animation.state(), QAbstractAnimation.State.Stopped)
        self.assertEqual([button._reveal for button in self.traffic.buttons], [0.] * 3)
        self.traffic.show()
        self.assertEqual(self.traffic._progress, 0.)

    def test_supplied_svg_assets_are_byte_identical_and_renderable(self):
        hashes = {
            "close": "21151d41d1e8d94239b26155b7794be81a3daad31b5fa6f5270a1f072ccea500",
            "minimize": "668a05b8d06d7af2f6fffd44eadaaa5de49dc3151423fb899e434a5c40fe75b0",
            "maximize": "5f522931300adf9af7dd7fe9c9a9019c5ea63dfec6cb952f8bd43f53a2d86a82",
        }
        for key, expected in hashes.items():
            with self.subTest(icon=key):
                path = ROOT / "assets" / "window_controls" / f"{key}.svg"
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)
                self.assertIsNotNone(_window_icon_renderer(key))
                image = _window_icon_mask(key, 64)
                self.assertFalse(image.isNull())
                self.assertEqual(image.format(), QImage.Format.Format_RGBA8888_Premultiplied)
                self.assertGreater(_window_icon_coverage(key), .05)
                self.assertLess(_window_icon_coverage(key), .8)

    def test_icon_coverage_counts_real_alpha_area_including_holes(self):
        for key in ("close", "minimize", "maximize"):
            with self.subTest(icon=key):
                image = _window_icon_mask(key, 256)
                actual = sum(
                    image.pixelColor(x, y).alpha()
                    for y in range(image.height()) for x in range(image.width())
                ) / (255. * image.width() * image.height())
                self.assertAlmostEqual(_window_icon_coverage(key), actual, places=8)
        maximize = _window_icon_mask("maximize", 64)
        self.assertLess(maximize.pixelColor(32, 32).alpha(), 16)
        self.assertGreater(maximize.pixelColor(38, 24).alpha(), 200)

    def test_icon_tints_keep_status_hue_and_adjust_for_different_occupied_areas(self):
        for button in self.traffic.buttons:
            tint = _window_icon_tint(button._icon_key, button._color)
            self.assertLess(tint.value(), button._color.value())
            self.assertGreater(tint.value(), 70)
            self.assertLessEqual(abs(tint.hue() - button._color.hue()), 2)
            self.assertLessEqual(abs(tint.saturation() - button._color.saturation()), 3)
        background = QColor("#e5a65c")
        ordered = sorted(("close", "minimize", "maximize"), key=_window_icon_coverage)
        areas = [_window_icon_coverage(key) for key in ordered]
        brightness = [_window_icon_tint(key, background).value() for key in ordered]
        self.assertLess(areas[0], areas[-1])
        self.assertLess(brightness[0], brightness[-1])
        self.assertEqual(brightness, sorted(brightness))


if __name__ == "__main__":
    unittest.main()
