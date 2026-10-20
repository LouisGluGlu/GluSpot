"""Visible-pose retargets and bounded GUI-only target preparation."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QPushButton, QStyleFactory, QWidget
from shiboken6 import delete, isValid

from local.music_navigation_transition import capture_navigation_frame, MusicNavigationTransition
from tests.test_music_cover_navigation import _LayerWindow


class _FixedLayerWindow(_LayerWindow):
    def navigation_cover_state(self):
        # Unmapped WA_DontShowOnScreen native handles may keep their initial
        # platform location. This fixture supplies explicit global evidence.
        return QRect(self.geometry().topLeft()+QPoint(15, 15), self.size()/3), self.cover, self.angle


class NavigationRetargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pages = [_FixedLayerWindow(350.), _FixedLayerWindow(10.), _FixedLayerWindow(25.)]
        for page in self.pages:
            page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            page.show()
        self.app.processEvents()
        for page, rect in zip(self.pages, (QRect(40, 60, 300, 220), QRect(160, 90, 800, 600),
                                            QRect(-80, 40, 500, 400))):
            page.setGeometry(rect)
        self.nav = MusicNavigationTransition()
        self.nav.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)

    def tearDown(self):
        self.nav.cancel()
        self.nav.close()
        for page in self.pages:
            if isValid(page):
                page.close()
        self.app.processEvents()

    def start(self, *, cover=True):
        frame = capture_navigation_frame(self.pages[0], separate_cover=cover, freeze_cover=True)
        self.assertTrue(self.nav.start(frame, self.pages[1]))
        self.nav._animation.stop()
        return frame

    def pose(self):
        window, cover = self.nav._window_rect_at(), self.nav._cover_rect_at()
        return (window.x(), window.y(), window.width(), window.height(),
                cover.x(), cover.y(), cover.width(), cover.height(), self.nav._cover_angle_at())

    def assert_values_close(self, a, b, places=6):
        self.assertEqual(len(a), len(b))
        for one, two in zip(a, b):
            self.assertAlmostEqual(one, two, places=places)

    def test_reverse_preserves_actual_window_cover_angle_and_incoming_velocity(self):
        self.start()
        self.nav._advance(.37)
        before, velocity = self.pose(), self.nav._pose_velocity()
        frame = self.nav.origin_frame(self.pages[1], separate_cover=True)
        with patch.object(self.pages[1], "grab", side_effect=AssertionError("origin re-grab")):
            self.assertTrue(self.nav.start(frame, self.pages[0]))
        self.nav._animation.stop()
        self.assert_values_close(self.pose(), before)
        self.assert_values_close(self.nav._pose_velocity(), velocity)
        self.assertEqual(self.nav._animation.duration(), 560)
        self.assertTrue(self.nav._cover_transition)
        self.assertTrue(self.pages[0].frozen)
        # Both angle and rectangle continue their incoming velocity before
        # braking into the reverse direction; they never restart at an end.
        epsilon = .00001
        self.nav._advance(epsilon)
        finite_velocity = tuple((new-old)/(epsilon*.560) for new, old in zip(self.pose(), before))
        for observed, expected in zip(finite_velocity, velocity[:9]):
            self.assertAlmostEqual(observed, expected, delta=.15)
        stage = QRectF(self.nav.geometry())
        for index in range(101):
            self.nav._advance(index/100.)
            self.assertTrue(stage.contains(self.nav._window_rect_at()))
            self.assertTrue(stage.contains(self.nav._cover_rect_at()))
        self.assert_values_close(self.nav._pose_velocity(), (0.,)*10)
        self.assertAlmostEqual(self.nav._cover_angle_at() % 360., 350.)

    def test_cover_origin_pixels_and_independent_album_survive_retarget(self):
        self.start()
        self.nav._advance(.42)
        def rendered():
            image = QImage(self.nav.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            self.nav.render(image)
            return image, self.nav.geometry().topLeft()
        before, origin = rendered()
        cover = self.nav._cover_rect_at()
        frame = self.nav.origin_frame(self.pages[1], separate_cover=True)
        self.nav.start(frame, self.pages[0])
        self.nav._animation.stop()
        after, target = rendered()
        center = cover.center().toPoint()
        self.assertEqual(before.pixelColor(center-origin), after.pixelColor(center-target))
        # Sample only opaque interior pixels; re-rasterizing the bounded
        # existing scene can change fractional AA edge coverage slightly.
        equal = total = 0
        rect = self.nav._window_rect_at().adjusted(8, 8, -8, -8).toRect()
        for y in range(rect.top(), rect.bottom(), 11):
            for x in range(rect.left(), rect.right(), 11):
                point = QPoint(x, y)
                a, b = before.pixelColor(point-origin), after.pixelColor(point-target)
                total += 1
                equal += max(abs(a.red()-b.red()), abs(a.green()-b.green()), abs(a.blue()-b.blue())) <= 2
        self.assertGreater(equal/max(1, total), .98)
        self.assertFalse(self.nav._origin_frame.cover_image.isNull())
        self.assertLessEqual(max(self.nav._origin_frame.image.width(), self.nav._origin_frame.image.height()), 1600)

    def test_plain_retarget_keeps_window_pose_and_velocity(self):
        self.start(cover=False)
        self.nav._advance(.55)
        before, velocity = self.nav._window_rect_at(), self.nav._pose_velocity()
        frame = self.nav.origin_frame(self.pages[1])
        self.nav.start(frame, self.pages[0])
        self.nav._animation.stop()
        self.assertEqual(self.nav._window_rect_at(), before)
        self.assert_values_close(self.nav._pose_velocity(), velocity)
        self.assertEqual(self.nav._animation.duration(), 260)

    def test_request_presents_origin_before_capture_and_only_latest_target_runs(self):
        frame = capture_navigation_frame(self.pages[0], separate_cover=True, freeze_cover=True)
        with patch("local.music_navigation_transition.capture_navigation_frame", wraps=capture_navigation_frame) as capture:
            self.assertTrue(self.nav.request_start(frame, self.pages[1]))
            self.assertTrue(self.nav.isVisible())
            self.assertEqual(capture.call_count, 0)
            self.assertIs(self.nav._origin_frame, frame)
            replacement = self.nav.origin_frame(self.pages[0], separate_cover=True)
            self.nav.request_start(replacement, self.pages[2])
            self.assertEqual(capture.call_count, 0)
            self.assertEqual(self.pages[1].windowOpacity(), 1.)
            self.assertTrue(self.pages[1].isEnabled())
            # WA_DontShowOnScreen intentionally has no native expose. Give
            # the hold layer its first real paint before its queued capture.
            image = QImage(self.nav.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            self.nav.render(image)
            self.assertTrue(self.nav._origin_presented)
            self.assertEqual(capture.call_count, 0)
            self.app.processEvents()
            self.assertEqual(capture.call_count, 1)
        self.assertIs(self.nav._destination, self.pages[2])
        self.assertIs(self.nav._origin_frame, frame)
        self.assertIsNone(self.nav._pending_start)
        self.assertFalse(self.nav._incoming_velocity)
        self.assertTrue(self.pages[0].frozen)
        self.assertEqual(self.pages[0].resume_count, 0)
        self.nav.cancel()
        self.assertEqual(self.pages[0].resume_count, 1)

    def test_pending_replacement_samples_at_ready_after_old_motion_continues(self):
        self.start()
        self.nav._advance(.2)
        earlier = self.nav._cover_rect_at()
        frame = self.nav.origin_frame(self.pages[1], separate_cover=True)
        self.nav.request_start(frame, self.pages[2])
        self.pages[1].hide()  # controller's intentional handoff is detached
        self.assertTrue(self.nav.isVisible())
        self.nav._advance(.62)
        ready_pose, ready_velocity = self.pose(), self.nav._pose_velocity()
        self.assertNotEqual(self.nav._cover_rect_at(), earlier)
        self.nav._prepare_pending()
        self.nav._animation.stop()
        self.assert_values_close(self.pose(), ready_pose)
        self.assert_values_close(self.nav._pose_velocity(), ready_velocity)

    def test_finished_old_animation_holds_valid_frame_while_target_is_pending(self):
        self.start()
        frame = self.nav.origin_frame(self.pages[1], separate_cover=True)
        self.nav.request_start(frame, self.pages[2])
        self.nav._advance(1.)
        self.nav._finished()
        self.assertTrue(self.nav.isVisible())
        final = self.nav._cover_rect_at()
        self.nav._prepare_pending()
        self.nav._animation.stop()
        self.assertEqual(self.nav._cover_rect_at(), final)
        self.assertFalse(self.nav._incoming_velocity)

    def test_failed_pending_capture_and_cancel_restore_input_and_release_freezes(self):
        frame = capture_navigation_frame(self.pages[0], separate_cover=True, freeze_cover=True)
        self.nav.request_start(frame, self.pages[1])
        with patch("local.music_navigation_transition.capture_navigation_frame", return_value=None):
            self.nav._prepare_pending()
        self.assertFalse(self.nav.isVisible())
        self.assertTrue(self.pages[1].isEnabled())
        self.assertEqual(self.pages[1].windowOpacity(), 1.)
        self.assertEqual(self.pages[0].resume_count, 1)
        self.assertIsNone(self.nav._pending_start)
        self.nav.cancel()
        self.assertEqual(self.pages[0].resume_count, 1)

    def test_destroying_or_hiding_pending_target_cancels_queued_work(self):
        for destroy in (False, True):
            target = self.pages[2] if destroy else self.pages[1]
            frame = capture_navigation_frame(self.pages[0], separate_cover=True, freeze_cover=True)
            self.nav.request_start(frame, target)
            if destroy:
                delete(target)
            else:
                target.hide()
            self.assertIsNone(self.nav._pending_start)
            self.assertFalse(self.nav.isVisible())
            with patch("local.music_navigation_transition.capture_navigation_frame") as capture:
                self.app.processEvents()
                capture.assert_not_called()

    def test_reentrant_old_ready_cannot_replace_latest_target(self):
        frame = capture_navigation_frame(self.pages[0], separate_cover=True, freeze_cover=True)
        self.nav.request_start(frame, self.pages[1])
        def old_capture(destination, **kwargs):
            result = capture_navigation_frame(destination, **kwargs)
            self.nav.request_start(self.nav.origin_frame(self.pages[0], separate_cover=True), self.pages[2])
            return result
        with patch("local.music_navigation_transition.capture_navigation_frame", side_effect=old_capture):
            self.nav._prepare_pending()
        self.assertIs(self.nav._pending_start[2], self.pages[2])
        self.assertIsNone(self.nav._destination)
        self.nav._prepare_pending()
        self.assertIs(self.nav._destination, self.pages[2])

    def test_target_capture_preserves_enabled_button_style_and_input_fence(self):
        target = QWidget()
        self.pages.append(target)
        target.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        target.setGeometry(80, 100, 320, 160)
        button = QPushButton("Return to music", target)
        style = QStyleFactory.create("Fusion")
        button.setStyle(style)
        button.setGeometry(30, 45, 240, 60)
        target.show()
        self.app.processEvents()
        enabled = capture_navigation_frame(target).image
        target.setEnabled(False)
        disabled = capture_navigation_frame(target).image
        self.assertNotEqual(enabled, disabled)  # real QStyle visual evidence
        target.setEnabled(True)
        frame = capture_navigation_frame(self.pages[0])
        self.nav.request_start(frame, target)
        self.assertFalse(target.isEnabled())
        self.nav._prepare_pending()
        self.nav._animation.stop()
        self.assertEqual(self.nav._target_frame.image, enabled)
        self.assertFalse(target.isEnabled())
        self.nav.cancel()
        self.assertTrue(target.isEnabled())

    def test_capture_scopes_non_mixin_grab_and_restores_scope_on_failure(self):
        target = self.pages[1]
        original_grab = target.grab
        def complete_capture(*args, **kwargs):
            self.assertEqual(target._glass_capture_depth, 1)
            return original_grab(*args, **kwargs)
        with patch.object(target, "grab", side_effect=complete_capture):
            self.assertIsNotNone(capture_navigation_frame(target))
        self.assertEqual(target._glass_capture_depth, 0)
        with patch.object(target, "grab", side_effect=RuntimeError("gone")):
            self.assertIsNone(capture_navigation_frame(target))
        self.assertEqual(target._glass_capture_depth, 0)

    def test_immediate_start_supersedes_pending_request_and_queued_callback(self):
        frame = capture_navigation_frame(self.pages[0], separate_cover=True, freeze_cover=True)
        self.nav.request_start(frame, self.pages[1])
        image = QImage(self.nav.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.nav.render(image)
        replacement = self.nav.origin_frame(self.pages[0], separate_cover=True)
        self.assertTrue(self.nav.start(replacement, self.pages[2]))
        self.nav._animation.stop()
        self.app.processEvents()
        self.assertIsNone(self.nav._pending_start)
        self.assertIs(self.nav._destination, self.pages[2])
        self.assertFalse(self.nav._waiting_initial)
        self.assertEqual(self.pages[1].windowOpacity(), 1.)
        self.assertTrue(self.pages[1].isEnabled())

    def test_minimizing_pending_target_cancels_without_late_capture(self):
        frame = capture_navigation_frame(self.pages[0], freeze_cover=True)
        self.nav.request_start(frame, self.pages[1])
        self.pages[1].showMinimized()
        self.assertIsNone(self.nav._pending_start)
        self.assertFalse(self.nav.isVisible())
        with patch("local.music_navigation_transition.capture_navigation_frame") as capture:
            self.app.processEvents()
            capture.assert_not_called()
        self.assertTrue(self.pages[1].isEnabled())

    def test_cover_to_plain_target_keeps_current_album_in_first_frame(self):
        self.start()
        self.nav._advance(.42)
        before = QImage(self.nav.size(), QImage.Format.Format_ARGB32_Premultiplied)
        before.fill(Qt.GlobalColor.transparent)
        self.nav.render(before)
        center = self.nav._cover_rect_at().center().toPoint()
        before_color = before.pixelColor(center-self.nav.geometry().topLeft())
        target = QWidget()
        self.pages.append(target)
        target.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        target.setGeometry(30, 60, 500, 320)
        target.show()
        self.app.processEvents()
        frame = self.nav.origin_frame(self.pages[1], separate_cover=True)
        self.nav.request_start(frame, target)
        self.nav._prepare_pending()
        self.nav._animation.stop()
        self.assertFalse(self.nav._cover_transition)
        after = QImage(self.nav.size(), QImage.Format.Format_ARGB32_Premultiplied)
        after.fill(Qt.GlobalColor.transparent)
        self.nav.render(after)
        self.assertEqual(after.pixelColor(center-self.nav.geometry().topLeft()), before_color)


if __name__ == "__main__":
    unittest.main()
