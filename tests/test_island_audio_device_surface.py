"""Native Qt regressions for the device notice inside the existing V2 island."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from api.providers import EmptyIslandDataProvider, empty_visual_data
from local.island_audio_device_motion import IslandDevicePayload
from local.island_stack_visual import IslandKind, IslandStackVisual, Message, SpringRect, StackVisual


PAYLOAD = IslandDevicePayload("buds", "我的 AirPods", "airpods", 70)


class IslandAudioDeviceSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.visual = IslandStackVisual(data_provider=EmptyIslandDataProvider(empty_visual_data()))
        self.visual.timer.stop()
        self.visual.location_mode = "free"
        self.visual.finder_mode = False
        self.visual.expanded = False
        self.visual.music_ambience_style = "pure_black"
        self.visual.lyric_flow_style = "wave_stream"
        self.visual.lyrics = ["一直继续播放的歌词", "下一行歌词"]
        self.visual.timed_lyrics = [(0.0, self.visual.lyrics[0]), (20.0, self.visual.lyrics[1])]
        self.visual.lyric_index = 0
        self.visual.prev_lyric_index = 0
        self.visual.track_title = "Fixture song"
        self.visual.artist = "Fixture artist"
        self.visual.media_track_id = "fixture-song"
        self.visual.media_position_seconds = 7.0
        self.visual.media_raw_position_seconds = 7.0
        self.visual.media_position_sync_at = 10.0
        self.visual.media_position_sampled_at = 10.0
        self.visual.media_is_playing = False
        self.visual.lyric_changed_at = 9.0
        self.visual.music_surface_transition_mode = ""
        self.visual.music_surface_transition_started_at = 0.0
        self.visual._island_button_ripples = ()
        self.now = 10.0

    def tearDown(self):
        self.visual.music_fullscreen_overlay = None
        self.visual.close()
        self.visual.deleteLater()
        self.app.processEvents()

    def _advance_to(self, target):
        while self.now < target:
            self.now = min(target, self.now + 1.0 / 120.0)
            self.visual._audio_device_motion.advance(self.now)

    def _notice(self, payload=PAYLOAD):
        self.visual._audio_device_motion.announce(payload, self.now)
        self._advance_to(self.now + 0.40)
        self.assertEqual(self.visual._audio_device_pose().reveal, 1.0)

    def _image(self, method="_paint_music", rect=None, expanded=False):
        shell = QRectF(12.0, 10.0, 390.0, 50.0) if rect is None else QRectF(rect)
        image = QImage(max(430, int(shell.right() + 12)), max(90, int(shell.bottom() + 12)), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            with patch("local.island_stack_visual.time.monotonic", return_value=self.now):
                if method == "_paint_music":
                    self.visual._paint_music(painter, shell, expanded)
                elif method == "_paint_music_compact":
                    self.visual._paint_music_compact(painter, shell)
                else:
                    self.visual._paint_audio_device_idle_island(painter, shell, method)
        finally:
            painter.end()
        return image

    def _show(self):
        with patch.object(self.visual, "_center_on_screen"), patch.object(self.visual, "_force_super_topmost"):
            self.visual.show()
            self.app.processEvents()

    def _route(self, serial=1):
        return AudioRouteSnapshot(event_serial=serial, event_name="我的 AirPods", event_device_id="buds")

    def _clock_state(self):
        return (tuple(self.visual.lyrics), tuple(self.visual.timed_lyrics), self.visual.lyric_index,
                self.visual.prev_lyric_index, self.visual.media_position_seconds,
                self.visual.media_raw_position_seconds, self.visual.media_position_sync_at,
                self.visual.media_position_sampled_at, self.visual.media_is_playing,
                self.visual.media_track_id)

    def test_battery_notice_keeps_real_live_lyric_viewport_and_media_clock(self):
        self._notice()
        before = self._clock_state()
        calls = []
        viewport = self.visual._paint_compact_lyric_viewport

        def paint_live(painter, rect):
            calls.append((QRectF(rect), painter.opacity()))
            viewport(painter, rect)

        with patch.object(self.visual, "_paint_compact_lyric_viewport", side_effect=paint_live):
            image = self._image()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 1.0)
        self.assertGreater(calls[0][0].width(), 100.0)
        self.assertLess(calls[0][0].width(), 390.0)
        self.assertEqual(self._clock_state(), before)
        self.assertFalse(image.isNull())

    def test_unknown_battery_uses_device_name_and_does_not_paint_lyrics(self):
        self._notice(replace(PAYLOAD, battery_percent=None))
        before = self._clock_state()
        live = Mock()
        with patch.object(self.visual, "_paint_compact_lyric_viewport", live):
            image = self._image()
        live.assert_not_called()
        self.assertEqual(self._clock_state(), before)
        # The central name visibly differs from the empty background raster.
        backdrop = self.visual._audio_device_background_image
        differences = 0
        for y in range(18, 52):
            for x in range(90, 310):
                pixel = image.pixelColor(x, y)
                base = backdrop.pixelColor(x - 12, y - 10)
                differences += int(sum(abs(a - b) for a, b in zip(pixel.getRgb()[:3], base.getRgb()[:3])) > 40)
        self.assertGreater(differences, 80)

    def test_name_is_centered_bold_and_device_glyph_is_larger(self):
        self._notice(replace(PAYLOAD, battery_percent=None))
        painter = Mock()
        painter.opacity.return_value = 1.0
        shell = QRectF(12, 10, 390, 50)
        with patch("local.island_stack_visual.paint_audio_output_icon") as glyph:
            self.visual._paint_audio_device_content(painter, shell, self.visual._audio_device_pose(), keep_lyrics=True)
        bounds, alignment, _ = painter.drawText.call_args.args
        self.assertAlmostEqual(bounds.center().x(), shell.center().x())
        self.assertEqual(alignment, Qt.AlignmentFlag.AlignCenter)
        self.assertEqual(painter.setFont.call_args.args[0].weight(), QFont.Weight.Bold)
        self.assertGreater(glyph.call_args.args[1].height(), self.visual._u(31))

    def test_battery_ring_is_thicker_without_exceeding_shell(self):
        self._notice()
        painter = Mock()
        painter.opacity.return_value = 1.0
        shell = QRectF(12, 10, 390, 50)
        with patch("local.island_stack_visual.paint_audio_output_icon"), patch.object(self.visual, "_paint_audio_device_faded_layer"):
            self.visual._paint_audio_device_content(painter, shell, self.visual._audio_device_pose(), keep_lyrics=True)
        self.assertGreater(painter.setPen.call_args.args[0].widthF(), 3.0)
        self.assertTrue(shell.contains(painter.drawEllipse.call_args.args[0]))

    def test_empty_battery_is_a_valid_ring_and_keeps_lyrics(self):
        self._notice(replace(PAYLOAD, battery_percent=0))
        live = Mock()
        with patch.object(self.visual, "_paint_compact_lyric_viewport", live):
            self._image()
        live.assert_called_once()

    def test_five_second_expiry_restores_original_compact_painter(self):
        self._notice(replace(PAYLOAD, battery_percent=None))
        self._advance_to(15.10)
        self.assertGreater(self.visual._audio_device_pose().reveal, 0.0)
        self.assertLess(self.visual._audio_device_pose().reveal, 1.0)
        self._advance_to(15.5)
        self.assertIsNone(self.visual._audio_device_pose())
        base = Mock()
        device = Mock()
        with patch.object(self.visual, "_paint_music_compact_base", base), patch.object(self.visual, "_paint_audio_device_content", device):
            self._image("_paint_music_compact")
        base.assert_called_once()
        device.assert_not_called()

    def test_actual_pure_white_and_black_backgrounds_choose_opposite_ink(self):
        self._notice(replace(PAYLOAD, battery_percent=None))
        region = QRectF(34.0, 26.0, 26.0, 20.0)
        for style, light_ink in (("pure_white", False), ("pure_black", True)):
            with self.subTest(style=style):
                self.visual.music_ambience_style = style
                self._image()
                ink = self.visual._audio_device_ink(region)
                self.assertEqual(ink.lightness() > 128, light_ink)

    def test_white_bridge_ink_uses_rendered_background_not_dark_style_name(self):
        self._notice(replace(PAYLOAD, battery_percent=None))
        self.visual.music_ambience_style = "pure_black"
        self.visual.music_surface_transition_mode = "white_bridge"
        self.visual.music_surface_transition_started_at = self.now - 0.3
        self.visual.music_surface_transition_duration = 0.62
        self.visual.music_surface_transition_from = (QColor(245, 247, 250), QColor(228, 233, 241))
        self.visual.music_surface_transition_to = (QColor(245, 247, 250), QColor(228, 233, 241))
        self.assertGreater(self.visual._music_control_icon_color().lightness(), 128)
        self._image()
        self.assertLess(self.visual._audio_device_ink(QRectF(120, 25, 100, 20)).lightness(), 128)

    def test_background_ink_sampling_never_reads_desktop_or_provider(self):
        self._notice()
        # Existing live lyrics read the cached route for display compensation.
        # Isolate the new device/background path from that established reader.
        with patch.object(self.visual, "_paint_compact_lyric_viewport"), patch.object(self.visual, "_sample_background_luma", side_effect=AssertionError("Desktop sampling forbidden")), patch.object(self.visual.data_provider, "get_audio_route_snapshot", side_effect=AssertionError("Provider reads forbidden"), create=True):
            self._image()
            self.visual._audio_device_ink(QRectF(30, 20, 30, 20))

    def test_notice_fade_attenuates_base_renderer_that_owns_its_opacity(self):
        self.visual._audio_device_motion.announce(replace(PAYLOAD, battery_percent=None), self.now)
        self._advance_to(10.16)
        reveal = self.visual._audio_device_pose().reveal

        def absolute_base(painter, _rect):
            painter.setOpacity(1.0)
            painter.fillRect(QRectF(170.0, 20.0, 30.0, 20.0), QColor("red"))

        with patch.object(self.visual, "_paint_music_compact_base", side_effect=absolute_base), patch.object(self.visual, "_paint_audio_device_content"):
            image = self._image("_paint_music_compact")
        self.assertAlmostEqual(image.pixelColor(180, 30).alphaF(), 1.0 - reveal, delta=0.015)

    def test_notice_fade_attenuates_live_renderer_that_owns_its_opacity(self):
        self.visual._audio_device_motion.announce(PAYLOAD, self.now)
        self._advance_to(10.16)
        reveal = self.visual._audio_device_pose().reveal

        def absolute_live(painter, _rect):
            painter.setOpacity(1.0)
            painter.fillRect(QRectF(170.0, 20.0, 30.0, 20.0), QColor("cyan"))

        with patch.object(self.visual, "_paint_music_compact_base"), patch.object(self.visual, "_paint_compact_lyric_viewport", side_effect=absolute_live):
            image = self._image("_paint_music_compact")
        self.assertAlmostEqual(image.pixelColor(180, 30).alphaF(), reveal, delta=0.015)

    def _middle_alpha(self, image):
        return sum(image.pixelColor(x, y).alpha() for y in range(14, 57) for x in range(90, 310))

    def test_real_gelv_and_ember_base_layers_obey_notice_crossfade(self):
        self.visual._audio_device_motion.announce(replace(PAYLOAD, battery_percent=None), self.now)
        self._advance_to(10.16)
        reveal = self.visual._audio_device_pose().reveal
        for style in ("gelv_songscatter", "ember_fade"):
            with self.subTest(style=style):
                self.visual.lyric_flow_style = style
                with patch.object(self.visual, "_audio_device_pose", return_value=None):
                    full = self._image("_paint_music_compact")
                with patch.object(self.visual, "_paint_audio_device_content"):
                    faded = self._image("_paint_music_compact")
                full_alpha = self._middle_alpha(full)
                self.assertGreater(full_alpha, 100)
                self.assertAlmostEqual(self._middle_alpha(faded) / full_alpha, 1.0 - reveal, delta=0.025)

    def test_real_gelv_and_ember_live_center_obeys_notice_crossfade(self):
        self.visual._audio_device_motion.announce(PAYLOAD, self.now)
        self._advance_to(10.16)
        pose = self.visual._audio_device_pose()
        for style in ("gelv_songscatter", "ember_fade"):
            with self.subTest(style=style):
                self.visual.lyric_flow_style = style
                with patch.object(self.visual, "_audio_device_pose", return_value=replace(pose, reveal=1.0)), patch.object(self.visual, "_paint_music_compact_base"):
                    full = self._image("_paint_music_compact")
                with patch.object(self.visual, "_paint_music_compact_base"):
                    faded = self._image("_paint_music_compact")
                full_alpha = self._middle_alpha(full)
                self.assertGreater(full_alpha, 100)
                self.assertAlmostEqual(self._middle_alpha(faded) / full_alpha, pose.reveal, delta=0.025)

    def test_narrow_tasker_preserves_selected_geometry_and_fits_glyph_ring(self):
        self._notice()
        self.visual.location_mode = "tasker"
        self.visual.taskbar_selected_drop_zone = QRectF(0.0, 0.0, 100.0, 38.0)
        self.visual._taskbar_height = lambda: 38.0
        width, height = self.visual._main_size_for_kind(IslandKind.MUSIC, 600.0)
        self.assertLessEqual(width, 100.0)
        self.assertLessEqual(height, 38.0)
        shell = QRectF(12.0, 10.0, width, height)
        glyphs = []
        inks = []
        icon_paint = __import__("local.audio_output_icons", fromlist=["paint_audio_output_icon"]).paint_audio_output_icon
        ink_reader = self.visual._audio_device_ink

        def icon(painter, rect, kind, color):
            glyphs.append(QRectF(rect))
            icon_paint(painter, rect, kind, color)

        def ink(rect):
            inks.append(QRectF(rect))
            return ink_reader(rect)

        with patch("local.island_stack_visual.paint_audio_output_icon", side_effect=icon), patch.object(self.visual, "_audio_device_ink", side_effect=ink):
            image = self._image(rect=shell)
        self.assertEqual(len(glyphs), 1)
        self.assertTrue(shell.contains(glyphs[0]))
        ring = inks[1]
        self.assertTrue(shell.contains(ring))
        self.assertLess(glyphs[0].right(), ring.left())
        for x, y in ((int(shell.left() - 1), int(shell.center().y())),
                     (int(shell.right() + 2), int(shell.center().y())),
                     (int(shell.center().x()), int(shell.top() - 1)),
                     (int(shell.center().x()), int(shell.bottom() + 2))):
            self.assertEqual(image.pixelColor(x, y).alpha(), 0)

    def test_early_entrance_sliding_glyph_is_clipped_to_existing_shell(self):
        self.visual._audio_device_motion.announce(PAYLOAD, self.now)
        self._advance_to(10.04)
        shell = QRectF(12.0, 10.0, 50.0, 34.0)
        image = self._image(rect=shell)
        for x in range(0, 11):
            for y in range(9, 46):
                self.assertEqual(image.pixelColor(x, y).alpha(), 0)

    def test_nonmusic_tasker_notice_does_not_increase_selected_height(self):
        self._notice()
        self.visual.location_mode = "tasker"
        self.visual.taskbar_selected_drop_zone = QRectF(0, 0, 100, 20)
        self.visual._taskbar_height = lambda: 20.0
        for kind in (IslandKind.EMPTY_TEXT, IslandKind.TIME, IslandKind.PLUGIN, IslandKind.THIN_BAR):
            with self.subTest(kind=kind):
                before = self.visual._base_main_size_for_kind(kind, 600)
                width, height = self.visual._main_size_for_kind(kind, 600)
                self.assertLessEqual(width, 100)
                self.assertEqual(height, before[1])

    def test_expanded_notice_does_not_take_over_content_or_layout(self):
        self._notice()
        self.visual.expanded = True
        self.visual.plugin_panel_open = True
        self.assertIsNone(self.visual._audio_device_pose())
        base_width = self.visual._base_music_target_width(True)
        self.assertEqual(self.visual._music_target_width(True), base_width)
        expanded = Mock()
        notice = Mock()
        with patch.object(self.visual, "_paint_music_expanded", expanded), patch.object(self.visual, "_paint_audio_device_content", notice):
            self._image(expanded=True)
        expanded.assert_called_once()
        notice.assert_not_called()
        self.assertTrue(self.visual.plugin_panel_open)
        self._advance_to(15.5)
        self.visual.expanded = False
        self.assertIsNone(self.visual._audio_device_pose())

    def test_hidden_and_suppressed_events_are_consumed_without_stale_replay(self):
        device = AudioRouteDevice("buds", PAYLOAD.name, kind=PAYLOAD.kind)
        with patch("local.island_stack_visual.time.monotonic", return_value=10.0):
            self.visual.on_audio_device_event(self._route(), device)
        self.assertFalse(self.visual._audio_device_motion.active)
        self.assertEqual(self.visual._audio_device_event_serial, 1)
        self._show()
        self.visual.scene_suppressed = True
        with patch("local.island_stack_visual.time.monotonic", return_value=10.0):
            self.visual.on_audio_device_event(self._route(2), device)
        self.assertFalse(self.visual._audio_device_motion.active)
        self.visual.scene_suppressed = False
        self.visual.on_audio_device_event(self._route(2), device)
        self.assertFalse(self.visual._audio_device_motion.active)

    def test_hiding_and_showing_clears_existing_notice(self):
        self._show()
        self._notice()
        self.visual.hide()
        self.assertFalse(self.visual._audio_device_motion.active)
        self._show()
        self.assertIsNone(self.visual._audio_device_pose())

    def test_opening_music_surface_clears_notice_without_replay_on_restore(self):
        self._show()
        self._notice()
        surface = SimpleNamespace(show_from_island=Mock(), isVisible=lambda: True, hide=Mock())
        self.visual.music_fullscreen_overlay = surface
        with patch.object(self.visual, "_current_main_global_rect", return_value=QRectF()), patch.object(self.visual, "_release_super_topmost"):
            self.visual._show_music_fullscreen()
        surface.show_from_island.assert_called_once()
        self.assertFalse(self.visual._audio_device_motion.active)
        self.assertTrue(self.visual._music_surface_active)
        with patch.object(self.visual, "_center_on_screen"), patch.object(self.visual, "_force_super_topmost"), patch.object(self.visual, "raise_"), patch.object(self.visual, "activateWindow"):
            self.visual._restore_island_from_music_surface()
        self.assertIsNone(self.visual._audio_device_pose())

    def test_provider_event_with_fresh_exact_device_battery_sets_payload(self):
        self._show()
        device = AudioRouteDevice("buds", PAYLOAD.name, kind=PAYLOAD.kind,
                                  battery_percent=70, battery_source="windows-driver-cache", battery_observed_at=1000.0)
        with patch("local.island_stack_visual.time.monotonic", return_value=10.0), patch("local.island_audio_device_motion.time.time", return_value=1010.0):
            self.visual.on_audio_device_event(self._route(), device)
        self.assertEqual(self.visual._audio_device_motion.pose.payload, PAYLOAD)
        self.assertEqual(self.visual._audio_device_event_serial, 1)

    def test_nonmusic_main_message_retains_identity_size_and_paint_priority(self):
        self._notice()
        self.visual.messages = [Message("Chat", "Sender", "An actual message", QColor("white"), QColor("gray"), serial=1)]
        self.visual._music_enabled_for_stack = lambda: False
        self.visual._plugin_enabled = lambda: False
        self.visual._empty_island_display_mode = lambda: "bar"
        self.assertEqual(self.visual._main_kind(), IslandKind.MESSAGE)
        base_size = self.visual._base_main_size_for_kind(IslandKind.MESSAGE, 300.0)
        self.assertEqual(self.visual._main_size_for_kind(IslandKind.MESSAGE, 300.0), base_size)
        rect = QRectF(12.0, 10.0, 300.0, 50.0)
        message = self.visual.messages[0]
        visual = StackVisual("actual-message", IslandKind.MESSAGE, rect, False, message, 10)
        motion = SpringRect(rect)
        motion.opacity = 1.0
        self.visual.motions = {visual.key: motion}
        image = QImage(self.visual.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        message_paint, notice_paint = Mock(), Mock()
        with patch.object(self.visual, "_build_visuals", return_value=[visual]), patch.object(self.visual, "_paint_stage"), patch.object(self.visual, "_paint_taskbar_drop_hint"), patch.object(self.visual, "_paint_message", message_paint), patch.object(self.visual, "_paint_audio_device_idle_island", notice_paint), patch.object(self.visual, "_update_interaction_mask"), patch("local.island_stack_visual.time.monotonic", return_value=self.now):
            self.visual.render(image)
        message_paint.assert_called_once()
        notice_paint.assert_not_called()

    def test_empty_text_and_time_use_inline_name_without_live_lyrics(self):
        self._notice()
        for kind in (IslandKind.EMPTY_TEXT, IslandKind.TIME, IslandKind.THIN_BAR, IslandKind.PLUGIN):
            with self.subTest(kind=kind):
                live = Mock()
                with patch.object(self.visual, "_paint_compact_lyric_viewport", live):
                    image = self._image(kind)
                live.assert_not_called()
                self.assertFalse(image.isNull())

    def test_paused_notice_uses_existing_active_frame_plan(self):
        self.visual._audio_device_motion.announce(PAYLOAD, self.now)
        self.visual.media_is_playing = False
        plan = self.visual._active_visual_animation_plan(self.now)
        self.assertTrue(plan.active)
        self.assertEqual(plan.reason, "audio_device")


if __name__ == "__main__":
    unittest.main()
