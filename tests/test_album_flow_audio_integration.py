"""Exercise the album-flow page and output chooser together without devices."""
from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from local.music_surface_window import MusicSurfaceWindow
from tests.test_audio_output_panel import _RouteProvider
from tests.test_music_fullscreen import _Owner


class AlbumFlowAudioIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _Owner()
        self.provider = _RouteProvider()
        self.owner.data_provider = self.provider
        self.owner.music_flow_intensity = 1.0
        self.owner.music_surface_reading_strategy = "gold"
        self.owner.media_track_id = "album-flow-output-integration"
        self.owner.track_title = "Album colours"
        self.owner.artist = "GluSpot test fixture"
        self.owner.media_position_seconds = 1.0
        self.owner.media_is_playing = False
        self.owner.timed_lyrics = [(0.0, "A readable lyric"), (8.0, "Next sentence")]
        self.owner.album_art = QImage(120, 120, QImage.Format.Format_RGB32)
        with QPainter(self.owner.album_art) as painter:
            for index, color in enumerate(("#cd283e", "#edbe52", "#3154b7", "#65a878")):
                painter.fillRect(index * 30, 0, 30, 120, QColor(color))
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window.show()
            self.window._timer.stop()
            self.app.processEvents()
        self.addCleanup(self._dispose)

    def _dispose(self):
        self.window.shutdown()
        self.window.deleteLater()
        self.owner.close()
        self.owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _render_page(self, now):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            self.window.render(image)
        return image

    def _background_state(self):
        return (self.window._background_cache.cacheKey(),
                self.window._background_cache_key, self.window._background_cache_at)

    def _open_with_pointer(self, fullscreen=False, now=100.0):
        if self.window._audio_output_panel is not None:
            self.window._audio_output_panel.dismiss(immediate=True)
        self.window._fullscreen = fullscreen
        self.window._reset_control_idle(now=now)
        self._render_page(now)
        button = self.window._button_rects["audio_output"]
        with patch("local.music_surface_window.time.monotonic", return_value=now + 0.5):
            QTest.mouseClick(self.window, Qt.MouseButton.LeftButton, pos=button.center().toPoint())
        panel = self.window._audio_output_panel
        self.assertIsNotNone(panel)
        self.assertTrue(panel.isVisible())
        self.assertFalse(self.window._moving)
        panel._animation_timer.stop()
        panel._poll_timer.stop()
        # Exercise the real trajectory, including its bounded row overshoot,
        # before pointer actions can belong to the displayed native rows.
        panel._clock.reset(now + .5)
        for index in range(60):
            panel._advance_animation(now + .5 + (index + 1) / 60)
        panel._animation_timer.stop()
        self.assertTrue(panel._native_content_ready())
        self.assertFalse(panel._scroll_opacity.isEnabled())
        return panel

    def _mixed_snapshot(self, pending=False):
        members = ("homepod", "homepod-2", "bluetooth")
        devices = tuple(replace(device, route_state="pending" if pending else "active",
                                volume=None if pending and device.transport == "airplay" else
                                .41 if device.id == "homepod" else .48 if device.id == "homepod-2" else device.volume)
                        if device.id in members else device for device in self.provider.devices)
        if not any(device.id == "homepod-2" for device in devices):
            devices += (AudioRouteDevice("homepod-2", "书房 HomePod mini", "homepod-mini", "airplay",
                         volume=None if pending else .48, route_state="pending" if pending else "active"),)
        self.provider.devices = devices
        self.provider.toggle_audio_output = lambda device_id: self.provider.calls.append(("toggle", device_id)) or True
        return AudioRouteSnapshot(devices, active_id="local" if pending else "homepod",
            state="connecting" if pending else "casting", member_ids=() if pending else members,
            pending_ids=members if pending else (), group_id="combined-group", capture_scope="music_process_tree",
            cast_latency_seconds=1.51, status_message="正在连接同步播放设备" if pending else "正在同步播放到 3 个设备")

    def test_chooser_capture_holds_displayed_flow_until_a_complete_page_paint(self):
        for fullscreen in (False, True):
            with self.subTest(fullscreen=fullscreen):
                self.window._fullscreen = fullscreen
                self.window._reset_control_idle(now=100.0)
                self._render_page(100.0)
                before = self._background_state()
                displayed = QImage(self.window._background_cache)
                samples = self.window._background_sample_colors(self.window.rect())
                panel = self._open_with_pointer(fullscreen)
                self.assertEqual(self._background_state(), before)
                # Both opening and polling crop the page after another ambient
                # frame is due. A new patch must not split the displayed field.
                with patch("local.audio_output_panel.time.monotonic", return_value=102.0):
                    panel._capture_material(force=True)
                    panel._capture_material()
                self.assertEqual(self._background_state(), before)
                self.assertEqual(self.window._background_cache, displayed)
                self.assertEqual(self.window._background_sample_colors(self.window.rect()), samples)
                panel.dismiss()
                self._render_page(104.0)
                self.assertNotEqual(self._background_state()[0], before[0])
                self.assertNotEqual(self.window._background_cache, displayed)

    def test_material_excludes_visible_child_and_keeps_album_colour_and_opacity(self):
        panel = self._open_with_pointer()
        marker = QWidget(self.window)
        marker.setGeometry(panel.geometry())
        marker.setAutoFillBackground(True)
        palette = marker.palette()
        palette.setColor(marker.backgroundRole(), QColor("#ff00ff"))
        marker.setPalette(palette)
        before = self._background_state()
        with patch("local.audio_output_panel.time.monotonic", return_value=102.0):
            panel._capture_material(force=True)
            without_child = QImage(panel._material)
            marker.show()
            marker.lower()
            self.assertTrue(marker.isVisible())
            # Establish that this child would visibly contaminate a capture.
            child = QImage(marker.size(), QImage.Format.Format_RGB32)
            marker.render(child)
            self.assertEqual(child.pixelColor(child.width() // 2, child.height() // 2), QColor("#ff00ff"))
            panel._capture_material(force=True)
        self.assertEqual(panel._material, without_child)
        self.assertEqual(self._background_state(), before)
        material = panel._material
        self.assertFalse(material.isNull())
        pixels = [material.pixelColor(x, y)
                  for y in range(0, material.height(), 8)
                  for x in range(0, material.width(), 8)]
        self.assertTrue(all(color.alpha() == 255 for color in pixels))
        self.assertGreater(max(max(color.getRgb()[:3]) - min(color.getRgb()[:3]) for color in pixels), 3)
        # The user's whole-scene decision supersedes independent local
        # inversion, including this panel's average crop. Check the shared
        # authority at the same instant rather than demanding a new local ink.
        with patch("local.music_surface_window.time.monotonic", return_value=102.0):
            panel._sync_surface_style()
            decided = self.window._scene_control_ink()
        self.assertEqual(panel._ink, decided)
        self.assertEqual(panel._muted, decided)
        self.assertTrue(all(row.ink == decided for row in panel._rows.values()))
        marker.hide()

    def test_displayed_backdrop_refreshes_material_without_repainting_lyrics(self):
        panel = self._open_with_pointer()
        # The helper settles the opening slide at its final crop location.
        with patch("local.audio_output_panel.time.monotonic", return_value=108.0):
            panel.sync_snapshot()
            panel._capture_material(force=True)
        before = QImage(panel._material)
        captured_at = panel._material_at
        state = self._background_state()
        with patch.object(self.window, "render", side_effect=AssertionError("extra page repaint")):
            with patch("local.audio_output_panel.time.monotonic", return_value=110.0):
                panel.sync_snapshot()
                panel._capture_material()
        self.assertEqual(panel._material, before)
        self.assertEqual(panel._material_at, captured_at)
        self.assertEqual(self._background_state(), state)
        self._render_page(112.0)
        displayed = self._background_state()
        self.assertNotEqual(displayed, state)
        with patch.object(self.window, "render", side_effect=AssertionError("extra page repaint")):
            with patch("local.audio_output_panel.time.monotonic", return_value=113.0):
                panel._capture_material()
        self.assertNotEqual(panel._material, before)
        self.assertEqual(self._background_state(), displayed)
        self.assertEqual(panel._material_at, 113.0)

    def test_circular_output_control_selects_fake_route_and_escape_dismisses_chooser(self):
        for fullscreen in (False, True):
            with self.subTest(fullscreen=fullscreen):
                self.provider.snapshot = replace(self.provider.snapshot, active_id="local")
                self.window._sync_audio_output_status()
                self.window._fullscreen = fullscreen
                self.window._reset_control_idle(now=100.0)
                closed_page = self._render_page(100.0)
                closed_controls = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
                closed_controls.fill(Qt.GlobalColor.transparent)
                with QPainter(closed_controls) as painter, patch(
                        "local.music_surface_window.time.monotonic", return_value=100.0):
                    self.window._paint_window_controls(painter, QRectF(self.window.rect()))
                panel = self._open_with_pointer(fullscreen)
                buttons = self.window._button_rects
                self.assertEqual(set(buttons), {"audio_output", "content_layout", "translation",
                                                "mode" if fullscreen else "return"})
                output, translation = buttons["audio_output"], buttons["translation"]
                self.assertAlmostEqual(output.width(), output.height())
                self.assertEqual(output.size(), translation.size())
                self.assertLess(output.right(), translation.left())
                with patch.object(self.provider, "get_audio_route_snapshot",
                                  side_effect=AssertionError("provider read during paint")):
                    open_page = self._render_page(100.5)
                    controls = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
                    controls.fill(Qt.GlobalColor.transparent)
                    with QPainter(controls) as painter, patch(
                            "local.music_surface_window.time.monotonic", return_value=100.5):
                        self.window._paint_window_controls(painter, QRectF(self.window.rect()))
                closed_face = closed_page.copy(output.toAlignedRect())
                suppressed = controls.copy(output.toAlignedRect())
                self.assertTrue(all(suppressed.pixelColor(x, y).alpha() == 0
                    for y in range(suppressed.height()) for x in range(suppressed.width())))
                # The same source slot now becomes the chooser, whose first
                # selected row owns the uniform device surface. No second blue
                # button remains above it, and adjacent controls stay drawn.
                self.assertAlmostEqual(panel.y(), output.top(), delta=1)
                self.assertAlmostEqual(panel.x() + panel.width(), output.right(), delta=1)
                row = panel._rows["local"]
                native_panel = QImage(panel.size(), QImage.Format.Format_ARGB32_Premultiplied)
                native_panel.fill(Qt.GlobalColor.transparent)
                panel.render(native_panel)
                row_face = QPoint(row.width() // 2, row.height() - 7)
                page_point = row.mapTo(self.window, row_face)
                actual = open_page.pixelColor(page_point)
                # The native panel is translucent. Compare its composition
                # over the same displayed page. The additional layout button
                # moves the chooser above artwork/lyrics at some sizes, so a
                # background-only raster is no longer the entire underlay.
                background = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
                background.fill(Qt.GlobalColor.transparent)
                with patch("local.music_surface_window.time.monotonic", return_value=100.5):
                    self.window.render(background, renderFlags=QWidget.RenderFlag.DrawWindowBackground)
                with QPainter(background) as painter:
                    painter.drawImage(panel.pos(), native_panel)
                expected = background.pixelColor(page_point)
                self.assertEqual(actual.alpha(), 255)
                self.assertLess(native_panel.pixelColor(row.mapTo(panel, row_face)).alpha(), 255)
                for channel in ("red", "green", "blue"):
                    self.assertAlmostEqual(getattr(actual, channel)(), getattr(expected, channel)(), delta=1)
                self.assertTrue(row.isChecked())
                self.assertTrue(row.volume_slider.isVisible())
                face = closed_controls.pixelColor(round(output.center().x()), round(output.top()) + 3)
                self.assertGreater(face.alpha(), 180)
                self.assertLess(face.alpha(), 245)
                self.assertGreater(sum(controls.pixelColor(x, y).alpha() > 0
                    for y in range(round(translation.top()), round(translation.bottom()))
                    for x in range(round(translation.left()), round(translation.right()))), 100)
                self.provider.calls.clear()
                QTest.mouseClick(panel._rows["bluetooth"], Qt.MouseButton.LeftButton)
                self.assertEqual(self.provider.calls, [("select", "bluetooth")])
                self.provider.snapshot = replace(self.provider.snapshot, active_id="bluetooth")
                panel.sync_snapshot()
                self.window._sync_audio_output_status()
                self.assertEqual(self.window._audio_output_kinds, ("airpods-pro",))
                self.assertEqual(panel._rows["bluetooth"].state, "active")
                self.assertEqual(panel._rows["local"].state, "available")
                with patch.object(self.window, "return_to_island") as return_to_island:
                    QTest.keyClick(panel, Qt.Key.Key_Escape)
                return_to_island.assert_not_called()
                self.assertTrue(panel.is_closing)
                panel._clock.reset(200)
                for index in range(60):
                    panel._advance_animation(200 + (index + 1) / 60)
                    if not panel.isVisible():
                        break
                self.assertFalse(panel.isVisible())
                self.assertFalse(panel._animation_timer.isActive())
                self.assertFalse(panel._poll_timer.isActive())

    def test_mixed_pending_and_confirmed_members_keep_one_displayed_background_frame(self):
        for fullscreen in (False, True):
            with self.subTest(fullscreen=fullscreen):
                self.provider.snapshot = self._mixed_snapshot(pending=True)
                panel = self._open_with_pointer(fullscreen)
                members = self.provider.snapshot.pending_ids
                for identity in members:
                    self.assertEqual(panel._rows[identity].state, "pending")
                    self.assertFalse(panel._rows[identity].isChecked())
                self.assertTrue(panel._rows["homepod-2"].volume_slider.isHidden())
                self.provider.calls.clear()
                QTest.mouseClick(panel._rows["homepod-2"], Qt.MouseButton.LeftButton)
                self.assertEqual(self.provider.calls, [("select", "homepod-2")])
                self.assertEqual(panel._rows["homepod-2"].state, "pending")
                # Pointer activation can legitimately repaint the whole page.
                # Poll/crop must retain that newly displayed complete frame.
                before = self._background_state()
                displayed = QImage(self.window._background_cache)
                self.provider.snapshot = self._mixed_snapshot()
                with patch("local.audio_output_panel.time.monotonic", return_value=102.0):
                    panel.sync_snapshot()
                    panel._capture_material(force=True)
                for identity in members:
                    self.assertTrue(panel._rows[identity].isChecked())
                    self.assertEqual(panel._rows[identity].subtitle, "同步播放")
                    self.assertFalse(panel._rows[identity].volume_slider.isHidden())
                self.assertFalse(panel._rows["local"].isChecked())
                self.assertEqual(panel.stop_button.text(), "停止同步播放")
                self.assertEqual(self._background_state(), before)
                self.assertEqual(self.window._background_cache, displayed)
                with patch.object(self.provider, "get_audio_route_snapshot",
                                  side_effect=AssertionError("provider read during combined paint")):
                    self._render_page(before[2] + 4.0)
                self.assertNotEqual(self.window._background_cache, displayed)
                panel._animation_timer.stop()
                panel._poll_timer.stop()

    def test_named_mixed_device_volume_does_not_toggle_members_or_advance_captured_flow(self):
        self.provider.snapshot = self._mixed_snapshot()
        panel = self._open_with_pointer()
        members = self.provider.snapshot.member_ids
        other_values = {identity: panel._rows[identity].volume_slider.value()
                        for identity in ("homepod", "bluetooth")}
        slider = panel._rows["homepod-2"].volume_slider
        self.provider.calls.clear()
        # The row's mouse volume/name zone deliberately toggles mute. The
        # retained native scrubber adjusts volume by keyboard/accessibility.
        slider.setFocus(Qt.FocusReason.OtherFocusReason)
        QTest.keyClick(slider, Qt.Key.Key_Right)
        self.assertEqual(len(self.provider.calls), 1)
        operation, identity, requested = self.provider.calls[0]
        self.assertEqual((operation, identity), ("volume", "homepod-2"))
        self.assertAlmostEqual(requested, slider.value() / 100)
        self.assertEqual(panel._snapshot.member_ids, members)
        self.assertEqual({identity: panel._rows[identity].volume_slider.value()
                          for identity in other_values}, other_values)
        before = self._background_state()
        self.provider.snapshot = replace(self.provider.snapshot, devices=tuple(
            replace(device, volume=requested) if device.id == "homepod-2" else device
            for device in self.provider.snapshot.devices))
        with patch("local.audio_output_panel.time.monotonic", return_value=102.0):
            panel.sync_snapshot()
            panel._capture_material(force=True)
        self.assertEqual(panel._rows["homepod-2"].device.volume, requested)
        self.assertEqual(self._background_state(), before)
        self.assertEqual(panel._snapshot.member_ids, members)
        self.assertEqual(self.provider.calls, [("volume", "homepod-2", requested)])

    def test_compact_mixed_stop_is_scroll_accessible_and_waits_for_authoritative_snapshot(self):
        self.window.resize(640, 360)
        self.provider.snapshot = self._mixed_snapshot()
        # The compact density now fits the original four rows without
        # scrolling. Retain this footer-in-a-long-list integration scenario
        # by supplying additional actual (unselected) fake endpoints.
        self.provider.snapshot = replace(self.provider.snapshot, devices=self.provider.snapshot.devices + tuple(
            AudioRouteDevice(f"extra-{index}", f"Additional output {index}", kind="speaker")
            for index in range(5)))
        panel = self._open_with_pointer()
        self.assertGreater(panel.scroll.verticalScrollBar().maximum(), 0)
        panel.scroll.ensureWidgetVisible(panel.stop_button)
        self.app.processEvents()
        stop_center = panel.stop_button.mapTo(panel.scroll.viewport(), panel.stop_button.rect().center())
        self.assertTrue(panel.scroll.viewport().rect().contains(stop_center))
        self.assertIn("同步播放", panel.title_label.text())
        self.assertIn("仅当前音乐软件", panel.scope_label.text())
        self.assertLessEqual(panel.scope_label.heightForWidth(panel.scope_label.width()),
                             panel.scope_label.height())
        self.provider.calls.clear()
        QTest.mouseClick(panel.stop_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.provider.calls, [("stop",)])
        self.assertEqual(panel._snapshot.member_ids, ("homepod", "homepod-2", "bluetooth"))
        self.assertTrue(panel.stop_button.isEnabled())
        before = self._background_state()
        self.provider.snapshot = replace(self.provider.snapshot, active_id="local", state="ready",
            member_ids=(), pending_ids=(), group_id="", capture_scope="", cast_latency_seconds=0.0,
            status_message="同步播放已停止", devices=tuple(replace(device, route_state="available")
                                                        for device in self.provider.snapshot.devices))
        with patch("local.audio_output_panel.time.monotonic", return_value=102.0):
            panel.sync_snapshot()
            panel._capture_material(force=True)
        self.assertFalse(panel.stop_button.isVisible())
        self.assertFalse(panel.stop_button.isEnabled())
        self.assertTrue(panel._rows["local"].isChecked())
        self.assertTrue(all(not panel._rows[identity].isChecked()
                            for identity in ("homepod", "homepod-2", "bluetooth")))
        self.assertEqual(panel.status_label.text(), "同步播放已停止")
        self.assertEqual(self._background_state(), before)
        self.assertTrue(self.window.rect().contains(panel.geometry()))
        self.assertEqual(self.provider.calls, [("stop",)])


if __name__ == "__main__":
    unittest.main()
