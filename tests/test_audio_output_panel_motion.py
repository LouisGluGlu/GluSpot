from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QWheelEvent
from PySide6.QtWidgets import QApplication, QWidget

from api.audio_routes import AudioRouteDevice
from local.audio_output_panel import AudioOutputPanel
from tests.test_audio_output_panel import _RouteProvider


class _WheelOwner(QWidget):
    def __init__(self):
        super().__init__()
        self.wheels = 0

    def wheelEvent(self, event):
        self.wheels += 1
        event.accept()


class AudioOutputPanelMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _WheelOwner()
        self.owner.resize(640, 360)
        self.owner.show()
        self.provider = _RouteProvider()
        self.provider.snapshot = replace(self.provider.snapshot, devices=tuple(
            replace(device, volume=.5) if device.id == "local" else device
            for device in self.provider.devices))
        self.panel = AudioOutputPanel(self.provider, self.owner)
        with patch("local.audio_output_panel.time.monotonic", return_value=100.0):
            self.panel.open_at(QPoint(620, 65), source_rect=QRectF(578, 11, 42, 42))
        self.panel._animation_timer.stop()
        self.panel._poll_timer.stop()
        self.panel._reveal = 1.0
        self.panel._apply_reveal_pose()
        for row in self.panel._rows.values():
            row.advance(1.0)
        self.app.processEvents()
        self.provider.calls.clear()

    def tearDown(self):
        self.panel.dismiss(immediate=True)
        self.owner.close()

    def _move(self, widget, position, now):
        global_point = widget.mapToGlobal(position)
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(position), QPointF(global_point),
                            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        with patch("local.audio_output_panel.time.monotonic", return_value=now):
            self.app.sendEvent(widget, event)

    def _wheel(self, widget, position, now, *, angle=120, pixel=0, inverted=False):
        event = QWheelEvent(QPointF(position), QPointF(widget.mapToGlobal(position)),
                            QPoint(0, pixel), QPoint(0, angle), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, inverted)
        event.ignore()
        with patch("local.audio_output_panel.time.monotonic", return_value=now):
            self.app.sendEvent(widget, event)
        self.assertTrue(event.isAccepted())
        self.assertEqual(self.owner.wheels, 0)

    def _add_list(self, count):
        extra = tuple(AudioRouteDevice(f"device-{index}", f"音频设备 {index}") for index in range(count))
        self.provider.snapshot = replace(self.provider.snapshot, devices=self.provider.snapshot.devices + extra)
        self.panel.sync_snapshot()
        self.app.processEvents()

    def test_selected_white_fill_requires_one_second_then_adjusts_only_its_volume(self):
        row = self.panel._rows["local"]
        point = QPoint(75, 25)
        self.assertTrue(row.volume_hit(QPointF(point)))
        self._move(row, point, 101.0)
        self._wheel(row, point, 101.99)
        self.assertEqual(self.provider.calls, [])
        self._wheel(row, point, 102.01)
        self.assertEqual(self.provider.calls, [("volume", "local", .52)])

    def test_grey_half_name_and_other_devices_cannot_own_volume_wheel(self):
        row = self.panel._rows["local"]
        point = QPoint(row.width() - 20, 25)
        self.assertFalse(row.volume_hit(QPointF(point)))
        self._move(row, point, 101.0)
        self._wheel(row, point, 110.0)
        self.assertEqual(self.provider.calls, [])
        other = self.panel._rows["bluetooth"]
        self.assertTrue(other.volume_slider.isHidden())
        self._move(other, QPoint(75, 25), 111.0)
        self._wheel(other, QPoint(75, 25), 115.0)
        self.assertEqual(self.provider.calls, [])

    def test_leaving_or_crossing_fill_boundary_resets_dwell(self):
        row = self.panel._rows["local"]
        white, grey = QPoint(75, 25), QPoint(row.width() - 20, 25)
        self._move(row, white, 101.0)
        self._move(row, grey, 102.1)
        self._move(row, white, 102.2)
        self._wheel(row, white, 102.9)
        self.assertEqual(self.provider.calls, [])
        self.app.sendEvent(row, QEvent(QEvent.Type.Leave))
        self._move(row, white, 103.0)
        self._wheel(row, white, 103.9)
        self.assertEqual(self.provider.calls, [])
        self._wheel(row, white, 104.01)
        self.assertEqual(self.provider.calls, [("volume", "local", .52)])

    def test_zero_unknown_and_unselected_have_no_white_hit_surface(self):
        self.provider.snapshot = replace(self.provider.snapshot, devices=tuple(
            replace(device, volume=0) if device.id == "local" else device
            for device in self.provider.snapshot.devices))
        self.panel.sync_snapshot()
        self.assertFalse(self.panel._rows["local"].volume_hit(QPointF(2, 28)))
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod")
        self.panel.sync_snapshot()
        row = self.panel._rows["homepod"]
        self.assertEqual(row.state, "active")
        self.assertTrue(row.volume_slider.isHidden())
        self.assertFalse(row.volume_hit(QPointF(75, 25)))

    def test_pixel_volume_accumulates_small_deltas_and_preserves_native_direction(self):
        row, point = self.panel._rows["local"], QPoint(75, 25)
        self._move(row, point, 101.0)
        for now in (102.1, 102.2):
            self._wheel(row, point, now, angle=0, pixel=3)
        self.assertEqual(self.provider.calls, [])
        self._wheel(row, point, 102.3, angle=0, pixel=3, inverted=True)
        self.assertEqual(self.provider.calls, [("volume", "local", .51)])
        self._wheel(row, point, 102.4, angle=-120)
        self.assertEqual(self.provider.calls[-1], ("volume", "local", .49))

    def test_wheel_over_margin_controls_and_scroll_edges_never_reaches_lyrics(self):
        self._add_list(9)
        bar = self.panel.scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        self._wheel(self.panel, QPoint(4, 80), 101.0, angle=-120)
        self.assertGreater(bar.value(), 0)
        bar.setValue(bar.maximum())
        self._wheel(self.panel.stop_button, QPoint(8, 8), 102.0, angle=-120)
        self.assertEqual(bar.value(), bar.maximum())
        self.assertLess(self.panel._edge_offset, 0)
        self._wheel(self.panel.scroll.viewport(), QPoint(250, 5), 103.0, pixel=4, angle=0)
        self.assertLess(bar.value(), bar.maximum())
        self.assertEqual(self.provider.calls, [])

    def test_feedback_amplitude_scales_with_scroll_range_and_settles(self):
        self._add_list(1)
        bar = self.panel.scroll.verticalScrollBar()
        bar.setRange(0, 8)
        bar.setValue(8)
        self.panel._scroll_list(-100)
        small = abs(self.panel._edge_offset)
        self.panel._edge_offset = 0
        bar.setRange(0, 800)
        bar.setValue(800)
        self.panel._scroll_list(-100)
        self.assertGreater(abs(self.panel._edge_offset), small)
        self.panel._clock.reset(200.0)
        for index in range(90):
            self.panel._advance_animation(200.0 + (index + 1) / 60)
        self.assertEqual(self.panel._edge_offset, 0)
        self.assertEqual(self.panel.rows_widget.y(), -bar.value())

    def test_reveal_dismiss_reopen_retargets_same_spring_without_pose_jump(self):
        self.panel._reveal = .62
        self.panel._reveal_velocity = 1.3
        self.panel._apply_reveal_pose()
        before = (self.panel.pos(), self.panel._reveal, self.panel._reveal_velocity)
        self.panel.dismiss()
        self.assertTrue(self.panel.is_closing)
        self.assertEqual(before, (self.panel.pos(), self.panel._reveal, self.panel._reveal_velocity))
        self.assertFalse(self.panel._poll_timer.isActive())
        with patch("local.audio_output_panel.time.monotonic", return_value=201.0):
            self.panel.open_at(QPoint(620, 65), source_rect=QRectF(578, 11, 42, 42))
        self.assertEqual(before[1:], (self.panel._reveal, self.panel._reveal_velocity))
        self.assertEqual(before[0], self.panel.pos())
        self.assertFalse(self.panel.is_closing)
        self.assertTrue(self.panel._poll_timer.isActive())

    def test_menu_replaces_source_slot_instead_of_floating_below_it(self):
        source = QRectF(568, 20, 42, 42)
        self.panel.open_at(QPoint(610, 74), source_rect=source)
        self.panel._animation_timer.stop()
        self.panel._poll_timer.stop()
        self.assertEqual(self.panel.x() + self.panel.width(), round(source.right()))
        self.assertEqual(self.panel.y(), round(source.top()))
        for amount in (0, .15, .48, .8, 1):
            self.panel._reveal = amount
            self.panel._apply_reveal_pose()
            mapped = self.panel.render_pose.shell_rect.translated(self.panel.x(), self.panel.y())
            self.assertAlmostEqual(mapped.top(), source.top(), delta=.5)
            self.assertAlmostEqual(mapped.right(), source.right(), delta=.5)

    def test_menu_width_follows_source_proportion_without_double_dpi_scaling(self):
        for diameter, expected in ((35, 220), (42, 220), (50, 260), (64, 333)):
            with self.subTest(diameter=diameter):
                self.panel.open_at(QPoint(610, 74), source_rect=QRectF(610 - diameter, 20, diameter, diameter))
                self.panel._animation_timer.stop()
                self.panel._poll_timer.stop()
                self.assertEqual(self.panel.width(), expected)
                self.assertEqual(self.panel._rows["local"].height(), max(48, round(diameter * 1.05)))
                self.assertLessEqual(self.panel.x() + self.panel.width(), self.owner.width() - 12)

    def test_compact_chooser_clamps_to_small_views_and_retains_native_volume(self):
        self._add_list(9)
        for width, height in ((240, 200), (320, 240), (640, 360), (1920, 1080)):
            with self.subTest(viewport=(width, height)):
                self.owner.resize(width, height)
                self.panel.open_at(QPoint(width - 20, 80), source_rect=QRectF(width - 62, 20, 42, 42))
                self.panel._animation_timer.stop()
                self.panel._poll_timer.stop()
                self.panel._reveal = 1
                self.panel._apply_reveal_pose()
                self.app.processEvents()
                self.assertGreaterEqual(self.panel.x(), 12)
                self.assertLessEqual(self.panel.x() + self.panel.width(), width - 12)
                self.assertLessEqual(self.panel.y() + self.panel.height(), height - 12)
                self.assertTrue(self.panel._rows["local"].volume_slider.isEnabled())
                self.assertGreater(self.panel.scroll.verticalScrollBar().maximum(), 0)

    def test_source_near_top_edge_is_never_clipped_by_a_popup_margin(self):
        self.owner.resize(595, 333)
        for top in (0, 9, 9.5):
            with self.subTest(top=top):
                source = QRectF(510, top, 21, 21)
                self.panel.open_at(QPoint(531, round(top + 33)), source_rect=source)
                self.panel._animation_timer.stop()
                self.panel._poll_timer.stop()
                self.assertEqual(self.panel.y(), int(top))
                self.panel._reveal = 0
                self.panel._apply_reveal_pose()
                circle = self.panel.render_pose.shell_rect
                self.assertGreaterEqual(circle.top(), 0)
                self.assertLessEqual(circle.bottom(), self.panel.height())
                self.assertEqual(circle.translated(self.panel.x(), self.panel.y()), source)
                self.panel.sync_snapshot()
                self.assertEqual(self.panel.y(), int(top))
                self.assertLessEqual(self.panel.y() + self.panel.height(), self.owner.height() - 12)

    def test_first_capsule_precedes_vertical_list_and_native_hit_targets(self):
        self.panel._reveal = .48
        self.panel._apply_reveal_pose()
        pose = self.panel.render_pose
        self.assertEqual(pose.row_unfold, 0)
        self.assertLess(pose.shell_rect.height(), self.panel.height() / 2)
        self.assertEqual(self.panel._scroll_opacity.opacity(), 0)
        self.assertFalse(self.panel.contains_visible_point(QPointF(20, self.panel.height() - 20)))
        self.assertTrue(self.panel.contains_visible_point(pose.first_rect.center()))
        self.panel._reveal = 1
        self.panel._apply_reveal_pose()
        self.assertEqual(self.panel._scroll_opacity.opacity(), 1)
        self.assertTrue(self.panel.contains_visible_point(QPointF(20, self.panel.height() - 20)))

    def test_compressed_rows_cannot_select_hidden_devices_or_change_volume(self):
        self.panel._reveal = .48
        self.panel._apply_reveal_pose()
        row = self.panel._rows["bluetooth"]
        point = QPointF(75, 25)
        event = QMouseEvent(QEvent.Type.MouseButtonRelease, point,
            QPointF(row.mapToGlobal(point.toPoint())), Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        self.assertTrue(self.panel.eventFilter(row, event))
        self.assertTrue(event.isAccepted())
        self._wheel(row, point.toPoint(), 110.0)
        self.assertEqual(self.provider.calls, [])

    def test_transition_uses_shared_row_painter_without_device_reads(self):
        row = self.panel._rows["local"]
        natural = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
        natural.fill(Qt.GlobalColor.transparent)
        row.render(natural)
        direct = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
        direct.fill(Qt.GlobalColor.transparent)
        with QPainter(direct) as painter:
            row.paint_contents(painter)
        self.assertEqual(bytes(natural.constBits()), bytes(direct.constBits()))
        for amount in (0, .48, .8):
            self.panel._reveal = amount
            self.panel._apply_reveal_pose()
            before = (self.panel._reveal, self.panel._reveal_velocity, row.elapsed)
            frame = QImage(self.panel.size(), QImage.Format.Format_ARGB32_Premultiplied)
            frame.fill(Qt.GlobalColor.transparent)
            with patch.object(self.provider, "get_audio_route_snapshot", side_effect=AssertionError("paint read")):
                self.panel.render(frame)
            self.assertEqual(before, (self.panel._reveal, self.panel._reveal_velocity, row.elapsed))
            self.assertEqual(self.provider.calls, [])

    def test_reversal_restores_first_capsule_before_returning_source_circle(self):
        self.panel.dismiss()
        self.panel._clock.reset(200)
        widths, heights = [], []
        for index in range(90):
            self.panel._advance_animation(200 + (index + 1) / 60)
            if not self.panel.isVisible():
                break
            pose = self.panel.render_pose
            widths.append(pose.shell_rect.width())
            heights.append(pose.shell_rect.height())
            if .45 < self.panel._reveal < .5:
                self.assertGreater(pose.shell_rect.width(), self.panel.width() * .99)
                self.assertLess(pose.shell_rect.height(), self.panel.height() * .5)
        self.assertFalse(self.panel.isVisible())
        self.assertGreater(widths[0], widths[-1])
        self.assertGreater(heights[0], heights[-1])

    def test_spring_row_overshoot_settles_before_one_native_handoff(self):
        self.panel._reveal = self.panel._reveal_velocity = 0
        self.panel._reveal_target = 1
        self.panel._clock.reset(200)
        ready = []
        peak = 0
        for index in range(90):
            self.panel._advance_animation(200 + (index + 1) / 60)
            unfold = self.panel.render_pose.row_unfold
            peak = max(peak, unfold)
            ready.append(not self.panel._scroll_opacity.isEnabled())
            if unfold > 1:
                self.assertTrue(self.panel._scroll_opacity.isEnabled())
                self.assertFalse(self.panel._native_content_ready())
            if ready[-1]:
                self.assertEqual(self.panel.render_pose.row_unfold, 1)
                self.assertEqual(self.panel._reveal_velocity, 0)
        self.assertGreater(peak, 1.01)
        self.assertLessEqual(peak, 1.02)
        self.assertIn(True, ready)
        self.assertEqual(ready, sorted(ready))

    def test_morph_to_native_handoff_uses_identical_first_row_pixels(self):
        self.panel._reveal = 1
        self.panel._reveal_velocity = .1
        self.panel._apply_reveal_pose()
        transitional = QImage(self.panel.size(), QImage.Format.Format_ARGB32_Premultiplied)
        transitional.fill(Qt.GlobalColor.transparent)
        self.panel.render(transitional)
        self.panel._reveal_velocity = 0
        self.panel._apply_reveal_pose()
        normal = QImage(self.panel.size(), QImage.Format.Format_ARGB32_Premultiplied)
        normal.fill(Qt.GlobalColor.transparent)
        self.panel.render(normal)
        bounds = self.panel.render_pose.first_rect.adjusted(2, 2, -2, -2).toAlignedRect()
        self.assertEqual(bytes(transitional.copy(bounds).constBits()), bytes(normal.copy(bounds).constBits()))

    def test_native_handoff_does_not_offset_or_leak_effect_cached_rows(self):
        self.owner.setStyleSheet("background: #000000;")
        for amount in (.48, 1):
            self.panel._reveal = amount
            self.panel._apply_reveal_pose()
            self.app.processEvents()
        self.assertFalse(self.panel._scroll_opacity.isEnabled())
        frame = QImage(self.owner.size(), QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(Qt.GlobalColor.transparent)
        self.owner.render(frame)
        row = self.panel._rows["local"]
        center = row.mapTo(self.owner, QPoint(80, 40))
        self.assertGreater(frame.pixelColor(center).lightness(), 200)
        outside = QPoint(self.panel.x() + self.panel.width() + 10, center.y())
        if self.owner.rect().contains(outside):
            self.assertEqual(frame.pixelColor(outside).lightness(), 0)

    def test_close_animation_hides_once_and_immediate_handoff_stops_timers(self):
        dismissed = []
        self.panel.dismissed.connect(lambda: dismissed.append(True))
        self.panel.dismiss()
        self.assertTrue(self.panel.isVisible())
        self.panel._clock.reset(200.0)
        for index in range(90):
            self.panel._advance_animation(200.0 + (index + 1) / 60)
            if not self.panel.isVisible():
                break
        self.assertFalse(self.panel.isVisible())
        self.assertEqual(dismissed, [True])
        self.assertFalse(self.panel._animation_timer.isActive())
        self.assertFalse(self.panel._poll_timer.isActive())
        self.panel.open_at(QPoint(620, 65))
        self.panel.dismiss(immediate=True)
        self.assertEqual(dismissed, [True, True])

    def test_clean_panel_retains_scope_accessibility_and_error_messages(self):
        for widget in (self.panel.title_label, self.panel.scope_label, self.panel.refresh_button,
                       self.panel.add_button, self.panel.close_button):
            self.assertTrue(widget.isHidden())
        self.assertIn("电脑全部声音", self.panel.accessibleDescription())
        self.assertIn("电脑全部声音", self.panel.toolTip())
        self.provider.snapshot = replace(self.provider.snapshot, state="error", status_message="无法连接设备")
        self.panel.sync_snapshot()
        self.assertTrue(self.panel.status_label.isVisible())
        self.assertEqual(self.panel.status_label.text(), "无法连接设备")

    def test_row_capsule_paints_actual_half_volume_and_unknown_selection(self):
        row = self.panel._rows["local"]
        row.progress = 1
        frame = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(Qt.GlobalColor.transparent)
        row.render(frame)
        self.assertGreater(frame.pixelColor(8, 28).lightness(), frame.pixelColor(row.width() - 20, 28).lightness() + 70)
        other = self.panel._rows["bluetooth"]
        other.progress = 0
        other.render(frame)
        # State remains native checkable for keyboard/accessibility, while no
        # drawn checkbox is part of this device-capsule rendering.
        self.assertFalse(other.isChecked())


if __name__ == "__main__":
    unittest.main()
