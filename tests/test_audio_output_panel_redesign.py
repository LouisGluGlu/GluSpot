"""Device chooser usability regressions through the native row/provider contract."""
from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from local.audio_output_panel import AudioOutputPanel
from local.music_surface_palette import relative_luminance


class _DelayedRouteProvider:
    """Acknowledges commands separately from publishing confirmed snapshots."""

    def __init__(self):
        self.snapshot = AudioRouteSnapshot(
            devices=(
                AudioRouteDevice("speaker", "扬声器（智能音箱 Pro-9500）", "speaker", volume=.14),
                AudioRouteDevice("headphones", "无线耳机", "airpods-pro", is_bluetooth=True, volume=.6),
                AudioRouteDevice("screen", "Display audio with a deliberately long endpoint name", "device", volume=.8),
                AudioRouteDevice("receiver", "客厅音箱", "homepod-mini", "airplay"),
            ),
            active_id="speaker", capture_scope="owned-player",
        )
        self.accept = True
        self.calls = []

    def get_audio_route_snapshot(self):
        return self.snapshot

    def refresh_audio_outputs(self):
        self.calls.append(("refresh",))

    def set_audio_output_volume(self, identity, volume):
        self.calls.append(("volume", identity, volume))
        return self.accept

    def select_audio_output(self, identity):
        self.calls.append(("solo", identity))
        return self.accept

    def toggle_audio_output(self, identity):
        self.calls.append(("join", identity))
        return self.accept

    def toggle_audio_output_mute(self, identity):
        self.calls.append(("mute", identity))
        return self.accept

    def stop_audio_cast(self):
        self.calls.append(("stop",))

    def update_device(self, identity, **fields):
        self.snapshot = replace(self.snapshot, devices=tuple(
            replace(device, **fields) if device.id == identity else device
            for device in self.snapshot.devices))


class AudioOutputPanelRedesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.owner.resize(960, 600)
        self.owner.show()
        self.provider = _DelayedRouteProvider()
        self.panel = AudioOutputPanel(self.provider, self.owner)

    def tearDown(self):
        self.panel.dismiss(immediate=True)
        self.owner.close()

    def _open(self, source_size=42):
        source = QRectF(self.owner.width() - 20 - source_size, 16, source_size, source_size)
        with patch("local.audio_output_panel.time.monotonic", return_value=100.):
            self.panel.open_at(source.bottomRight().toPoint(), source_rect=source)
        self.panel._animation_timer.stop()
        self.panel._poll_timer.stop()
        self.panel._reveal = 1.
        self.panel._appearance_elapsed = self.panel.APPEAR_SECONDS
        self.panel._apply_reveal_pose()
        for row in self.panel._rows.values():
            row.advance(1.)
        self.app.processEvents()
        self.panel._animation_timer.stop()
        self.provider.calls.clear()
        return self.panel._rows["speaker"]

    def _row_image(self, row):
        dpr = row.devicePixelRatioF()
        image = QImage(round(row.width() * dpr), round(row.height() * dpr),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(self.panel._background)
        with QPainter(image) as painter:
            row.paint_contents(painter)
        return image

    def _settle_hover(self, row):
        registration = row._control_hover_registration
        group, record = registration.group, registration.record
        with patch("local.control_hover.time.monotonic", return_value=100.):
            group._set_active(record, 100.)
        for tick in range(60):
            with patch("local.control_hover.time.monotonic", return_value=100. + (tick + 1) / 60):
                group._tick()
        group.timer.stop()
        return group, record

    @staticmethod
    def _visual_pointer_position(row, group, record, row_position):
        pose = group.visual_rect(record, 101.)
        scale = pose.width() / row.width()
        host_position = pose.topLeft() + QPointF(row_position) * scale
        return row.mapFromGlobal(group.host.mapToGlobal(host_position.toPoint()))

    @staticmethod
    def _sample_patch(image, logical_rect):
        dpr = image.devicePixelRatioF()
        return tuple(image.pixelColor(x, y).rgba()
                     for y in range(round(logical_rect.top() * dpr), round(logical_rect.bottom() * dpr))
                     for x in range(round(logical_rect.left() * dpr), round(logical_rect.right() * dpr)))

    def test_small_and_large_entry_controls_share_readable_panel_and_typography(self):
        layouts = []
        for source_size in (42, 82):
            with self.subTest(source_size=source_size):
                self.panel.dismiss(immediate=True)
                row = self._open(source_size)
                self.assertEqual(self.panel.width(), 360)
                self.assertEqual(row.height(), 72)
                self.assertEqual(row.name_font_size, 14)
                for inactive in (r for r in self.panel._rows.values() if r.state == "available"):
                    self.assertEqual(inactive.height(), 56)
                    self.assertEqual(inactive.name_font_size, 14)
                layouts.append((self.panel.width(), row.height(), row.name_font_size,
                                row.volume_slider.geometry()))
        self.assertEqual(layouts[0], layouts[1])

    def test_narrow_viewport_clamps_menu_without_clipping_its_source_or_actions(self):
        self.owner.resize(240, 260)
        row = self._open(82)
        self.assertLessEqual(self.panel.width(), self.owner.width() - 24)
        self.assertGreaterEqual(self.panel.width(), 82)
        self.assertTrue(self.owner.rect().contains(self.panel.geometry()))
        self.assertLessEqual(self.panel.geometry().bottom(), self.owner.height() - 12)
        source = self.panel._source_rect.translated(-self.panel.pos())
        self.assertTrue(QRectF(self.panel.rect()).contains(source))
        self.assertTrue(row.rect().contains(row.volume_slider.geometry()))
        for action in (row.solo_button, self.panel._rows["headphones"].join_button):
            self.assertTrue(action.parentWidget().rect().contains(action.geometry()))
            self.assertGreaterEqual(action.width(), 32)

    def test_selected_hover_uses_small_continuous_font_change_only(self):
        row = self._open(82)
        row.set_details_visible(True)
        sizes = [row.name_font_size]
        for elapsed in (.02, .02, .04, .08, .16, .32):
            row.advance(elapsed)
            sizes.append(row.name_font_size)
        self.assertGreater(sizes[1], 12)
        self.assertLess(sizes[1], sizes[0])
        self.assertTrue(all(12 <= size <= 14 for size in sizes))
        self.assertTrue(all(a >= b for a, b in zip(sizes, sizes[1:])))
        self.assertAlmostEqual(sizes[-1], 12, places=2)
        inactive = self.panel._rows["headphones"]
        inactive.set_details_visible(True)
        inactive.advance(1.)
        self.assertFalse(inactive.details_visible)
        self.assertEqual(inactive.name_font_size, 14)

    def test_track_endpoints_align_with_text_and_dispatch_only_volume(self):
        row = self._open()
        with patch.object(row, "_paint_name") as painted_name:
            self._row_image(row)
        name_rect = painted_name.call_args.args[1]
        slider = row.volume_slider
        self.assertEqual(slider.x() + 8, name_rect.left())
        self.assertEqual(slider.x() + slider.width() - 8, row.width() - 14)
        self.assertFalse(slider.geometry().intersects(row.solo_button.geometry()))
        # Send events to the visible row, not directly to the slider: native
        # child routing must agree with the track that the user sees.
        for x, expected in ((name_rect.left(), 0.), (row.width() - 14, 1.)):
            self.provider.calls.clear()
            QTest.mouseClick(row, Qt.MouseButton.LeftButton,
                             pos=QPoint(round(x), slider.geometry().center().y()))
            self.assertTrue(self.provider.calls)
            self.assertTrue(all(call[0] == "volume" for call in self.provider.calls))
            self.assertEqual(self.provider.calls[-1], ("volume", "speaker", expected))

    def test_short_confirmed_percentage_leaves_room_for_device_name(self):
        name = "AirPods Pro - Wireless"
        self.provider.update_device("speaker", name=name, volume=.58)
        row = self._open()
        with patch.object(row, "_paint_name") as painted_name:
            self._row_image(row)
        self.assertEqual(painted_name.call_args.args[2], name)
        self.assertEqual(row.volume_text, "58%")

    def test_pending_volume_is_labelled_target_without_fabricating_actual_readback(self):
        row = self._open()
        self.assertEqual(row.volume_text, "14%")
        with patch("local.audio_output_panel.time.monotonic", return_value=100.):
            row.volume_slider.setValue(78)
            self.panel.sync_snapshot()
        self.assertEqual(self.provider.calls, [("volume", "speaker", .78)])
        self.assertTrue(row.volume_pending)
        self.assertEqual(row.volume_text, "目标 78%")
        self.assertEqual(row.displayed_volume, 14)
        self.assertEqual(row.volume_slider.value(), 78)
        self.assertEqual(row.device.volume, .14)
        self.assertEqual(row.state, "active")

    def test_matching_readback_removes_target_and_external_volume_updates_follow(self):
        row = self._open()
        with patch("local.audio_output_panel.time.monotonic", return_value=100.):
            row.volume_slider.setValue(78)
        self.provider.update_device("speaker", volume=.78)
        with patch("local.audio_output_panel.time.monotonic", return_value=100.5):
            self.panel.sync_snapshot()
        self.assertFalse(row.volume_pending)
        self.assertEqual(row.volume_text, "78%")
        self.provider.update_device("speaker", volume=.24)
        self.panel.sync_snapshot()
        self.assertEqual(row.volume_text, "24%")
        self.assertEqual(row.volume_slider.value(), 24)
        self.assertEqual(self.provider.calls, [("volume", "speaker", .78)])

    def test_rejection_rolls_back_to_confirmed_value_and_preserves_route(self):
        row = self._open()
        before = self.provider.snapshot
        self.provider.accept = False
        row.volume_slider.setValue(78)
        self.assertFalse(row.volume_pending)
        self.assertEqual(row.volume_text, "14%")
        self.assertEqual(row.volume_slider.value(), 14)
        self.assertEqual(self.provider.snapshot, before)
        self.assertTrue(row.isChecked())
        self.assertEqual(row.volume_feedback, "未确认")
        self.assertIn("音量", self.panel.status_label.text())
        self.assertFalse(self.panel.status_label.isHidden())

    def test_unchanged_snapshot_timeout_retires_target_without_reissuing_commands(self):
        row = self._open()
        with patch("local.audio_output_panel.time.monotonic", return_value=100.):
            row.volume_slider.setValue(78)
        with patch("local.audio_output_panel.time.monotonic", return_value=102.1):
            self.panel.sync_snapshot()
        self.assertFalse(row.volume_pending)
        self.assertEqual(row.volume_text, "14%")
        self.assertEqual(row.volume_slider.value(), 14)
        self.assertTrue(row.isChecked())
        self.assertEqual(row.volume_feedback, "未确认")
        self.assertEqual(self.provider.calls, [("volume", "speaker", .78)])

    def test_muted_raw_target_is_pending_until_an_unmuted_readback(self):
        self.provider.update_device("speaker", volume=.5, volume_muted=True)
        row = self._open()
        self.assertEqual(row.volume_text, "0%")
        with patch("local.audio_output_panel.time.monotonic", return_value=100.):
            row.volume_slider.setValue(50)
            self.panel.sync_snapshot()
        self.assertTrue(row.volume_pending)
        self.assertEqual(row.volume_text, "目标 50%")
        self.assertEqual(row.displayed_volume, 0)
        self.provider.update_device("speaker", volume_muted=False)
        with patch("local.audio_output_panel.time.monotonic", return_value=100.4):
            self.panel.sync_snapshot()
        self.assertFalse(row.volume_pending)
        self.assertEqual(row.volume_text, "50%")
        self.assertEqual(self.provider.calls, [("volume", "speaker", .5)])

    def test_unknown_volume_has_no_target_or_fabricated_percentage(self):
        self.provider.update_device("speaker", volume=None)
        row = self._open()
        self.assertEqual(row.volume_text, "—")
        self.assertFalse(row.volume_pending)
        self.assertFalse(row.volume_slider.isEnabled())
        self.assertTrue(row.volume_slider.isHidden())
        self.assertIsNone(row.displayed_volume)
        row.click()
        self.assertEqual(self.provider.calls, [("mute", "speaker")])

    def test_volume_changes_are_confined_to_slider_and_value_not_row_pigment(self):
        row = self._open()
        patches = (QRectF(27, 7, 8, 6), QRectF(row.width() / 2 - 8, row.height() - 9, 16, 4))
        references = None
        for percent in (0, 14, 78, 100):
            with self.subTest(percent=percent):
                self.provider.update_device("speaker", volume=percent / 100.)
                self.panel.sync_snapshot()
                row.progress = 1.
                frame = self._row_image(row)
                samples = tuple(self._sample_patch(frame, rect) for rect in patches)
                self.assertTrue(all(samples))
                if references is None:
                    references = samples
                else:
                    self.assertEqual(samples, references)

    def test_muting_preserves_selection_face_and_shows_zero_percent(self):
        row = self._open()
        row.progress = 1.
        patch_rect = QRectF(row.width() / 2 - 8, row.height() - 9, 16, 4)
        before = self._sample_patch(self._row_image(row), patch_rect)
        self.provider.update_device("speaker", volume_muted=True)
        self.panel.sync_snapshot()
        self.assertEqual(row.volume_text, "0%")
        self.assertTrue(row.isChecked())
        self.assertEqual(row.state, "active")
        self.assertEqual(self._sample_patch(self._row_image(row), patch_rect), before)

    def test_action_keyboard_targets_keep_solo_join_and_mute_separate(self):
        row = self._open()
        inactive = self.panel._rows["headphones"]
        for action in (row.solo_button, inactive.join_button):
            self.assertEqual(action.focusPolicy(), Qt.FocusPolicy.StrongFocus)
            self.assertTrue(action.accessibleName())
            self.assertTrue(action.toolTip())
        QTest.keyClick(row, Qt.Key.Key_Space)
        QTest.keyClick(row.solo_button, Qt.Key.Key_Space)
        QTest.keyClick(inactive, Qt.Key.Key_Space)
        QTest.keyClick(inactive.join_button, Qt.Key.Key_Space)
        self.assertEqual(self.provider.calls, [
            ("mute", "speaker"), ("solo", "speaker"),
            ("solo", "headphones"), ("join", "headphones"),
        ])
        self.assertTrue(row.isChecked())
        self.assertFalse(inactive.isChecked())

    def test_owned_windows_group_shows_sync_and_exposes_stop(self):
        self.provider.snapshot = replace(self.provider.snapshot, state="casting",
            member_ids=("speaker", "headphones"), group_id="owned-renderer-epoch")
        self._open()
        self.assertIn("同步", self.panel.title_label.text())
        self.assertIn("同步", self.panel._rows["speaker"].subtitle)
        self.assertFalse(self.panel.stop_button.isHidden())
        self.assertTrue(self.panel.stop_button.isEnabled())
        self.panel.stop_button.click()
        self.assertEqual(self.provider.calls, [("stop",)])
        self.assertEqual(self.provider.snapshot.group_id, "owned-renderer-epoch")

    def test_one_remaining_owned_renderer_keeps_its_group_stop_action(self):
        self.provider.snapshot = replace(self.provider.snapshot, state="casting",
            member_ids=("speaker",), group_id="owned-renderer-epoch")
        self._open()
        self.assertFalse(self.panel.stop_button.isHidden())
        self.assertTrue(self.panel.stop_button.isEnabled())
        self.panel.stop_button.click()
        self.assertEqual(self.provider.calls, [("stop",)])

    def test_failed_member_retains_confirmed_membership_and_explains_failure(self):
        self.provider.snapshot = replace(self.provider.snapshot, state="casting",
            member_ids=("speaker", "headphones"), group_id="owned-renderer-epoch")
        self.provider.update_device("headphones", route_state="error", route_message="设备播放失败")
        self._open()
        row = self.panel._rows["headphones"]
        self.assertTrue(row.isChecked())
        self.assertEqual(row.state, "active")
        self.assertIn("失败", row.subtitle)
        self.assertIn("设备播放失败", row.toolTip())
        self.assertEqual(self.provider.calls, [])

    def test_source_or_renderer_owner_change_cancels_old_pending_drag(self):
        for field, target in (("music_source_app_id", "another-player"),
                              ("capture_scope", "music_process_tree"),
                              ("group_id", "another-renderer-epoch")):
            with self.subTest(field=field):
                self.panel.dismiss(immediate=True)
                self.provider.snapshot = replace(self.provider.snapshot, state="casting",
                    capture_scope="owned-player", music_source_app_id="player-one",
                    member_ids=("speaker", "headphones"), group_id="original-renderer-epoch")
                row = self._open()
                with patch("local.audio_output_panel.time.monotonic", return_value=100.):
                    row.volume_slider.setSliderDown(True)
                    row.volume_slider.setValue(78)
                self.provider.snapshot = replace(self.provider.snapshot, **{field: target})
                with patch("local.audio_output_panel.time.monotonic", return_value=100.2):
                    self.panel.sync_snapshot()
                self.assertFalse(row.volume_slider.isSliderDown())
                self.assertFalse(row.volume_pending)
                self.assertEqual(row.volume_slider.value(), 14)
                self.assertEqual(self.provider.calls, [("volume", "speaker", .78)])

    def test_owner_change_discards_real_held_slider_mute_solo_and_join_releases(self):
        for field, target in (("music_source_app_id", "another-player"),
                              ("capture_scope", "music_process_tree"),
                              ("group_id", "another-renderer-epoch")):
            for action in ("volume", "mute", "solo", "join"):
                with self.subTest(owner_field=field, held_action=action):
                    self.panel.dismiss(immediate=True)
                    self.provider.snapshot = replace(self.provider.snapshot, state="casting",
                        capture_scope="owned-player", music_source_app_id="original-player",
                        member_ids=("speaker", "headphones"), group_id="original-renderer-epoch")
                    selected = self._open()
                    row = self.panel._rows["screen"] if action == "join" else selected
                    child = (row.volume_slider if action == "volume" else
                             row.solo_button if action == "solo" else
                             row.join_button if action == "join" else row)
                    group, record = self._settle_hover(row)
                    row_position = (child.geometry().center() if child is not row else
                                    QPoint(row.width() // 2, 16))
                    press_position = self._visual_pointer_position(row, group, record, row_position)
                    with patch("local.control_hover.time.monotonic", return_value=101.), \
                         patch("local.audio_output_panel.time.monotonic", return_value=101.):
                        QTest.mousePress(row, Qt.MouseButton.LeftButton, pos=press_position)
                    self.assertIs(group.pressed, record)
                    self.assertIs(group.pressed_widget(), child)
                    self.assertIsNotNone(group.pressed_visual)
                    if action == "volume":
                        self.assertTrue(child.isSliderDown())
                        self.assertTrue(row.volume_pending)
                    else:
                        self.assertTrue(child.isDown())
                    calls_at_owner_change = tuple(self.provider.calls)
                    self.provider.snapshot = replace(self.provider.snapshot, **{field: target})
                    with patch("local.audio_output_panel.time.monotonic", return_value=101.2):
                        self.panel.sync_snapshot()
                    self.assertIsNone(group.pressed)
                    self.assertIsNone(group.pressed_widget)
                    self.assertIsNone(group.pressed_visual)
                    for item in self.panel._rows.values():
                        self.assertFalse(item.isDown())
                        self.assertFalse(item.solo_button.isDown())
                        self.assertFalse(item.join_button.isDown())
                        self.assertFalse(item.volume_slider.isSliderDown())
                        self.assertFalse(item.volume_pending)
                    with patch("local.control_hover.time.monotonic", return_value=101.3), \
                         patch("local.audio_output_panel.time.monotonic", return_value=101.3):
                        QTest.mouseMove(row, press_position)
                        QTest.mouseRelease(row, Qt.MouseButton.LeftButton, pos=press_position)
                    self.assertEqual(tuple(self.provider.calls), calls_at_owner_change)
                    self.assertIsNone(group.pressed)
                    self.assertIsNone(group.pressed_widget)
                    self.assertIsNone(group.pressed_visual)

    def test_same_epoch_member_addition_keeps_real_pointer_drag_and_releases_normally(self):
        self.provider.snapshot = replace(self.provider.snapshot, state="casting",
            capture_scope="owned-player", music_source_app_id="player-one",
            member_ids=("speaker", "headphones"), group_id="same-renderer-epoch")
        row = self._open()
        group, record = self._settle_hover(row)
        press_position = self._visual_pointer_position(
            row, group, record, row.volume_slider.geometry().center())
        with patch("local.control_hover.time.monotonic", return_value=101.), \
             patch("local.audio_output_panel.time.monotonic", return_value=101.):
            QTest.mousePress(row, Qt.MouseButton.LeftButton, pos=press_position)
        held_pose = QRectF(group.pressed_visual)
        self.assertIs(group.pressed, record)
        self.assertIs(group.pressed_widget(), row.volume_slider)
        self.assertTrue(row.volume_slider.isSliderDown())
        self.provider.snapshot = replace(self.provider.snapshot,
            member_ids=("speaker", "headphones", "screen"))
        with patch("local.audio_output_panel.time.monotonic", return_value=101.2):
            self.panel.sync_snapshot()
        self.assertIs(group.pressed, record)
        self.assertIs(group.pressed_widget(), row.volume_slider)
        self.assertEqual(group.pressed_visual, held_pose)
        self.assertTrue(row.volume_slider.isSliderDown())
        destination = row.volume_slider.pos() + QPoint(row.volume_slider.width() - 8, 13)
        release_position = self._visual_pointer_position(row, group, record, destination)
        with patch("local.control_hover.time.monotonic", return_value=101.3), \
             patch("local.audio_output_panel.time.monotonic", return_value=101.3):
            QTest.mouseMove(row, release_position)
            QTest.mouseRelease(row, Qt.MouseButton.LeftButton, pos=release_position)
        self.assertEqual(row.volume_slider.value(), 100)
        self.assertFalse(row.volume_slider.isSliderDown())
        self.assertIsNone(group.pressed)
        self.assertIsNone(group.pressed_widget)
        self.assertIsNone(group.pressed_visual)
        self.assertTrue(self.provider.calls)
        self.assertTrue(all(call[0] == "volume" and call[1] == "speaker"
                            for call in self.provider.calls))

    def test_unpolled_owner_change_blocks_old_gesture_then_allows_a_fresh_action(self):
        for field, target in (("music_source_app_id", "another-player"),
                              ("capture_scope", "music_process_tree"),
                              ("group_id", "another-renderer-epoch")):
            for action in ("volume", "mute", "solo", "join", "stop"):
                with self.subTest(owner_field=field, action=action):
                    self.panel.dismiss(immediate=True)
                    self.provider.snapshot = replace(self.provider.snapshot, state="casting",
                        capture_scope="owned-player", music_source_app_id="original-player",
                        member_ids=("speaker", "headphones"), group_id="original-renderer-epoch")
                    selected = self._open()
                    row = self.panel._rows["screen"] if action == "join" else selected
                    group, record = self._settle_hover(row)
                    if action == "stop":
                        destination = self.panel.stop_button
                        press_position = destination.rect().center()
                    else:
                        destination = row
                        child = (row.volume_slider if action == "volume" else
                                 row.solo_button if action == "solo" else
                                 row.join_button if action == "join" else row)
                        row_position = (child.geometry().center() if child is not row else
                                        QPoint(row.width() // 2, 16))
                        press_position = self._visual_pointer_position(row, group, record, row_position)
                    with patch("local.control_hover.time.monotonic", return_value=101.), \
                         patch("local.audio_output_panel.time.monotonic", return_value=101.):
                        QTest.mousePress(destination, Qt.MouseButton.LeftButton, pos=press_position)
                    if action != "stop":
                        self.assertIs(group.pressed, record)
                    calls_before_owner_change = tuple(self.provider.calls)
                    self.provider.snapshot = replace(self.provider.snapshot, **{field: target})
                    # Deliberately do not poll. The action itself must notice
                    # the newly published owner before sending its command.
                    with patch("local.control_hover.time.monotonic", return_value=101.2), \
                         patch("local.audio_output_panel.time.monotonic", return_value=101.2):
                        if action == "volume":
                            end = row.volume_slider.pos() + QPoint(row.volume_slider.width() - 8, 13)
                            release_position = self._visual_pointer_position(row, group, record, end)
                            QTest.mouseMove(destination, release_position)
                        else:
                            release_position = press_position
                        QTest.mouseRelease(destination, Qt.MouseButton.LeftButton, pos=release_position)
                    self.assertEqual(tuple(self.provider.calls), calls_before_owner_change)
                    self.assertEqual(self.panel._snapshot, self.provider.snapshot)
                    self.assertIsNone(group.pressed)
                    self.assertIsNone(group.pressed_widget)
                    self.assertIsNone(group.pressed_visual)
                    self.assertFalse(row.volume_slider.isSliderDown())
                    self.assertFalse(row.volume_pending)
                    for item in self.panel._rows.values():
                        self.assertFalse(item.isDown())
                        self.assertFalse(item.solo_button.isDown())
                        self.assertFalse(item.join_button.isDown())
                    self.provider.calls.clear()
                    group, record = self._settle_hover(row)
                    if action == "volume":
                        start = row.volume_slider.pos() + QPoint(8, 13)
                        end = row.volume_slider.pos() + QPoint(row.volume_slider.width() - 8, 13)
                        start_position = self._visual_pointer_position(row, group, record, start)
                        end_position = self._visual_pointer_position(row, group, record, end)
                        with patch("local.control_hover.time.monotonic", return_value=101.), \
                             patch("local.audio_output_panel.time.monotonic", return_value=101.):
                            QTest.mousePress(row, Qt.MouseButton.LeftButton, pos=start_position)
                            QTest.mouseMove(row, end_position)
                            QTest.mouseRelease(row, Qt.MouseButton.LeftButton, pos=end_position)
                        self.assertEqual(row.volume_slider.value(), 100)
                        self.assertTrue(self.provider.calls)
                        self.assertTrue(all(call[0] == "volume" and call[1] == row.device.id
                                            for call in self.provider.calls))
                    else:
                        if action == "stop":
                            fresh_destination = self.panel.stop_button
                            fresh_position = fresh_destination.rect().center()
                        else:
                            fresh_destination = row
                            fresh_position = self._visual_pointer_position(row, group, record, row_position)
                        with patch("local.control_hover.time.monotonic", return_value=101.), \
                             patch("local.audio_output_panel.time.monotonic", return_value=101.):
                            QTest.mouseClick(fresh_destination, Qt.MouseButton.LeftButton, pos=fresh_position)
                        expected = ("stop",) if action == "stop" else (action, row.device.id)
                        self.assertEqual(self.provider.calls, [expected])

    def test_healthy_same_epoch_member_addition_preserves_pending_drag(self):
        self.provider.snapshot = replace(self.provider.snapshot, state="casting",
            capture_scope="owned-player", music_source_app_id="player-one",
            member_ids=("speaker", "headphones"), group_id="same-renderer-epoch")
        row = self._open()
        with patch("local.audio_output_panel.time.monotonic", return_value=100.):
            row.volume_slider.setSliderDown(True)
            row.volume_slider.setValue(78)
        self.provider.snapshot = replace(self.provider.snapshot,
            member_ids=("speaker", "headphones", "screen"))
        with patch("local.audio_output_panel.time.monotonic", return_value=100.2):
            self.panel.sync_snapshot()
        self.assertTrue(row.volume_slider.isSliderDown())
        self.assertTrue(row.volume_pending)
        self.assertEqual(row.volume_text, "目标 78%")
        self.assertEqual(row.volume_slider.value(), 78)
        self.assertEqual(self.provider.calls, [("volume", "speaker", .78)])

    def test_polled_owner_change_discards_held_stop_move_and_release_before_fresh_click(self):
        self.provider.snapshot = replace(self.provider.snapshot, state="casting",
            capture_scope="owned-player", music_source_app_id="original-player",
            member_ids=("speaker", "headphones"), group_id="original-renderer-epoch")
        self._open()
        button = self.panel.stop_button
        position = button.rect().center()
        with patch("local.control_hover.time.monotonic", return_value=101.), \
             patch("local.audio_output_panel.time.monotonic", return_value=101.):
            QTest.mousePress(button, Qt.MouseButton.LeftButton, pos=position)
        self.assertTrue(button.isDown())
        self.assertEqual(self.provider.calls, [])
        self.provider.snapshot = replace(self.provider.snapshot,
            music_source_app_id="another-player", group_id="another-renderer-epoch")
        with patch("local.audio_output_panel.time.monotonic", return_value=101.2):
            self.panel.sync_snapshot()
        self.assertFalse(button.isDown())
        self.assertTrue(self.panel._discard_pointer_until_release)
        held_move = QMouseEvent(QEvent.Type.MouseMove, QPointF(position),
            QPointF(button.mapToGlobal(position)), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        with patch("local.control_hover.time.monotonic", return_value=101.3), \
             patch("local.audio_output_panel.time.monotonic", return_value=101.3):
            self.app.sendEvent(button, held_move)
            self.assertFalse(button.isDown())
            self.assertTrue(self.panel._discard_pointer_until_release)
            QTest.mouseRelease(button, Qt.MouseButton.LeftButton, pos=position)
        self.assertEqual(self.provider.calls, [])
        self.assertFalse(button.isDown())
        self.assertFalse(self.panel._discard_pointer_until_release)
        with patch("local.control_hover.time.monotonic", return_value=101.4), \
             patch("local.audio_output_panel.time.monotonic", return_value=101.4):
            QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=position)
        self.assertEqual(self.provider.calls, [("stop",)])

    def test_readable_name_ink_on_light_and_dark_selected_faces(self):
        row = self._open()
        for background in (QColor(20, 20, 24), QColor(245, 245, 247)):
            with self.subTest(background=background.name()):
                self.owner._background_sample_colors = lambda rect, color=background: (color,)
                self.panel.sync_snapshot()
                row.progress = 1.
                frame = self._row_image(row)
                dpr = frame.devicePixelRatioF()
                face = frame.pixelColor(round(row.width() / 2 * dpr), round((row.height() - 7) * dpr))
                a, b = sorted((relative_luminance(row.ink), relative_luminance(face)))
                self.assertGreaterEqual((b + .05) / (a + .05), 4.5)


if __name__ == "__main__":
    unittest.main()
