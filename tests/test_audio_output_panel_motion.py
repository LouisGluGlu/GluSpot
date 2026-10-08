from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QWheelEvent
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
