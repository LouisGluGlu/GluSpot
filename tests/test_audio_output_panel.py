from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from local.audio_output_panel import AudioOutputPanel
from local.music_surface_window import MusicSurfaceWindow


class _RouteProvider:
    def __init__(self):
        self.devices = (
            AudioRouteDevice("local", "本地扬声器", kind="speaker", is_default=True, volume=.36),
            AudioRouteDevice("bluetooth", "蓝牙耳机", kind="airpods-pro", is_bluetooth=True, volume=.62),
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

    def set_audio_output_volume(self, device_id, volume):
        self.calls.append(("volume", device_id, volume))
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

    def _row_order(self):
        return tuple(item.widget().device.id for index in range(self.panel.rows_layout.count())
                     if (item := self.panel.rows_layout.itemAt(index)).widget() in self.panel._rows.values())

    def test_open_lists_actual_devices_and_only_queues_refresh(self):
        self._open()
        self.assertEqual(self.provider.calls, [("refresh",)])
        self.assertEqual(tuple(self.panel._rows), ("local", "bluetooth", "homepod"))
        self.assertEqual(self.panel._rows["local"].subtitle, "当前输出")
        self.assertEqual(self.panel._rows["bluetooth"].subtitle, "蓝牙音频")
        self.assertFalse(self.panel.stop_button.isEnabled())
        self.assertFalse(self.panel.stop_button.isVisible())
        self.assertFalse(hasattr(self.panel, "advanced_widget"))
        self.assertLess(self.panel.height(), 310)
        self.assertGreaterEqual(self.panel.x(), 12)
        self.assertLessEqual(self.panel.x() + self.panel.width(), self.owner.width() - 12)

    def test_request_acceptance_does_not_invent_connected_or_pending_state(self):
        self._open()
        self.panel._rows["homepod"].click()
        self.assertIn(("select", "homepod"), self.provider.calls)
        self.assertEqual(self.panel._rows["homepod"].state, "available")
        self.assertEqual(self.panel._rows["local"].state, "active")
        self.assertEqual(self._row_order(), ("local", "bluetooth", "homepod"))
        self.provider.snapshot = replace(self.provider.snapshot, pending_id="homepod", state="connecting", status_message="正在连接客厅 HomePod")
        self.panel.sync_snapshot()
        self.assertEqual(self.panel._rows["homepod"].subtitle, "正在连接…")
        self.assertEqual(self._row_order(), ("homepod", "local", "bluetooth"))
        self.assertEqual(self.panel.status_label.text(), "正在连接客厅 HomePod")
        self.assertTrue(self.panel.stop_button.isEnabled())
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod", pending_id="", state="streaming")
        self.panel.sync_snapshot()
        self.assertEqual(self.panel._rows["homepod"].subtitle, "正在投放")

    def test_group_member_selection_and_volume_remain_separate(self):
        second = AudioRouteDevice("homepod-2", "书房 HomePod mini", "homepod-mini", "airplay", volume=.48)
        devices = tuple(replace(d, volume=.41, route_state="active") if d.id in {"homepod", "bluetooth"} else d
                        for d in self.provider.devices) + (replace(second, route_state="active"),)
        self.provider.snapshot = AudioRouteSnapshot(devices, active_id="local", state="casting",
            member_ids=("homepod", "homepod-2", "bluetooth"), group_id="session-1", capture_scope="music_process_tree")
        self.provider.toggle_audio_output = lambda device_id: self.provider.calls.append(("toggle", device_id)) or True
        self._open()
        for identity in ("homepod", "homepod-2", "bluetooth"):
            self.assertEqual(self.panel._rows[identity].state, "active")
            self.assertTrue(self.panel._rows[identity].isChecked())
        self.assertEqual(self.panel._rows["bluetooth"].subtitle, "同步播放")
        self.assertEqual(self.panel._rows["local"].state, "available")
        self.assertFalse(self.panel._rows["local"].isChecked())
        self.assertTrue(self.panel.stop_button.isEnabled())
        self.assertEqual(self.panel.stop_button.text(), "停止同步播放")
        self.panel._rows["homepod-2"].volume_slider.setValue(53)
        self.assertIn(("volume", "homepod-2", .53), self.provider.calls)
        self.assertFalse(any(call[0] in {"toggle", "select"} for call in self.provider.calls))
        self.panel._rows["homepod-2"].click()
        self.assertIn(("toggle", "homepod-2"), self.provider.calls)
        self.assertEqual(self.panel._rows["homepod-2"].state, "active")
        self.assertTrue(self.panel._rows["homepod-2"].isChecked())

    def test_all_pending_group_members_remain_pending_until_native_confirmation(self):
        self.provider.snapshot = replace(self.provider.snapshot, active_id="local", state="connecting",
            pending_ids=("homepod", "bluetooth"), status_message="正在连接同步输出")
        self._open()
        self.assertEqual(self.panel._rows["homepod"].state, "pending")
        self.assertEqual(self.panel._rows["bluetooth"].state, "pending")
        self.assertFalse(self.panel._rows["homepod"].isChecked())
        self.assertFalse(self.panel._rows["bluetooth"].isChecked())
        self.assertFalse(self.panel._rows["local"].isChecked())
        self.assertIn("同步播放", self.panel.title_label.text())
        self.assertIn("仅当前音乐软件", self.panel.scope_label.text())
        self.assertTrue(self.panel.stop_button.isEnabled())
        self.assertTrue(self.panel.stop_button.isVisible())

    def test_single_receiver_sends_all_audio_even_with_active_route_evidence(self):
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod", state="casting",
            capture_scope="all_system", devices=tuple(replace(d, route_state="active", volume=.41)
                                                        if d.id == "homepod" else d for d in self.provider.devices))
        self._open()
        self.assertEqual(self.panel.title_label.text(), "单设备投放")
        self.assertIn("电脑全部声音", self.panel.scope_label.text())
        self.assertEqual(self.panel.stop_button.text(), "停止投放")
        self.assertEqual(self.panel._rows["homepod"].subtitle, "正在投放")
        self.assertTrue(self.panel._rows["homepod"].isChecked())
        self.assertFalse(self.panel._rows["local"].isChecked())

    def test_confirmed_selection_is_first_and_volume_polls_keep_layout_stable(self):
        self._open()
        rows = dict(self.panel._rows)
        self.provider.snapshot = replace(self.provider.snapshot, active_id="bluetooth")
        self.panel.sync_snapshot()
        self.assertEqual(self._row_order(), ("bluetooth", "local", "homepod"))
        self.assertEqual(self.panel._device_order, self._row_order())
        self.assertEqual(self.panel.close_button.nextInFocusChain(), rows["bluetooth"])
        self.assertEqual(rows["bluetooth"].volume_slider.nextInFocusChain(), rows["local"])
        self.assertEqual(rows["local"].volume_slider.nextInFocusChain(), rows["homepod"])
        with patch.object(self.panel.rows_layout, "insertWidget") as insert, \
                patch.object(self.panel.rows_layout, "removeWidget") as remove:
            self.provider.snapshot = replace(self.provider.snapshot, revision=1,
                devices=tuple(replace(device, volume=.5) if device.id == "bluetooth" else device
                              for device in self.provider.devices))
            self.panel.sync_snapshot()
            self.panel.sync_snapshot()
        insert.assert_not_called()
        remove.assert_not_called()
        self.assertEqual(self.panel._rows, rows)
        self.assertEqual(rows["bluetooth"].volume_slider.value(), 50)

    def test_group_members_sort_in_published_order_and_pending_replaces_old_group(self):
        second = AudioRouteDevice("homepod-2", "书房 HomePod mini", "homepod-mini", "airplay")
        devices = self.provider.devices + (second,)
        self.provider.snapshot = AudioRouteSnapshot(devices, active_id="local", state="casting",
            member_ids=("homepod-2", "homepod", "bluetooth"), capture_scope="music_process_tree",
            cast_source_id="local")
        self._open()
        self.assertEqual(self._row_order(), ("bluetooth", "homepod", "homepod-2", "local"))
        self.provider.snapshot = replace(self.provider.snapshot, state="connecting",
            pending_ids=("homepod-2", "homepod"))
        self.panel.sync_snapshot()
        self.assertEqual(self._row_order(), ("homepod", "homepod-2", "local", "bluetooth"))
        self.assertEqual(self.panel._rows["bluetooth"].state, "available")
        self.assertEqual(self.panel._rows["local"].state, "available")
        self.assertEqual(self.panel._rows["homepod"].state, "pending")
        self.assertFalse(any(row.isChecked() for row in self.panel._rows.values()))

    def test_failed_or_disconnected_target_returns_to_authoritative_output(self):
        self.provider.snapshot = replace(self.provider.snapshot, pending_id="homepod", state="connecting")
        self._open()
        local = self.panel._rows["local"]
        homepod = self.panel._rows["homepod"]
        self.provider.snapshot = replace(self.provider.snapshot, pending_id="", state="error",
            status_message="设备已断开，请刷新后重试",
            devices=tuple(replace(device, available=False) if device.id == "homepod" else device
                          for device in self.provider.devices))
        self.panel.sync_snapshot()
        self.assertEqual(self._row_order(), ("local", "bluetooth", "homepod"))
        self.assertIs(self.panel._rows["local"], local)
        self.assertIs(self.panel._rows["homepod"], homepod)
        self.assertFalse(homepod.isEnabled())
        self.provider.snapshot = replace(self.provider.snapshot,
            devices=self.provider.devices[:2])
        self.panel.sync_snapshot()
        self.assertEqual(self._row_order(), ("local", "bluetooth"))
        self.assertNotIn("homepod", self.panel._rows)
        self.assertEqual(local.state, "active")

    def test_empty_active_evidence_does_not_pin_the_default_source(self):
        self.provider.snapshot = replace(self.provider.snapshot, devices=tuple(reversed(self.provider.devices)),
                                         active_id="", state="error")
        self._open()
        self.assertEqual(self._row_order(), ("homepod", "bluetooth", "local"))
        self.assertFalse(any(row.isChecked() for row in self.panel._rows.values()))

    def test_reorder_waits_for_volume_drag_release_and_preserves_focus_and_request(self):
        self._open()
        self.app.processEvents()
        rows = dict(self.panel._rows)
        slider = rows["bluetooth"].volume_slider
        slider.setFocus(Qt.FocusReason.MouseFocusReason)
        slider.setSliderDown(True)
        slider.setValue(79)
        request_until = rows["bluetooth"]._request_until
        before = tuple(self.provider.calls)
        self.provider.snapshot = replace(self.provider.snapshot, pending_id="homepod", state="connecting")
        self.panel.sync_snapshot()
        self.assertEqual(self._row_order(), ("local", "bluetooth", "homepod"))
        self.assertEqual(rows["homepod"].state, "pending")
        self.assertEqual(tuple(self.provider.calls), before)
        slider.setSliderDown(False)
        self.app.processEvents()
        self.assertEqual(self._row_order(), ("homepod", "local", "bluetooth"))
        self.assertEqual(self.panel._rows, rows)
        self.assertIs(self.app.focusWidget(), slider)
        self.assertEqual(slider.value(), 79)
        self.assertEqual(rows["bluetooth"]._requested_volume, .79)
        self.assertEqual(rows["bluetooth"]._request_until, request_until)
        self.assertEqual(tuple(self.provider.calls), before)

    def test_downgrade_shows_target_single_mode_without_counting_old_group(self):
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod", state="casting",
            member_ids=("homepod", "bluetooth"), capture_scope="music_process_tree")
        self._open()
        self.assertEqual(self.panel.stop_button.text(), "停止同步播放")
        self.provider.snapshot = replace(self.provider.snapshot, active_id="local", state="connecting",
            member_ids=(), pending_ids=("homepod",), pending_id="homepod", capture_scope="all_system")
        self.panel.sync_snapshot()
        self.assertEqual(self.panel.title_label.text(), "单设备投放")
        self.assertIn("正在切换到单设备投放", self.panel.scope_label.text())
        self.assertIn("电脑全部声音", self.panel.scope_label.text())
        self.assertEqual(self.panel.stop_button.text(), "停止投放")
        self.assertEqual(self.panel._rows["homepod"].state, "pending")
        self.assertFalse(any(row.isChecked() for row in self.panel._rows.values()))
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod", state="casting",
            member_ids=(), pending_ids=(), pending_id="")
        self.panel.sync_snapshot()
        self.assertEqual(self.panel._rows["homepod"].subtitle, "正在投放")
        self.assertTrue(self.panel._rows["homepod"].isChecked())

    def test_capture_scope_owns_mode_even_when_transient_member_count_differs(self):
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod", state="casting",
            member_ids=("homepod",), capture_scope="music_process_tree")
        self._open()
        self.assertEqual(self.panel.title_label.text(), "同步播放 · 1 个设备")
        self.assertIn("仅当前音乐软件", self.panel.scope_label.text())
        self.assertNotIn("电脑全部声音", self.panel.scope_label.text())
        self.assertEqual(self.panel.stop_button.text(), "停止同步播放")
        self.assertEqual(self.panel._rows["homepod"].subtitle, "同步播放")
        self.provider.snapshot = replace(self.provider.snapshot, capture_scope="all_system",
            member_ids=(), pending_ids=("homepod", "bluetooth"), state="connecting")
        self.panel.sync_snapshot()
        self.assertEqual(self.panel.title_label.text(), "单设备投放")
        self.assertIn("电脑全部声音", self.panel.scope_label.text())
        self.assertEqual(self.panel.stop_button.text(), "停止投放")

    def test_unchanged_snapshot_keeps_rows_and_layout_idle_but_recovers_after_read_error(self):
        self._open()
        self.panel._appearance_elapsed = 1
        with patch.object(self.panel, "_fit_to_content") as fit, \
                patch.object(self.panel, "_sync_tab_order") as tabs, \
                patch.object(self.panel._rows["local"], "set_device") as row_update, \
                patch.object(self.panel, "_capture_material") as material:
            self.panel.sync_snapshot()
            self.panel.sync_snapshot()
        fit.assert_not_called()
        tabs.assert_not_called()
        row_update.assert_not_called()
        self.assertEqual(material.call_count, 2)
        with patch.object(self.provider, "get_audio_route_snapshot", side_effect=OSError("gone")):
            self.panel.sync_snapshot()
        self.assertFalse(self.panel._rows["local"].isEnabled())
        self.panel.sync_snapshot()
        self.assertTrue(self.panel._rows["local"].isEnabled())
        self.assertFalse(self.panel.status_label.isVisible())

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
        with patch("local.audio_output_panel.QInputDialog.getText", return_value=(" 192.168.1.25 ", True)):
            self.panel.add_button.click()
        self.assertIn(("add", "192.168.1.25"), self.provider.calls)
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod", state="casting")
        self.panel.sync_snapshot()
        self.panel.stop_button.click()
        self.assertIn(("stop",), self.provider.calls)
        self.assertEqual(self.panel._rows["homepod"].state, "active")

    def test_cancelled_manual_dialog_does_not_enqueue(self):
        self._open()
        before = tuple(self.provider.calls)
        with patch("local.audio_output_panel.QInputDialog.getText", return_value=("192.168.1.25", False)):
            self.panel.add_button.click()
        self.assertEqual(tuple(self.provider.calls), before)

    def test_no_options_or_manual_delay_controls_and_auto_badge_on_active_receiver_only(self):
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod", state="casting", cast_latency_seconds=1.52)
        self._open()
        for removed in ("advanced_button", "advanced_widget", "latency_slider", "auto_latency_button"):
            self.assertFalse(hasattr(self.panel, removed))
        self.assertEqual(self.panel._rows["homepod"].delay_text, "+1.52s")
        self.assertEqual(self.panel._rows["local"].delay_text, "")
        self.assertIn("自动歌词补偿", self.panel._rows["homepod"].accessibleDescription())
        self.provider.snapshot = replace(self.provider.snapshot, active_id="local")
        self.panel.sync_snapshot()
        self.assertEqual(self.panel._rows["homepod"].delay_text, "")
        self.assertFalse(any(c[0] in {"latency", "auto_latency"} for c in self.provider.calls))

    def test_small_surface_keeps_devices_and_footer_accessible_in_one_scroll_area(self):
        self.owner.resize(640, 360)
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod")
        self._open()
        self.app.processEvents()
        self.assertGreater(self.panel.scroll.viewport().height(), 150)
        bar = self.panel.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        bottom = self.panel.stop_button.mapTo(self.panel.scroll.viewport(), self.panel.stop_button.rect().bottomLeft())
        self.assertLessEqual(bottom.y(), self.panel.scroll.viewport().height())
        self.assertGreaterEqual(bottom.y(), 0)
        self.assertLessEqual(self.panel.y() + self.panel.height(), self.owner.height() - 12)

    def test_keyboard_volume_and_row_selection_are_separate_and_tab_order_is_logical(self):
        self._open()
        row = self.panel._rows["local"]
        self.assertEqual(row.nextInFocusChain(), row.volume_slider)
        QTest.keyClick(row.volume_slider, Qt.Key.Key_Right)
        self.assertIn(("volume", "local", .38), self.provider.calls)
        self.assertFalse(any(c[0] == "select" for c in self.provider.calls))
        QTest.keyClick(row, Qt.Key.Key_Space)
        self.assertIn(("select", "local"), self.provider.calls)

    def test_volume_pointer_scrub_only_queues_volume_and_poll_does_not_reset_drag(self):
        self._open()
        self.app.processEvents()
        row = self.panel._rows["bluetooth"]
        slider = row.volume_slider
        point = QPoint(round(slider.width() * .8), slider.height() // 2)
        QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=point)
        requested = slider.value()
        self.assertGreater(requested, 75)
        self.panel.sync_snapshot()
        self.assertEqual(slider.value(), requested)
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=point)
        self.assertFalse(slider.isSliderDown())
        self.assertFalse(any(c[0] == "select" for c in self.provider.calls))
        self.assertTrue(any(c[0] == "volume" and c[1] == "bluetooth" for c in self.provider.calls))

    def test_volume_request_awaits_readback_then_external_updates_follow(self):
        self._open()
        row = self.panel._rows["local"]
        row.volume_slider.setValue(71)
        self.panel.sync_snapshot()
        self.assertEqual(row.volume_slider.value(), 71)
        self.assertEqual(row.device.volume, .36)
        devices = tuple(replace(d, volume=.71) if d.id == "local" else d for d in self.provider.snapshot.devices)
        self.provider.snapshot = replace(self.provider.snapshot, devices=devices)
        self.panel.sync_snapshot()
        self.assertIsNone(row._requested_volume)
        self.provider.snapshot = replace(self.provider.snapshot, devices=tuple(replace(d, volume=.28) if d.id == "local" else d for d in devices))
        self.panel.sync_snapshot()
        self.assertEqual(row.volume_slider.value(), 28)

    def test_unconfirmed_volume_times_out_to_actual_readback_without_new_action(self):
        self._open()
        row = self.panel._rows["local"]
        with patch("local.audio_output_panel.time.monotonic", return_value=100):
            row.volume_slider.setValue(71)
        before = tuple(self.provider.calls)
        with patch("local.audio_output_panel.time.monotonic", return_value=103):
            self.panel.sync_snapshot()
        self.assertEqual(row.volume_slider.value(), 36)
        self.assertEqual(tuple(self.provider.calls), before)

    def test_failed_volume_reverts_and_preserves_cast_and_original_device_state(self):
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod", state="casting", devices=tuple(replace(d, volume=.4) if d.id == "homepod" else d for d in self.provider.devices))
        self._open()
        self.provider.accept = False
        row = self.panel._rows["homepod"]
        row.volume_slider.setValue(77)
        self.assertEqual(row.volume_slider.value(), 40)
        self.assertEqual(row.state, "active")
        self.assertEqual(self.panel.status_label.text(), "暂时无法调整此设备音量。")
        self.assertFalse(any(c[0] in {"stop", "select"} for c in self.provider.calls))

    def test_unknown_volume_never_exposes_editable_or_fabricated_slider(self):
        self._open()
        slider = self.panel._rows["homepod"].volume_slider
        self.assertTrue(slider.isHidden())
        self.assertFalse(slider.isEnabled())
        self.assertFalse(any(c[0] == "volume" for c in self.provider.calls))

    def test_current_homepod_volume_remains_usable_when_mdns_temporarily_disappears(self):
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod", state="casting", devices=(replace(self.provider.devices[2], volume=.4, available=False),))
        self._open()
        row = self.panel._rows["homepod"]
        self.assertTrue(row.volume_slider.isEnabled())
        row.volume_slider.setValue(45)
        self.assertIn(("volume", "homepod", .45), self.provider.calls)

    def test_muted_source_slider_reports_muting_and_never_queues_route_actions(self):
        self.provider.snapshot = replace(self.provider.snapshot, devices=(replace(self.provider.devices[0], volume_muted=True),))
        self._open()
        slider = self.panel._rows["local"].volume_slider
        self.assertIn("已静音", slider.accessibleName())
        slider.setValue(43)
        self.assertIn(("volume", "local", .43), self.provider.calls)
        self.assertFalse(any(c[0] in {"stop", "select"} for c in self.provider.calls))

    def test_volume_wheel_is_ignored(self):
        self._open()
        slider = self.panel._rows["local"].volume_slider
        event = SimpleNamespace(ignore=lambda: None)
        before = tuple(self.provider.calls)
        slider.wheelEvent(event)
        self.assertEqual(tuple(self.provider.calls), before)

    def test_small_list_opens_with_current_receiver_visible(self):
        self.owner.resize(640, 360)
        extra = tuple(AudioRouteDevice(f"device-{i}", f"设备 {i}") for i in range(5))
        self.provider.snapshot = replace(self.provider.snapshot, active_id="homepod",
                                         devices=extra + self.provider.devices)
        self._open()
        self.app.processEvents()
        row = self.panel._rows["homepod"]
        top = row.mapTo(self.panel.scroll.viewport(), row.rect().topLeft())
        bottom = row.mapTo(self.panel.scroll.viewport(), row.rect().bottomLeft())
        self.assertGreaterEqual(top.y(), 0)
        self.assertLessEqual(bottom.y(), self.panel.scroll.viewport().height())

    def test_long_group_list_opens_at_first_member_instead_of_capture_source(self):
        self.owner.resize(640, 360)
        extra = tuple(AudioRouteDevice(f"device-{index}", f"设备 {index}") for index in range(8))
        self.provider.snapshot = replace(self.provider.snapshot, active_id="local", state="casting",
            devices=extra + self.provider.devices, member_ids=("homepod", "bluetooth"),
            capture_scope="music_process_tree", cast_source_id="local")
        self._open()
        self.app.processEvents()
        self.assertEqual(self._row_order()[:2], ("bluetooth", "homepod"))
        self.assertEqual(self.panel.scroll.verticalScrollBar().value(), 0)
        row = self.panel._rows["bluetooth"]
        self.assertGreaterEqual(row.mapTo(self.panel.scroll.viewport(), row.rect().topLeft()).y(), 0)
        self.assertEqual(self.panel._rows["local"].state, "available")

    def test_material_capture_excludes_children_and_is_rate_limited(self):
        self._open()
        self.panel._appearance_elapsed = 1
        before = tuple(self.provider.calls)
        with patch.object(self.owner, "render", wraps=self.owner.render) as render:
            with patch("local.audio_output_panel.time.monotonic", return_value=500):
                self.panel._capture_material(force=True)
                self.panel._capture_material()
            self.assertEqual(render.call_count, 1)
            self.assertEqual(render.call_args.args[-1], QWidget.RenderFlag.DrawWindowBackground)
        self.assertFalse(self.panel._material.isNull())
        self.assertLessEqual(self.panel._material.width(), self.panel.width() // 2 + 1)
        self.assertEqual(tuple(self.provider.calls), before)

    def test_material_samples_the_popup_location_and_blurs_page_content(self):
        class Page(QWidget):
            def paintEvent(page, event):
                with QPainter(page) as painter:
                    painter.fillRect(page.rect(), QColor(0, 0, 255))
                    painter.fillRect(0, 0, 300, page.height(), QColor(255, 0, 0))
        page = Page()
        page.resize(600, 400)
        page.show()
        panel = AudioOutputPanel(self.provider, page)
        try:
            panel.open_at(QPoint(580, 50))
            panel._animation_timer.stop()
            panel._poll_timer.stop()
            material = panel._material
            left = material.pixelColor(8, material.height() // 2)
            right = material.pixelColor(material.width() - 8, material.height() // 2)
            self.assertGreater(left.red(), left.blue() + 10)
            self.assertGreater(right.blue(), right.red() + 10)
            # The underlying hard edge is softened before being veiled.
            edge = round((300 - panel.x()) / 2)
            near = material.pixelColor(edge, material.height() // 2)
            self.assertGreater(near.blue(), left.blue())
            self.assertGreater(near.red(), right.red())
        finally:
            panel.dismiss()
            page.close()

    def test_missing_music_backdrop_uses_one_fallback_until_geometry_or_palette_changes(self):
        self.owner._background_cache = QImage()
        self.owner._background_cache_key = None
        with patch.object(self.owner, "render", wraps=self.owner.render) as render:
            self._open()
            self.assertEqual(render.call_count, 1)
            with patch("local.audio_output_panel.time.monotonic", return_value=500):
                self.panel._capture_material()
                self.panel._capture_material()
            self.assertEqual(render.call_count, 1)
            self.panel.move(self.panel.pos() + QPoint(-10, 0))
            self.panel._capture_material()
            self.assertEqual(render.call_count, 2)

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
        self.assertGreater(self.panel._background.red(), 240)

    def test_many_devices_scroll_without_exposing_advanced_fields_or_losing_active_row(self):
        self.owner.resize(640, 360)
        extra = tuple(AudioRouteDevice(f"device-{index}", f"音频设备 {index}") for index in range(10))
        self.provider.snapshot = replace(self.provider.snapshot, devices=self.provider.devices + extra)
        self._open()
        self.app.processEvents()
        self.assertGreater(self.panel.scroll.verticalScrollBar().maximum(), 0)
        self.assertFalse(hasattr(self.panel, "advanced_widget"))
        self.assertEqual(self.panel._rows["local"].state, "active")
        self.assertLessEqual(self.panel.y() + self.panel.height(), self.owner.height() - 12)

    def test_paint_does_not_advance_motion_or_run_provider_actions(self):
        self._open()
        self.panel._opacity.setOpacity(1.0)
        before = (self.panel._appearance_elapsed, self.panel._rows["local"].elapsed, tuple(self.provider.calls))
        frame = QImage(self.panel.size(), QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(Qt.GlobalColor.transparent)
        with patch.object(self.panel, "_capture_material", side_effect=AssertionError("paint capture")):
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
