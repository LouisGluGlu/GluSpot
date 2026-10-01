from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from local.audio_output_panel import AudioOutputPanel
from local.music_surface_window import MusicSurfaceWindow


class _RouteProvider:
    def __init__(self):
        self.devices = (
            AudioRouteDevice("local", "本地扬声器", kind="speaker", is_default=True),
            AudioRouteDevice("bluetooth", "蓝牙耳机", kind="headphones", is_bluetooth=True),
            AudioRouteDevice("homepod", "客厅 HomePod", kind="homepod", transport="airplay"),
        )
        self.snapshot = AudioRouteSnapshot(self.devices, active_id="local")
        self.calls = []
        self.accept = True

    def get_audio_route_snapshot(self):
        return self.snapshot

    def refresh_audio_outputs(self):
        self.calls.append(("refresh",))

    def select_audio_output(self, device_id):
        self.calls.append(("select", device_id))
        return self.accept

    def stop_audio_cast(self):
        self.calls.append(("stop",))

    def add_homepod(self, host):
        self.calls.append(("add", host))
        return self.accept

    def set_audio_output_latency(self, seconds):
        self.calls.append(("latency", seconds))
        self.snapshot = replace(self.snapshot, cast_latency_seconds=seconds, cast_latency_auto=False)

    def set_audio_output_auto_latency(self, enabled):
        self.calls.append(("auto_latency", enabled))
        self.snapshot = replace(self.snapshot, cast_latency_auto=enabled)
        return self.accept


class AudioOutputPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.owner.resize(960, 600)
        self.owner.show()
        self.provider = _RouteProvider()
        self.panel = AudioOutputPanel(self.provider, self.owner)

    def tearDown(self):
        self.panel.dismiss()
        self.owner.close()

    def _open(self):
        self.panel.open_at(QPoint(940, 60))
        self.panel._animation_timer.stop()
        self.panel._poll_timer.stop()

    def test_open_lists_actual_devices_and_only_queues_refresh(self):
        self._open()
        self.assertEqual(self.provider.calls, [("refresh",)])
        self.assertEqual(tuple(self.panel._rows), ("local", "bluetooth", "homepod"))
        self.assertEqual(self.panel._rows["local"].subtitle, "当前输出")
        self.assertEqual(self.panel._rows["bluetooth"].subtitle, "蓝牙音频")
        self.assertFalse(self.panel.stop_button.isEnabled())
        self.assertFalse(self.panel.stop_button.isVisible())
        self.assertFalse(self.panel.advanced_widget.isVisible())
        self.assertLess(self.panel.height(), 310)
        self.assertGreaterEqual(self.panel.x(), 12)
        self.assertLessEqual(self.panel.x() + self.panel.width(), self.owner.width() - 12)

    def test_request_acceptance_does_not_invent_connected_or_pending_state(self):
        self._open()
        self.panel._rows["homepod"].click()
        self.assertIn(("select", "homepod"), self.provider.calls)
        self.assertEqual(self.panel._rows["homepod"].state, "available")
        self.assertEqual(self.panel._rows["local"].state, "active")
        self.provider.snapshot = replace(self.provider.snapshot, pending_id="homepod", state="connecting", status_message="正在连接客厅 HomePod")
        self.panel.sync_snapshot()
        self.assertEqual(self.panel._rows["homepod"].subtitle, "正在连接…")
        self.assertEqual(self.panel.status_label.text(), "正在连接客厅 HomePod")
        self.assertTrue(self.panel.stop_button.isEnabled())
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod", pending_id="", state="streaming")
        self.panel.sync_snapshot()
        self.assertEqual(self.panel._rows["homepod"].subtitle, "正在投放")

    def test_failure_retains_authoritative_active_output_and_exact_reason(self):
        self._open()
        self.provider.snapshot = replace(self.provider.snapshot, pending_id="", state="error", status_message="HomePod 配对失败，请检查设备访问设置")
        self.panel.sync_snapshot()
        self.assertEqual(self.panel.status_label.text(), self.provider.snapshot.status_message)
        self.assertEqual(self.panel._rows["local"].state, "active")
        self.assertEqual(self.panel._rows["homepod"].state, "available")

    def test_snapshot_failure_reveals_message_when_default_status_was_hidden(self):
        self._open()
        self.assertFalse(self.panel.status_label.isVisible())
        with patch.object(self.provider, "get_audio_route_snapshot", side_effect=OSError("gone")):
            self.panel.sync_snapshot()
        self.assertTrue(self.panel.status_label.isVisible())
        self.assertEqual(self.panel.status_label.text(), "音频设备暂时不可用。")
        self.assertFalse(self.panel.stop_button.isEnabled())

    def test_new_snapshot_preserves_surviving_row_motion_and_removes_departed_device(self):
        self._open()
        original = self.panel._rows["local"]
        original.elapsed, original.progress = 0.21, 0.54
        self.provider.snapshot = replace(self.provider.snapshot, devices=(self.provider.devices[0], AudioRouteDevice("new", "新蓝牙音响", is_bluetooth=True)))
        self.panel.sync_snapshot()
        self.assertIs(self.panel._rows["local"], original)
        self.assertEqual((original.elapsed, original.progress), (0.21, 0.54))
        self.assertEqual(self.panel._rows["new"].elapsed, 0.0)
        self.assertNotIn("homepod", self.panel._rows)

    def test_unavailable_output_cannot_dispatch_a_route_action(self):
        self.provider.snapshot = replace(self.provider.snapshot, devices=(replace(self.provider.devices[1], available=False),))
        self._open()
        row = self.panel._rows["bluetooth"]
        self.assertFalse(row.isEnabled())
        row.click()
        self.assertNotIn(("select", "bluetooth"), self.provider.calls)

    def test_manual_device_add_and_stop_use_provider_boundary(self):
        self._open()
        self.panel.advanced_button.click()
        self.panel.host_input.setText(" 192.168.1.25 ")
        self.panel.add_button.click()
        self.assertIn(("add", "192.168.1.25"), self.provider.calls)
        self.assertEqual(self.panel.host_input.text(), "")
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod", state="streaming")
        self.panel.sync_snapshot()
        self.panel.stop_button.click()
        self.assertIn(("stop",), self.provider.calls)
        self.assertEqual(self.panel._rows["homepod"].state, "active")

    def test_cast_delay_control_reads_snapshot_and_changes_only_provider_setting(self):
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod", cast_latency_seconds=1.5,
                                         cast_latency_auto=False)
        self._open()
        self.assertFalse(self.panel.latency_widget.isVisible())
        self.panel.advanced_button.click()
        self.assertTrue(self.panel.latency_widget.isVisible())
        self.assertEqual(self.panel.latency_slider.value(), 150)
        self.assertEqual(self.panel.latency_label.text(), "歌词延后 1.50 秒")
        self.assertFalse(any(call[0] == "latency" for call in self.provider.calls))
        self.panel.latency_slider.setValue(235)
        self.assertIn(("latency", 2.35), self.provider.calls)
        self.assertEqual(self.panel.latency_label.text(), "歌词延后 2.35 秒")
        self.provider.snapshot = replace(self.provider.snapshot, active_id="local")
        self.panel.sync_snapshot()
        self.assertFalse(self.panel.latency_widget.isVisible())

    def test_small_surface_keeps_devices_and_footer_accessible_in_one_scroll_area(self):
        self.owner.resize(640, 360)
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod")
        self._open()
        self.app.processEvents()
        self.assertGreater(self.panel.scroll.viewport().height(), 150)
        self.assertFalse(self.panel.host_input.isVisible())
        self.panel.advanced_button.click()
        self.app.processEvents()
        scroll = self.panel.scroll.verticalScrollBar()
        self.assertGreater(scroll.maximum(), 0)
        scroll.setValue(scroll.maximum())
        bottom = self.panel.host_input.mapTo(self.panel.scroll.viewport(), self.panel.host_input.rect().bottomLeft())
        self.assertLessEqual(bottom.y(), self.panel.scroll.viewport().height())
        self.assertGreaterEqual(bottom.y(), 0)
        self.assertTrue(self.panel.host_input.isVisible())

    def test_more_options_is_keyboard_operable_and_preserves_route_and_motion_state(self):
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod", cast_latency_seconds=1.5)
        self._open()
        row = self.panel._rows["homepod"]
        row.elapsed, row.progress = 0.25, 0.4
        before = tuple(self.provider.calls)
        QTest.keyClick(self.panel.advanced_button, Qt.Key.Key_Space)
        self.assertTrue(self.panel.advanced_widget.isVisible())
        self.assertTrue(self.panel.latency_widget.isVisible())
        self.assertEqual(self.panel.advanced_button.nextInFocusChain(), self.panel.auto_latency_button)
        self.assertEqual(self.panel.auto_latency_button.nextInFocusChain(), self.panel.latency_slider)
        self.assertEqual(self.panel.latency_slider.nextInFocusChain(), self.panel.host_input)
        self.assertEqual((row.elapsed, row.progress), (0.25, 0.4))
        self.assertEqual(tuple(self.provider.calls), before)
        QTest.keyClick(self.panel.advanced_button, Qt.Key.Key_Space)
        self.assertFalse(self.panel.advanced_widget.isVisible())
        self.assertTrue(self.panel.stop_button.isVisible())

    def test_automatic_compensation_shows_estimate_and_manual_toggle_stays_on_session(self):
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod", state="casting",
                                         cast_latency_seconds=1.52, cast_latency_estimate_seconds=1.525)
        self._open()
        self.panel.advanced_button.click()
        self.assertTrue(self.panel.auto_latency_button.isChecked())
        self.assertFalse(self.panel.latency_slider.isEnabled())
        self.assertEqual(self.panel.latency_estimate_label.text(), "投放缓冲估计 1.52 秒")
        self.assertFalse(any(call[0] in {"auto_latency", "latency"} for call in self.provider.calls))
        self.panel.auto_latency_button.click()
        self.assertIn(("auto_latency", False), self.provider.calls)
        self.assertTrue(self.panel.latency_slider.isEnabled())
        self.assertEqual(self.provider.snapshot.active_id, "homepod")
        self.assertFalse(any(call[0] in {"select", "stop"} for call in self.provider.calls))

    def test_cached_music_backdrop_drives_panel_tone_and_readable_neutral_ink(self):
        self._open()
        for backdrop in (QColor(64, 35, 28), QColor(20, 20, 22), QColor(232, 228, 219), QColor(255, 255, 255)):
            with self.subTest(backdrop=backdrop.name()):
                self.owner._background_sample_colors = lambda _rect: (backdrop,)
                self.owner._surface_font = lambda size, weight: self.panel._label_font
                self.panel.sync_snapshot()
                self.assertGreaterEqual(MusicSurfaceWindow._contrast_ratio(self.panel._ink, self.panel._background), 4.5)
                self.assertGreaterEqual(MusicSurfaceWindow._contrast_ratio(self.panel._muted, self.panel._background), 4.5)
                self.assertEqual(self.panel._accent, self.panel._ink)
                self.assertEqual(self.panel._rows["local"].ink, self.panel._ink)
                self.assertEqual(self.panel._rows["local"].label_font.families(), self.panel._label_font.families())
        self.assertEqual(self.panel._background, QColor(255, 255, 255))

    def test_many_devices_scroll_without_exposing_advanced_fields_or_losing_active_row(self):
        self.owner.resize(640, 360)
        extra = tuple(AudioRouteDevice(f"device-{index}", f"音频设备 {index}") for index in range(10))
        self.provider.snapshot = replace(self.provider.snapshot, devices=self.provider.devices + extra)
        self._open()
        self.app.processEvents()
        self.assertGreater(self.panel.scroll.verticalScrollBar().maximum(), 0)
        self.assertFalse(self.panel.advanced_widget.isVisible())
        self.assertEqual(self.panel._rows["local"].state, "active")
        self.assertLessEqual(self.panel.y() + self.panel.height(), self.owner.height() - 12)

    def test_paint_does_not_advance_motion_or_run_provider_actions(self):
        self._open()
        self.panel._opacity.setOpacity(1.0)
        before = (self.panel._appearance_elapsed, self.panel._rows["local"].elapsed, tuple(self.provider.calls))
        frame = QImage(self.panel.size(), QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(Qt.GlobalColor.transparent)
        self.panel.render(frame)
        self.assertEqual(before, (self.panel._appearance_elapsed, self.panel._rows["local"].elapsed, tuple(self.provider.calls)))
        self.assertFalse(frame.isNull())

    def test_stalled_frame_is_bounded_and_dismissal_stops_all_ui_timers(self):
        with patch("local.audio_output_panel.time.monotonic", return_value=100.0):
            self._open()
        self.panel._advance_animation(103.0)
        self.assertLessEqual(self.panel._appearance_elapsed, 1 / 30.0 + 1e-8)
        self.assertLess(self.panel._opacity.opacity(), 0.1)
        self.panel._animation_timer.start()
        self.panel._poll_timer.start()
        self.panel.dismiss()
        self.assertFalse(self.panel._animation_timer.isActive())
        self.assertFalse(self.panel._poll_timer.isActive())


class AudioOutputSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.owner.data_provider = _RouteProvider()
        self.owner.album_color = QColor(40, 90, 170)
        self.owner.secondary_color = QColor(20, 30, 70)
        self.owner.album_art = QImage()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.window.show()
        self.window._timer.stop()

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def test_output_control_uses_existing_idle_hide_and_no_provider_reads_in_paint(self):
        for fullscreen in (True, False):
            self.window._fullscreen = fullscreen
            self.window._reset_control_idle(now=100.0)
            with patch("local.music_surface_window.time.monotonic", return_value=100.0):
                self.window._sync_window_control_rects()
            button = self.window._button_rects["audio_output"]
            translation = self.window._button_rects["translation"]
            self.assertLess(button.right(), translation.left())
            self.assertEqual(button.size(), translation.size())
            with patch("local.music_surface_window.time.monotonic", return_value=110.0):
                self.window._sync_window_control_rects()
            self.assertEqual(self.window._button_rects, {})
        with patch.object(self.owner.data_provider, "get_audio_route_snapshot", side_effect=AssertionError("paint read")):
            frame = QImage(self.window.size(), QImage.Format.Format_RGB32)
            self.window.render(frame)

    def test_escape_closes_selector_before_returning_to_island(self):
        self.window._toggle_audio_output_panel()
        self.assertTrue(self.window._audio_output_panel.isVisible())
        event = SimpleNamespace(key=lambda: Qt.Key.Key_Escape)
        with patch.object(self.window, "return_to_island") as return_to_island:
            self.window.keyPressEvent(event)
        return_to_island.assert_not_called()
        self.assertFalse(self.window._audio_output_panel.isVisible())

    def test_resize_dismisses_selector_and_does_not_leave_polling_running(self):
        self.window._toggle_audio_output_panel()
        self.window.resize(800, 450)
        self.assertFalse(self.window._audio_output_panel.isVisible())
        self.assertFalse(self.window._audio_output_panel._poll_timer.isActive())

    def test_mode_handoff_dismisses_panel_and_keeps_popup_out_of_snapshot(self):
        self.window._toggle_audio_output_panel()
        snapshot = QImage(self.window.size(), QImage.Format.Format_RGB32)
        snapshot.fill(QColor(20, 40, 60))
        observed = []
        def capture():
            observed.append(self.window._audio_output_panel.isVisible())
            return snapshot
        with patch.object(self.window, "_capture_transition_snapshot", side_effect=capture):
            self.window.toggle_presentation_mode()
        self.assertEqual(observed, [False])
        self.assertFalse(self.window._audio_output_panel._poll_timer.isActive())

    def test_output_button_click_does_not_start_window_drag(self):
        self.window._fullscreen = False
        self.window._sync_window_control_rects()
        point = self.window._button_rects["audio_output"].center()
        event = SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton, position=lambda: point,
                                globalPosition=lambda: QPointF(self.window.mapToGlobal(point.toPoint())), accept=lambda: None)
        self.window.mousePressEvent(event)
        self.assertFalse(self.window._moving)
        self.window.mouseReleaseEvent(event)
        self.assertTrue(self.window._audio_output_panel.isVisible())

    def test_lyric_seek_excludes_output_latency_but_keeps_user_offset(self):
        offsets = []
        self.owner._effective_lyric_offset_seconds = lambda include_output_latency=True: offsets.append(include_output_latency) or (0.5 if not include_output_latency else -1.0)
        self.owner.data_provider.seek_playback_position = lambda seconds, **kwargs: self.owner.data_provider.calls.append(("seek", seconds)) or True
        self.window._browse_track_key = "fixture"
        with patch.object(self.window, "_display_lyrics", return_value=(["lyric"], [(10.0, "lyric")])):
            self.window._seek_to_lyric(0)
        self.assertEqual(offsets, [False])
        self.assertIn(("seek", 9.5), self.owner.data_provider.calls)


if __name__ == "__main__":
    unittest.main()
