from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
import json
import re
import unittest
from unittest.mock import patch

from api.models import AlbumQueueEntry, AudioFrame, ColorToken, IslandVisualData, MessageData, TrackData
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPixmap
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtWidgets import QApplication

from local.island_stack_visual import IslandKind, IslandStackVisual, LyricLayout, Message, SpringRect, StackVisual, TaskbarDropOverlay
from local.mydockfinder import FinderGlassMaterial


class IslandStackVisualMessageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _png_bytes(self, color: QColor) -> bytes:
        image = QImage(8, 8, QImage.Format.Format_ARGB32)
        image.fill(color)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        return bytes(data)

    def test_ingest_provider_messages_keeps_latest_message_per_app(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.messages = []
        visual.barrage_clock = 12.0
        visual._seen_provider_message_keys = set()
        visual.plugin_samples = []

        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("Line",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
        )
        visual_data = IslandVisualData(
            track=track,
            messages=(
                MessageData("WeChat", "Alice", "first", ColorToken(75, 210, 110), ColorToken(75, 210, 110)),
                MessageData("QQ", "Bob", "qq", ColorToken(80, 170, 255), ColorToken(80, 170, 255)),
                MessageData("WeChat", "Alice", "latest", ColorToken(75, 210, 110), ColorToken(75, 210, 110)),
            ),
            plugins=(),
        )

        visual._ingest_provider_messages(visual_data)

        self.assertEqual([message.app for message in visual.messages], ["WeChat", "QQ"])
        self.assertEqual(visual.messages[0].text, "latest")
        self.assertEqual(len([message for message in visual.messages if message.app == "WeChat"]), 1)

    def test_clear_messages_for_app_removes_all_matching_messages(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.messages = [
            Message("微信", "张三", "第一条", QColor(75, 210, 110), QColor(75, 210, 110)),
            Message("QQ", "李四", "第二条", QColor(80, 170, 255), QColor(80, 170, 255)),
            Message("微信", "王五", "第三条", QColor(75, 210, 110), QColor(75, 210, 110)),
        ]
        visual.barrage_preview_messages = [
            Message("微信", "预览", "预览消息", QColor(75, 210, 110), QColor(75, 210, 110)),
            Message("钉钉", "预览", "其他预览", QColor(50, 150, 255), QColor(50, 150, 255)),
        ]
        visual.update = lambda: None

        visual._clear_messages_for_app("微信")

        self.assertEqual([message.app for message in visual.messages], ["QQ"])
        self.assertEqual([message.app for message in visual.barrage_preview_messages], ["钉钉"])

    def test_lyric_display_lines_preserve_explicit_newline(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True

        lines = visual._lyric_display_lines("第一句歌词\n第二句歌词")

        self.assertEqual(lines, ["第一句歌词", "第二句歌词"])

    def test_lyric_display_lines_collapse_dual_line_when_disabled(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = False

        lines = visual._lyric_display_lines("English lyric\n中文翻译")

        self.assertEqual(lines, ["English lyric"])

    def test_lyric_display_lines_avoid_short_slash_split(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True

        lines = visual._lyric_display_lines("A/B test")

        self.assertEqual(lines, ["A/B test"])

    def test_dual_line_secondary_settings_are_saved_in_snapshot(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = True
        visual.music_auto_detect = False
        visual.persistent_plugin = True
        visual.finder_mode = False
        visual.manual_finder_available = False
        visual.expanded = False
        visual.show_seconds = True
        visual.enabled_plugin_count = 3
        visual.ambient_intensity = 0.0
        visual.music_ambience_style = "sea_level"
        visual.music_flow_intensity = 0.74
        visual.compact_duotone_balance = 50
        visual.expanded_duotone_balance = 50
        visual.ui_font_family = "PingFang SC"
        visual.high_quality_font_rendering = False
        visual.animation_fps = 60
        visual.animation_speed = 1.0
        visual.empty_bar_palette = "aurora"
        visual.empty_bar_width = 92
        visual.empty_bar_height = 14
        visual.empty_bar_y_offset = 0
        visual.empty_bar_opacity = 100
        visual.empty_bar_custom_colors = ("#46D6FF", "#73FFA6", "#FF5A86")
        visual.stack_center_ratio = 0.5
        visual.saved_window_pos = None
        visual.stack_direction = 1
        visual.barrage_y_offset = 0
        visual.barrage_font_size = 10
        visual.barrage_speed = 100
        visual.barrage_use_backdrop = True
        visual.barrage_show_icon = True
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.lyric_offset_seconds = 0.0
        visual.lyric_font_size = 18
        visual.lyric_elastic_strength = 100
        visual.lyric_elastic_mode = "both"
        visual.lyric_word_spacing = 100
        visual.enable_dual_line_lyrics = True
        visual.dual_line_secondary_weight = 88
        visual.dual_line_secondary_spacing = 124
        visual.no_lyrics_text = "暂无歌词"
        visual.super_topmost_enabled = True

        snapshot = visual.settings_snapshot()

        self.assertEqual(snapshot["v2_dual_line_secondary_weight"], 88)
        self.assertEqual(snapshot["v2_dual_line_secondary_spacing"], 124)
        self.assertTrue(snapshot["v2_super_topmost"])

    def test_lyric_flow_settings_are_saved_in_snapshot(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = True
        visual.music_auto_detect = False
        visual.persistent_plugin = True
        visual.finder_mode = False
        visual.manual_finder_available = False
        visual.expanded = False
        visual.show_seconds = True
        visual.enabled_plugin_count = 3
        visual.ambient_intensity = 0.0
        visual.music_ambience_style = "sea_level"
        visual.music_flow_intensity = 0.74
        visual.compact_duotone_balance = 50
        visual.expanded_duotone_balance = 50
        visual.ui_font_family = "PingFang SC"
        visual.high_quality_font_rendering = False
        visual.animation_fps = 60
        visual.animation_speed = 1.0
        visual.empty_bar_palette = "aurora"
        visual.empty_bar_width = 92
        visual.empty_bar_height = 14
        visual.empty_bar_y_offset = 0
        visual.empty_bar_opacity = 100
        visual.empty_bar_custom_colors = ("#46D6FF", "#73FFA6", "#FF5A86")
        visual.stack_center_ratio = 0.5
        visual.saved_window_pos = None
        visual.stack_direction = 1
        visual.barrage_y_offset = 0
        visual.barrage_font_size = 10
        visual.barrage_speed = 100
        visual.barrage_use_backdrop = True
        visual.barrage_show_icon = True
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.lyric_offset_seconds = 0.0
        visual.lyric_font_size = 18
        visual.lyric_elastic_strength = 100
        visual.lyric_elastic_mode = "both"
        visual.lyric_word_spacing = 100
        visual.wave_color_fill_enabled = True
        visual.wave_color_fill_fallback = "#00D7FF"
        visual.enable_dual_line_lyrics = False
        visual.dual_line_secondary_weight = 72
        visual.dual_line_secondary_spacing = 100
        visual.no_lyrics_text = "暂无歌词"
        visual.super_topmost_enabled = True
        visual.lyric_flow_style = "gelv_songscatter"
        visual.gelv_font_family = "PingFang SC"
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 13
        visual.gelv_dual_line_font_size = 18
        visual.gelv_initial_word_spacing = 168
        visual.gelv_word_spacing = 248
        visual.gelv_size_shift_enabled = False
        visual.gelv_color_fill_enabled = True
        visual.gelv_color_fill_fallback = "#00D7FF"
        visual.no_lyrics_display_mode = "album_island"
        visual.album_island_cover_count = 7
        visual.album_island_cover_size = 34
        visual.album_island_spacing = 140
        visual.album_island_secondary_style = "perspective_dim"
        visual.album_island_perspective_amount = 135
        visual.compact_lyric_show_cover = False

        snapshot = visual.settings_snapshot()

        self.assertEqual(snapshot["v2_lyric_flow_style"], "gelv_songscatter")
        self.assertEqual(snapshot["v2_gelv_font_family"], "PingFang SC")
        self.assertEqual(snapshot["v2_gelv_primary_font_size"], 24)
        self.assertEqual(snapshot["v2_gelv_secondary_font_size"], 13)
        self.assertEqual(snapshot["v2_gelv_dual_line_font_size"], 18)
        self.assertEqual(snapshot["v2_gelv_initial_word_spacing"], 168)
        self.assertEqual(snapshot["v2_gelv_word_spacing"], 248)
        self.assertFalse(snapshot["v2_gelv_size_shift_enabled"])
        self.assertTrue(snapshot["v2_gelv_color_fill_enabled"])
        self.assertEqual(snapshot["v2_gelv_color_fill_fallback"], "#00D7FF")
        self.assertTrue(snapshot["v2_wave_color_fill_enabled"])
        self.assertEqual(snapshot["v2_wave_color_fill_fallback"], "#00D7FF")
        self.assertEqual(snapshot["v2_no_lyrics_display_mode"], "album_island")
        self.assertEqual(snapshot["v2_album_island_cover_count"], 7)
        self.assertEqual(snapshot["v2_album_island_cover_size"], 34)
        self.assertEqual(snapshot["v2_album_island_spacing"], 140)
        self.assertEqual(snapshot["v2_album_island_secondary_style"], "perspective_dim")
        self.assertEqual(snapshot["v2_album_island_perspective_amount"], 135)
        self.assertFalse(snapshot["v2_compact_lyric_show_cover"])

    def test_location_tasker_settings_are_saved_in_snapshot(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = True
        visual.music_auto_detect = False
        visual.persistent_plugin = True
        visual.finder_mode = False
        visual.manual_finder_available = False
        visual.expanded = False
        visual.show_seconds = True
        visual.enabled_plugin_count = 3
        visual.ambient_intensity = 0.0
        visual.music_ambience_style = "sea_level"
        visual.music_flow_intensity = 0.74
        visual.compact_duotone_balance = 50
        visual.expanded_duotone_balance = 50
        visual.ui_font_family = "PingFang SC"
        visual.high_quality_font_rendering = False
        visual.animation_fps = 60
        visual.animation_speed = 1.0
        visual.empty_bar_palette = "aurora"
        visual.empty_bar_width = 92
        visual.empty_bar_height = 14
        visual.empty_bar_y_offset = 0
        visual.empty_bar_opacity = 100
        visual.empty_bar_custom_colors = ("#46D6FF", "#73FFA6", "#FF5A86")
        visual.stack_center_ratio = 0.5
        visual.saved_window_pos = None
        visual.stack_direction = 1
        visual.barrage_y_offset = 0
        visual.barrage_font_size = 10
        visual.barrage_speed = 100
        visual.barrage_use_backdrop = True
        visual.barrage_show_icon = True
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.lyric_offset_seconds = 0.0
        visual.lyric_font_size = 18
        visual.lyric_elastic_strength = 100
        visual.lyric_elastic_mode = "both"
        visual.lyric_word_spacing = 100
        visual.wave_color_fill_enabled = False
        visual.wave_color_fill_fallback = "#00D7FF"
        visual.enable_dual_line_lyrics = False
        visual.dual_line_secondary_weight = 72
        visual.dual_line_secondary_spacing = 100
        visual.no_lyrics_text = "暂无歌词"
        visual.super_topmost_enabled = True
        visual.lyric_flow_style = "wave_stream"
        visual.gelv_font_family = "Source Han Serif SC"
        visual.gelv_primary_font_size = 22
        visual.gelv_secondary_font_size = 12
        visual.gelv_dual_line_font_size = 17
        visual.gelv_initial_word_spacing = 220
        visual.gelv_word_spacing = 320
        visual.gelv_size_shift_enabled = True
        visual.gelv_color_fill_enabled = False
        visual.gelv_color_fill_fallback = "#00D7FF"
        visual.no_lyrics_display_mode = "text"
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_island_secondary_style = "perspective_dim"
        visual.album_island_perspective_amount = 100
        visual.compact_lyric_show_cover = True
        visual.location_mode = "tasker"
        visual.taskbar_fusion_mode = "compact"
        visual.taskbar_recognition_threshold = 0.66
        visual.taskbar_corner_style = "win11"
        visual.taskbar_plugin_compact_style = "block"
        visual.taskbar_preview_visible = True
        visual.dual_line_font_size = 13

        snapshot = visual.settings_snapshot()

        self.assertEqual(snapshot["v2_location_mode"], "tasker")
        self.assertEqual(snapshot["v2_taskbar_corner_style"], "win11")
        self.assertEqual(snapshot["v2_dual_line_font_size"], 13)
        self.assertNotIn("v2_taskbar_recognition_threshold", snapshot)
        self.assertNotIn("v2_taskbar_plugin_compact_style", snapshot)
        self.assertNotIn("v2_taskbar_message_compact_style", snapshot)
        self.assertNotIn("v2_taskbar_fusion_mode", snapshot)
        self.assertNotIn("v2_taskbar_hint_text", snapshot)

    def test_taskbar_preview_appears_when_free_mode_is_dragged_into_lower_third(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = False
        visual.manual_finder_available = False
        visual.location_mode = "free"
        visual.taskbar_fusion_mode = "off"
        visual.taskbar_preview_visible = False
        visual.taskbar_recognition_threshold = 0.66

        self.assertFalse(visual._should_show_taskbar_preview(0.62))
        self.assertTrue(visual._should_show_taskbar_preview(0.70))

    def test_taskbar_preview_requires_main_rect_contact_not_only_lower_third(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = False
        visual.manual_finder_available = False
        visual.location_mode = "free"
        visual.taskbar_recognition_threshold = 0.66
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        floating = QRectF(760.0, 930.0, 360.0, 38.0)
        touching = QRectF(760.0, 1026.0, 360.0, 38.0)

        self.assertFalse(visual._should_show_taskbar_preview_for_rect(0.95, floating, taskbar))
        self.assertTrue(visual._should_show_taskbar_preview_for_rect(0.95, touching, taskbar))

    def test_taskbar_preview_hides_when_finder_or_manual_mydockfinder_is_enabled(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = True
        visual.manual_finder_available = False
        visual.location_mode = "finder"
        visual.taskbar_fusion_mode = "off"
        visual.taskbar_preview_visible = False
        visual.taskbar_recognition_threshold = 0.66

        self.assertFalse(visual._should_show_taskbar_preview(0.95))
        visual.finder_mode = False
        visual.location_mode = "free"
        visual.manual_finder_available = True

        self.assertFalse(visual._should_show_taskbar_preview(0.95))

    def test_accepting_taskbar_drop_sets_tasker_location_mode(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = False
        visual.manual_finder_available = False
        visual.location_mode = "free"
        visual.taskbar_fusion_mode = "off"
        visual.taskbar_preview_visible = True
        visual.expanded = False
        visual.plugin_panel_open = False
        dock_calls: list[str] = []
        visual._dock_to_taskbar_task_area = lambda: dock_calls.append("dock") or True
        visual._pulse_stack = lambda: None
        visual.update = lambda: None

        self.assertTrue(visual._accept_taskbar_preview_drop())
        self.assertEqual(visual.location_mode, "tasker")
        self.assertEqual(visual.taskbar_fusion_mode, "compact")
        self.assertEqual(dock_calls, ["dock"])

    def test_tasker_location_reverts_to_free_when_leaving_taskbar_zone(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = False
        visual.manual_finder_available = False
        visual.location_mode = "tasker"
        visual.taskbar_fusion_mode = "compact"
        visual.taskbar_preview_visible = True
        visual._pulse_stack = lambda: None
        visual.update = lambda: None

        visual._set_taskbar_presence(False, notify=False)

        self.assertEqual(visual.location_mode, "free")
        self.assertEqual(visual.taskbar_fusion_mode, "off")
        self.assertFalse(visual.taskbar_preview_visible)

    def test_taskbar_drop_zone_rect_uses_taskbar_bounds(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)

        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        zone = visual._taskbar_drop_zone_rect(taskbar, desired_width=360.0)

        self.assertGreaterEqual(zone.top(), taskbar.top())
        self.assertLessEqual(zone.bottom(), taskbar.bottom())
        self.assertAlmostEqual(zone.center().x(), taskbar.center().x(), delta=0.1)

    def test_taskbar_drop_zone_prefers_detected_task_area(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        screen = object()
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        task_area = QRectF(180.0, 1040.0, 1320.0, 40.0)
        visual._taskbar_rect_for_screen = lambda _screen: taskbar
        visual._taskbar_task_area_rect_for_screen = lambda _screen, _taskbar: task_area

        zones = visual._taskbar_drop_zones_for_screen(screen, desired_width=360.0)

        self.assertEqual(len(zones), 2)
        self.assertTrue(all(zone.left() >= task_area.left() and zone.right() <= task_area.right() for zone in zones))
        self.assertLess(zones[0].center().x(), task_area.center().x())
        self.assertGreater(zones[1].center().x(), task_area.center().x())

    def test_taskbar_drop_zones_use_left_and_right_available_slots(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        screen = object()
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_rect_for_screen = lambda _screen: taskbar
        visual._taskbar_task_area_rect_for_screen = lambda _screen, _taskbar: taskbar
        visual._taskbar_occupied_rects_for_screen = lambda _screen, _taskbar: [
            QRectF(0.0, 1040.0, 170.0, 40.0),
            QRectF(640.0, 1040.0, 710.0, 40.0),
            QRectF(1810.0, 1040.0, 110.0, 40.0),
        ]

        zones = visual._taskbar_drop_zones_for_screen(screen, desired_width=360.0)

        self.assertEqual(len(zones), 2)
        self.assertLess(zones[0].center().x(), taskbar.center().x())
        self.assertGreater(zones[1].center().x(), taskbar.center().x())

    def test_taskbar_drop_zones_split_empty_taskbar_into_left_and_right_not_center(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        screen = object()
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_rect_for_screen = lambda _screen: taskbar
        visual._taskbar_task_area_rect_for_screen = lambda _screen, _taskbar: taskbar
        visual._taskbar_occupied_rects_for_screen = lambda _screen, _taskbar: []

        zones = visual._taskbar_drop_zones_for_screen(screen, desired_width=360.0)

        self.assertEqual(len(zones), 2)
        self.assertLess(zones[0].center().x(), taskbar.center().x() - 120.0)
        self.assertGreater(zones[1].center().x(), taskbar.center().x() + 120.0)
        self.assertFalse(any(abs(zone.center().x() - taskbar.center().x()) < 80.0 for zone in zones))

    def test_taskbar_drop_zone_selection_uses_drag_position(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        zones = [QRectF(190.0, 1044.0, 360.0, 32.0), QRectF(1460.0, 1044.0, 320.0, 32.0)]

        selected = visual._select_taskbar_drop_zone(zones, QPoint(1580, 1060))

        self.assertEqual(selected, zones[1])

    def test_tasker_track_drag_updates_position_ratio_within_taskbar_bounds(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        track = QRectF(180.0, 1040.0, 1500.0, 40.0)

        left_ratio = visual._taskbar_position_ratio_for_center(track, 280.0, 100.0)
        right_ratio = visual._taskbar_position_ratio_for_center(track, 280.0, 2000.0)
        middle_ratio = visual._taskbar_position_ratio_for_center(track, 280.0, 930.0)

        self.assertEqual(left_ratio, 0.0)
        self.assertEqual(right_ratio, 1.0)
        self.assertAlmostEqual(middle_ratio, 0.5, delta=0.02)

    def test_tasker_left_resize_handle_is_small_rounded_vertical_rail(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        main_rect = QRectF(520.0, 1044.0, 360.0, 34.0)

        handle = visual._taskbar_left_handle_rect(main_rect)

        self.assertLess(handle.width(), handle.height())
        self.assertLessEqual(handle.right(), main_rect.left() - 3.0)
        self.assertLessEqual(handle.width(), 4.0)
        self.assertGreater(handle.height(), main_rect.height() * 0.45)

    def test_tasker_left_resize_handle_is_visible_only_on_tasker_hover_or_drag(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.hovered_key = ""
        visual.drag_press_key = ""
        visual.dragging = False

        self.assertFalse(visual._taskbar_left_handle_visible())

        visual.hovered_key = "music"
        self.assertTrue(visual._taskbar_left_handle_visible())

        visual.hovered_key = ""
        visual.dragging = True
        visual.drag_press_key = "taskbar:left-handle"
        self.assertTrue(visual._taskbar_left_handle_visible())

    def test_tasker_window_position_clamps_to_bottom_screen_boundary(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        screen_geo = QRect(0, 0, 1920, 1080)
        new_pos = QPoint(500, 1100)

        clamped = visual._clamp_taskbar_window_pos(new_pos, main_top=940.0, main_w=420.0, main_h=176.0, screen_geo=screen_geo)

        self.assertLessEqual(clamped.y() + 940.0 + 176.0, screen_geo.bottom() - 6.0 + 1.0)

    def test_tasker_compact_music_width_is_stable_across_content_width_changes(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_manual_width = 0.0
        visual.taskbar_selected_drop_zone = QRectF()
        visual._u = lambda value: float(value)

        narrow = visual._taskbar_compact_music_size(300.0, 44.0)
        wide = visual._taskbar_compact_music_size(760.0, 44.0)

        self.assertEqual(narrow[0], wide[0])
        self.assertGreaterEqual(narrow[0], 360.0)

    def test_tasker_follow_position_preserves_intended_width_when_track_detection_is_narrow(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_position_ratio = 0.5
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(960, 1040)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, _taskbar: QRectF(910.0, 1040.0, 96.0, 40.0)
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 760.0
        visual._main_size_for_kind = lambda _kind, _stack_w: (420.0, 34.0)
        visual._main_top_for_direction = lambda *_args: 10.0
        visual.move = lambda _pos: None

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertAlmostEqual(visual.taskbar_selected_drop_zone.width(), 420.0, delta=0.1)

    def test_set_location_mode_tasker_refreshes_position_immediately(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.manual_finder_available = False
        visual.location_mode = "free"
        visual.finder_mode = False
        visual.attach_to_real_finder = False
        visual.taskbar_preview_visible = False
        visual.scene_suppressed = False
        visual.scene_reason = ""
        visual.scene_transition_mode = ""
        visual.group_frame_mask_path = None
        visual._reset_location_runtime_state = lambda **_kwargs: None
        visual._begin_surface_transition = lambda _reason="": False
        visual._sync_taskbar_drop_overlay = lambda: None
        visual.clearMask = lambda: None
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None
        refresh_calls: list[str] = []
        visual._refresh_taskbar_follow_position = lambda: refresh_calls.append("refresh") or True

        visual.set_location_mode("tasker", notify=False)

        self.assertEqual(visual.location_mode, "tasker")
        self.assertEqual(refresh_calls, ["refresh"])

    def test_tasker_follow_recovers_from_stale_cover_only_selected_zone(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.expanded = False
        visual.taskbar_position_ratio = 0.5
        visual.taskbar_manual_width = 0.0
        visual.taskbar_selected_drop_zone = QRectF(930.0, 1044.0, 42.0, 32.0)
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 650
        visual.mapToGlobal = lambda _point: QPoint(960, 1080)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, _taskbar: QRectF(180.0, 1040.0, 1500.0, 40.0)
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 760.0
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertGreaterEqual(visual.taskbar_selected_drop_zone.width(), 360.0)
        self.assertTrue(moves)
        main_global = QRectF(
            moves[-1].x() + visual.width() / 2.0 - visual.taskbar_selected_drop_zone.width() / 2.0,
            visual.taskbar_selected_drop_zone.top(),
            visual.taskbar_selected_drop_zone.width(),
            visual.taskbar_selected_drop_zone.height(),
        )
        self.assertGreaterEqual(main_global.left(), 0.0)
        self.assertLessEqual(main_global.right(), 1920.0)
        self.assertLessEqual(main_global.bottom(), 1080.0)

    def test_tasker_follow_skips_redundant_window_move(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.expanded = True
        visual.taskbar_position_ratio = 0.5
        visual.taskbar_manual_width = 0.0
        visual.taskbar_selected_drop_zone = QRectF()
        visual.saved_window_pos = None
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 650
        visual.mapToGlobal = lambda _point: QPoint(960, 1080)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, _taskbar: QRectF(180.0, 1040.0, 1500.0, 40.0)
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 760.0
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._refresh_taskbar_follow_position())
            self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertEqual(len(moves), 1)

    def test_tasker_tick_skips_recent_follow_refresh(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.last_tick_at = 100.0
        visual.animation_speed = 1.0
        visual.location_mode = "tasker"
        visual.drag_candidate = False
        visual.taskbar_follow_last_at = 100.0
        visual.scene_suppressed = False
        visual.pointer_pos = QPointF(-1.0, -1.0)
        visual.barrage_clock = 0.0
        visual.super_topmost_enabled = False
        visual.media_is_playing = False
        visual.timed_lyrics = []
        visual.lyric_changed_at = 100.0
        visual.lyrics = ["line"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.hovered_key = ""
        visual.hover_armed_key = ""
        visual.hover_started_at = 0.0
        visual.expanded = False
        visual.plugin_panel_open = False
        visual.left_stack_at = 0.0
        visual.scroll_phase = 0.0
        visual.ambient_phase = 0.0
        visual._refresh_media_data = lambda _now: None
        visual._update_scene_state = lambda _now: None
        visual._point_in_barrage = lambda _point: False
        visual._maybe_trigger_alarm_row_long_press = lambda _now: False
        visual._update_wave_values = lambda: None
        visual.update = lambda: None
        refresh_calls: list[str] = []
        visual._refresh_taskbar_follow_position = lambda: refresh_calls.append("refresh") or True

        with patch("local.island_stack_visual.time.monotonic", return_value=100.02):
            visual._tick()

        self.assertEqual(refresh_calls, [])

    def test_tasker_follow_skips_stable_material_refresh(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.expanded = True
        visual.taskbar_position_ratio = 0.5
        visual.taskbar_manual_width = 0.0
        visual.taskbar_selected_drop_zone = QRectF()
        visual.saved_window_pos = None
        visual.taskbar_follow_last_key = ""
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 650
        visual.mapToGlobal = lambda _point: QPoint(960, 1080)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, _taskbar: QRectF(180.0, 1040.0, 1500.0, 40.0)
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 760.0
        visual.move = lambda _pos: None
        material_calls: list[str] = []
        visual._update_taskbar_material_for_screen = lambda *_args: material_calls.append("material")

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            with patch("local.island_stack_visual.time.monotonic", return_value=200.0):
                self.assertTrue(visual._refresh_taskbar_follow_position())
            with patch("local.island_stack_visual.time.monotonic", return_value=200.05):
                self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertEqual(material_calls, ["material"])

    def test_taskbar_material_exclusion_uses_tasker_slot_without_rebuilding_layout(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_selected_drop_zone = QRectF(500.0, 1042.0, 420.0, 34.0)
        visual.motions = {}
        visual._u = lambda value: float(value)
        visual.mapToGlobal = lambda _point: QPoint(0, 0)
        rebuild_calls: list[str] = []

        def rebuild_layout():
            rebuild_calls.append("layout")
            return QRectF(500.0, 1042.0, 420.0, 34.0)

        visual._current_main_global_rect = rebuild_layout

        rects = visual._taskbar_material_exclusion_rects()

        self.assertEqual(rebuild_calls, [])
        self.assertTrue(rects)
        self.assertTrue(any(rect.contains(visual.taskbar_selected_drop_zone.center()) for rect in rects))

    def test_tasker_vertical_direction_uses_taskbar_position_not_standalone_auto(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.expanded = True
        visual.persistent_plugin = False
        visual.messages = []
        visual.stack_direction = 1
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.last_taskbar_rect = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._u = lambda value: float(value)
        visual.mapToGlobal = lambda _point: QPoint(0, 0)

        direction = visual._vertical_stack_direction(IslandKind.MUSIC, 176.0, QRect(0, 0, 1920, 1080))

        self.assertEqual(direction, -1)

    def test_tasker_window_move_treats_one_pixel_jitter_as_same_position(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.saved_window_pos = (100, 200)
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        moved = visual._move_taskbar_window_if_needed(QPoint(101, 199))

        self.assertFalse(moved)
        self.assertEqual(moves, [])

    def test_tasker_scene_suppression_ignores_fullscreen_foreground(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.scene_checked_at = -10.0
        visual.scene_suppressed = False
        visual.scene_transition_mode = ""
        visual.hovered_key = "music"
        visual.hover_armed_key = "music"
        visual.left_stack_at = 1.0
        visual.group_frame_mask_path = None
        visual._detect_scene_suppression = lambda: (True, "fullscreen")
        visual._set_input_passthrough = lambda _enabled: (_ for _ in ()).throw(AssertionError("Tasker should not passthrough on fullscreen"))
        visual.clearMask = lambda: None
        visual.update = lambda: None

        visual._update_scene_state(1.0)

        self.assertFalse(visual.scene_suppressed)
        self.assertEqual(visual.scene_reason, "")

    def test_tasker_follow_refresh_repositions_when_taskbar_moves(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_position_ratio = 0.8
        visual.taskbar_manual_width = 0.0
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(840, 1040)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1000.0, 1920.0, 80.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, _taskbar: QRectF(180.0, 1000.0, 1500.0, 80.0)
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_kind = lambda _kind, _stack_w: (300.0, 62.0)
        visual._main_top_for_direction = lambda *_args: 10.0
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertTrue(moves)
        self.assertLessEqual(moves[-1].y() + 10.0 + 62.0, 1080.0)

    def test_taskbar_expanded_material_tokens_follow_sampled_taskbar_color(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_material_color = QColor(238, 240, 244)
        visual.taskbar_material_is_light = True

        tokens = visual._taskbar_material_frame_tokens()

        self.assertGreater(tokens["fill_top"].red(), 210)
        self.assertLess(tokens["stroke"].alpha(), 120)

    def test_taskbar_message_block_uses_app_color_without_sender_initial(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        msg = Message("微信", "张三", "hello", QColor(75, 210, 110), QColor(75, 210, 110))

        model = visual._taskbar_message_block_model(msg, QRectF(0.0, 0.0, 34.0, 34.0))

        self.assertEqual(model["fill"], QColor(75, 210, 110))
        self.assertNotEqual(model["glyph"], "张")
        self.assertNotEqual(model["outer"], QColor(0, 0, 0, 238))

    def test_tasker_compact_album_island_uses_current_cover_without_side_slots(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_island_secondary_style = "perspective_dim"
        visual.album_island_perspective_amount = 100
        visual.album_island_transition_started_at = 1.0
        visual.album_island_transition_direction = 1
        visual._u = lambda value: float(value)
        art = QImage(16, 16, QImage.Format.Format_ARGB32)
        art.fill(QColor(120, 80, 50))
        previous = QImage(16, 16, QImage.Format.Format_ARGB32)
        previous.fill(QColor(50, 90, 140))
        next_art = QImage(16, 16, QImage.Format.Format_ARGB32)
        next_art.fill(QColor(180, 90, 120))
        visual.album_art = art
        visual.album_history = [{"track_id": "old", "album_art": previous}]
        visual.album_queue = [{"track_id": "next", "album_art": next_art}]

        with patch("local.island_stack_visual.time.monotonic", return_value=1.08):
            slots = visual._album_island_slots(QRectF(0.0, 0.0, 260.0, 34.0), reserve_text=True)

        self.assertEqual(len(slots), 1)
        self.assertEqual(int(slots[0]["side"]), 0)

    def test_taskbar_message_rails_cap_at_three_and_stay_inside_main_shell(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_message_compact_style = "strip"
        visual.music_playing = False
        visual.persistent_plugin = False
        visual.messages = [
            Message("微信", str(index), "hello", QColor(75, 210, 110), QColor(75, 210, 110))
            for index in range(5)
        ]
        visual._u = lambda value: float(value)
        main_rect = QRectF(500.0, 1044.0, 280.0, 34.0)

        rail = visual._taskbar_message_rect(main_rect, 0, 3, 1)
        second = visual._taskbar_message_rect(main_rect, 1, 3, 1)

        self.assertEqual(visual._folded_message_limit(IslandKind.MESSAGE), 3)
        self.assertLessEqual(rail.right(), main_rect.right())
        self.assertGreaterEqual(rail.left(), main_rect.left())
        self.assertAlmostEqual(main_rect.right() - rail.right(), 3.0, delta=0.1)
        self.assertGreaterEqual(second.left(), main_rect.left())
        self.assertLessEqual(second.right(), main_rect.right())

    def test_taskbar_material_sampling_skips_own_island_region(self):
        class FakeScreen:
            def __init__(self, exclusion: QRectF):
                self.exclusion = QRectF(exclusion)
                self.samples: list[QRectF] = []

            def grabWindow(self, _win_id, x, y, width, height):
                sample = QRectF(float(x), float(y), float(width), float(height))
                self.samples.append(sample)
                image = QImage(max(1, int(width)), max(1, int(height)), QImage.Format.Format_ARGB32)
                center = sample.center()
                if self.exclusion.contains(center):
                    image.fill(QColor(75, 210, 110))
                else:
                    image.fill(QColor(36, 38, 42))
                return QPixmap.fromImage(image)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        exclusion = QRectF(920.0, 1000.0, 160.0, 80.0)
        visual._taskbar_material_exclusion_rects = lambda: [QRectF(exclusion)]
        screen = FakeScreen(exclusion)

        color = visual._sample_taskbar_material_color(screen, QRectF(0.0, 1000.0, 2000.0, 80.0))

        self.assertEqual(color, QColor(36, 38, 42))
        self.assertTrue(screen.samples)
        self.assertFalse(exclusion.contains(screen.samples[-1].center()))

    def test_taskbar_material_sampling_is_throttled_between_animation_ticks(self):
        class FakeScreen:
            def __init__(self):
                self.calls = 0

            def grabWindow(self, _win_id, _x, _y, width, height):
                self.calls += 1
                image = QImage(max(1, int(width)), max(1, int(height)), QImage.Format.Format_ARGB32)
                image.fill(QColor(36, 38, 42))
                return QPixmap.fromImage(image)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.taskbar_material_color = QColor()
        visual.taskbar_material_is_light = False
        visual.taskbar_material_sampled_at = 0.0
        visual.taskbar_material_sample_key = ""
        visual._taskbar_material_exclusion_rects = lambda: []
        screen = FakeScreen()
        taskbar = QRectF(0.0, 1000.0, 2000.0, 80.0)

        with patch("local.island_stack_visual.time.monotonic", return_value=100.0):
            visual._update_taskbar_material_for_screen(screen, taskbar)
        first_refresh_calls = screen.calls
        with patch("local.island_stack_visual.time.monotonic", return_value=100.05):
            visual._update_taskbar_material_for_screen(screen, taskbar)

        self.assertGreater(first_refresh_calls, 0)
        self.assertEqual(screen.calls, first_refresh_calls)

    def test_taskbar_expanded_frame_targets_visual_rects_not_child_spring_rects(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.finder_mode = False
        visual.location_mode = "tasker"
        visual.taskbar_corner_style = "win11"
        visual.taskbar_material_color = QColor(36, 38, 42)
        visual.taskbar_material_is_light = False
        visual.group_frame_motion = None
        visual.group_frame_mask_path = None
        visual.finder_hover_rect = QRectF()
        visual._u = lambda value: float(value)
        image = QImage(900, 260, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        target_rect = QRectF(320.0, 120.0, 420.0, 52.0)
        old_motion = SpringRect(QRectF(40.0, 20.0, 18.0, 18.0))
        old_motion.opacity = 1.0
        items = [(StackVisual("music", IslandKind.MUSIC, target_rect, False, None, 10), old_motion)]

        visual._paint_taskbar_group_frame(painter, items, 1.0)
        painter.end()

        self.assertTrue(visual.finder_hover_rect.contains(target_rect.center()))

    def test_taskbar_expanded_frame_ignores_transient_plugin_alerts(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.finder_mode = False
        visual.location_mode = "tasker"
        visual.taskbar_corner_style = "win11"
        visual.taskbar_material_color = QColor(36, 38, 42)
        visual.taskbar_material_is_light = False
        visual.group_frame_motion = None
        visual.group_frame_mask_path = None
        visual.finder_hover_rect = QRectF()
        visual._u = lambda value: float(value)
        image = QImage(1200, 320, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        main_rect = QRectF(420.0, 140.0, 360.0, 48.0)
        alert_rect = QRectF(980.0, 140.0, 120.0, 42.0)
        main_motion = SpringRect(main_rect)
        main_motion.opacity = 1.0
        alert_motion = SpringRect(alert_rect)
        alert_motion.opacity = 1.0
        items = [
            (StackVisual("music", IslandKind.MUSIC, main_rect, False, None, 10), main_motion),
            (StackVisual("plugin-alert:time", IslandKind.PLUGIN_ALERT, alert_rect, False, None, 8), alert_motion),
        ]

        visual._paint_taskbar_group_frame(painter, items, 1.0)
        painter.end()

        self.assertTrue(visual.finder_hover_rect.contains(main_rect.center()))
        self.assertLess(visual.finder_hover_rect.right(), alert_rect.left())

    def test_tasker_compact_drops_stale_side_motion_without_exit_ghost(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"

        drop = getattr(visual, "_taskbar_should_drop_stale_motion_key", lambda _key: False)

        self.assertTrue(drop("plugin-alert:time"))
        self.assertTrue(drop("taskbar-overlay:time"))
        self.assertTrue(drop("message-dot:wechat"))
        self.assertFalse(drop("music"))

    def test_tasker_motion_uses_finder_like_stable_response(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.stack_bounce_until = 0.0

        params = getattr(visual, "_visual_motion_params", lambda _now: (0.34, 0.76))(10.0)

        self.assertLessEqual(params[0], 0.22)
        self.assertGreaterEqual(params[1], 0.88)

    def test_group_frame_target_reuses_tiny_tasker_jitter(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        previous = QRectF(100.0, 50.0, 320.0, 46.0)
        visual.group_frame_target_rect = QRectF(previous)
        visual.group_frame_target_mode_key = "tasker"

        coalesce = getattr(visual, "_coalesced_group_frame_target", lambda target, _mode: QRectF(target))
        tiny = coalesce(QRectF(101.0, 50.8, 319.4, 46.6), "tasker")
        large = coalesce(QRectF(116.0, 52.0, 320.0, 46.0), "tasker")

        self.assertEqual(tiny, previous)
        self.assertNotEqual(large, previous)

    def test_album_history_entry_downscales_large_art_for_memory(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "track-1"
        visual.track_title = "Song"
        visual.artist = "Artist"
        art = QImage(1200, 900, QImage.Format.Format_ARGB32)
        art.fill(QColor(80, 120, 180))
        visual.album_art = art
        visual.listened_seconds_total = 0.0

        entry = visual._current_album_history_entry()
        image = entry["album_art"]

        self.assertLessEqual(max(image.width(), image.height()), 256)

    def test_scaled_album_art_cache_keeps_only_small_recent_working_set(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._scaled_album_art_cache = {}
        art = QImage(320, 320, QImage.Format.Format_ARGB32)
        art.fill(QColor(80, 120, 180))
        image = QImage(260, 80, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)

        for index in range(7):
            visual._paint_cover(
                painter,
                QRectF(8.0 + index, 8.0, 42.0 + index, 42.0),
                6.0,
                art,
                cache_key=f"stress-{index}",
            )
        painter.end()

        self.assertLessEqual(len(visual._scaled_album_art_cache), 4)

    def test_lyric_layout_cache_keeps_small_recent_working_set(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._lyric_layout_cache = {}
        visual.ui_font_family = "PingFang SC"
        visual.high_quality_font_rendering = False
        visual.font_shrink_scale = 1.0
        visual.lyric_elastic_strength = 100
        visual.lyric_word_spacing = 100

        for index in range(72):
            visual._lyric_layout(f"line {index}", 16, True)

        self.assertLessEqual(len(visual._lyric_layout_cache), 48)

    def test_taskbar_group_frame_resets_when_target_jumps_from_stale_mode(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.finder_mode = False
        visual.location_mode = "tasker"
        visual.taskbar_corner_style = "win11"
        visual.taskbar_material_color = QColor(36, 38, 42)
        visual.taskbar_material_is_light = False
        visual.group_frame_motion = SpringRect(QRectF(0.0, 0.0, 1150.0, 220.0))
        visual.group_frame_motion.opacity = 1.0
        visual.group_frame_mode_key = "finder"
        visual.group_frame_mask_path = None
        visual.finder_hover_rect = QRectF()
        visual._u = lambda value: float(value)
        image = QImage(900, 260, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        main_rect = QRectF(360.0, 118.0, 300.0, 44.0)
        motion = SpringRect(main_rect)
        motion.opacity = 1.0

        visual._paint_taskbar_group_frame(painter, [(StackVisual("music", IslandKind.MUSIC, main_rect, False, None, 10), motion)], 1.0)
        painter.end()

        self.assertLess(visual.finder_hover_rect.width(), main_rect.width() + 60.0)
        self.assertAlmostEqual(visual.finder_hover_rect.center().x(), main_rect.center().x(), delta=20.0)

    def test_finder_frame_targets_visual_rects_not_child_spring_rects(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.finder_mode = True
        visual.location_mode = "finder"
        visual.group_frame_motion = None
        visual.group_frame_mask_path = None
        visual.finder_hover_rect = QRectF()
        visual.stack_direction = 1
        visual._u = lambda value: float(value)
        visual.width = lambda: 900
        visual.finder_material = FinderGlassMaterial(
            top_color=QColor(31, 32, 36, 235),
            mid_color=QColor(37, 38, 42, 235),
            bottom_color=QColor(44, 45, 50, 238),
            highlight_alpha=88,
            border_alpha=100,
            shadow_alpha=50,
            bar_height=44.0,
            blur_value=11,
            source="test",
            corner_radius=12.0,
            available=True,
        )
        visual._has_current_two_line_lyric = lambda: False
        image = QImage(900, 360, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        target_rect = QRectF(340.0, 118.0, 360.0, 64.0)
        old_motion = SpringRect(QRectF(32.0, 18.0, 20.0, 18.0))
        old_motion.opacity = 1.0
        items = [(StackVisual("music", IslandKind.MUSIC, target_rect, False, None, 10), old_motion)]

        visual._paint_myfinder_group_frame(painter, items, 1.0)
        painter.end()

        self.assertGreater(visual.finder_hover_rect.width(), target_rect.width())
        self.assertAlmostEqual(visual.finder_hover_rect.center().x(), target_rect.center().x(), delta=28.0)

    def test_manual_finder_toggle_resets_stale_tasker_frame_and_transition(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.attach_to_real_finder = False
        visual.manual_finder_available = False
        visual.taskbar_fusion_mode = "compact"
        visual.taskbar_preview_visible = True
        visual.taskbar_selected_drop_zone = QRectF(100.0, 100.0, 300.0, 40.0)
        visual.taskbar_drop_zones = [QRectF(100.0, 100.0, 300.0, 40.0)]
        visual.group_frame_motion = SpringRect(QRectF(0.0, 0.0, 1100.0, 240.0))
        visual.group_frame_mask_path = object()
        visual.group_frame_mode_key = "tasker"
        visual.finder_hover_rect = QRectF(20.0, 20.0, 400.0, 60.0)
        visual.interaction_mask_active = True
        visual.last_mask_rect = QRectF(20.0, 20.0, 400.0, 60.0)
        visual.surface_transition_pixmap = QPixmap(8, 8)
        visual.surface_transition_global_rect = QRectF(10.0, 10.0, 8.0, 8.0)
        visual.surface_transition_reason = "tasker"
        visual.hovered_key = "plugin"
        visual.hover_armed_key = "plugin"
        visual.left_stack_at = 10.0
        visual.drag_detach_global_rects = {"music": QRectF(10.0, 10.0, 30.0, 30.0)}
        visual._sync_taskbar_drop_overlay = lambda: None
        visual._pulse_stack = lambda: None
        visual._notify_settings_changed = lambda: None
        visual.update = lambda: None
        visual.clearMask = lambda: None

        visual.set_manual_finder_available(True, notify=False)

        self.assertEqual(visual.location_mode, "free")
        self.assertFalse(visual.finder_mode)
        self.assertEqual(visual.taskbar_fusion_mode, "off")
        self.assertFalse(visual.taskbar_preview_visible)
        self.assertIsNone(visual.group_frame_motion)
        self.assertIsNone(visual.group_frame_mask_path)
        self.assertTrue(visual.finder_hover_rect.isNull())
        self.assertFalse(visual.interaction_mask_active)
        self.assertTrue(visual.last_mask_rect.isNull())
        self.assertTrue(visual.surface_transition_pixmap.isNull())
        self.assertTrue(visual.surface_transition_global_rect.isNull())
        self.assertEqual(visual.surface_transition_reason, "")
        self.assertEqual(visual.hovered_key, "")
        self.assertEqual(visual.hover_armed_key, "")
        self.assertEqual(visual.drag_detach_global_rects, {})

    def test_finder_attach_disallowed_when_manual_finder_is_off(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.manual_finder_available = False
        visual.finder_material = FinderGlassMaterial(
            top_color=QColor(31, 32, 36, 235),
            mid_color=QColor(37, 38, 42, 235),
            bottom_color=QColor(44, 45, 50, 238),
            highlight_alpha=88,
            border_alpha=100,
            shadow_alpha=50,
            bar_height=44.0,
            blur_value=11,
            source="test",
            corner_radius=12.0,
            available=True,
        )

        self.assertFalse(visual._finder_attach_allowed())

        visual.manual_finder_available = True
        self.assertTrue(visual._finder_attach_allowed())

    def test_taskbar_left_handle_stays_visible_for_three_seconds_after_hover(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.hovered_key = ""
        visual.dragging = False
        visual.drag_press_key = ""
        visual.taskbar_left_handle_visible_until = 12.0

        with patch("local.island_stack_visual.time.monotonic", return_value=10.5):
            self.assertTrue(visual._taskbar_left_handle_visible())
        with patch("local.island_stack_visual.time.monotonic", return_value=13.2):
            self.assertFalse(visual._taskbar_left_handle_visible())

    def test_taskbar_left_handle_default_color_is_gray_and_configurable(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_left_handle_color = "#8E8E93"
        visual._effective_music_ambience_style = lambda: "pure_black"

        default_color = visual._taskbar_left_handle_color()
        visual.taskbar_left_handle_color = "#FF3366"
        custom_color = visual._taskbar_left_handle_color()

        self.assertEqual(default_color, QColor("#8E8E93"))
        self.assertEqual(custom_color, QColor("#FF3366"))

    def test_tasker_color_fill_does_not_restrict_music_background_style(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.lyric_flow_style = "wave_stream"
        visual.wave_color_fill_enabled = True
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        visual.set_music_ambience_style("sea_level", notify=False)

        self.assertEqual(visual.music_ambience_style, "sea_level")
        self.assertEqual(visual._effective_music_ambience_style(), "sea_level")

    def test_gelv_color_fill_does_not_restrict_music_background_style(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "free"
        visual.music_ambience_style = "pure_black"
        visual.lyric_flow_style = "gelv_songscatter"
        visual.gelv_color_fill_enabled = True
        visual._pulse_stack = lambda: None
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        visual.set_music_ambience_style("sea_level", notify=False)

        self.assertEqual(visual.music_ambience_style, "sea_level")
        self.assertEqual(visual._effective_music_ambience_style(), "sea_level")

    def test_enabling_gelv_color_fill_keeps_selected_music_background_style(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "free"
        visual.music_ambience_style = "duotone_full"
        visual.lyric_flow_style = "gelv_songscatter"
        visual.gelv_color_fill_enabled = False
        visual._pulse_stack = lambda: None
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        visual.set_gelv_color_fill_enabled(True, notify=False)

        self.assertEqual(visual.music_ambience_style, "duotone_full")

    def test_music_auto_detect_hides_without_real_audio_even_when_metadata_exists(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = True
        visual.music_auto_detect = True
        visual.live_audio_available = False
        visual.media_is_playing = True
        visual.media_source_app = "NetEase"
        visual.track_title = "Song"
        visual._estimated_media_position = lambda _now: 42.0

        self.assertFalse(visual._music_enabled_for_stack())

    def test_taskbar_time_due_alert_overlays_main_rect(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [
            (0, "TIME", "0:00", "时间到", QColor(255, 184, 0), "", "time:countdown", "done", "倒计时", "时间到", 0)
        ]
        main_rect = QRectF(120.0, 30.0, 280.0, 40.0)

        alert = visual._taskbar_main_overlay_alert()
        rect = visual._taskbar_main_overlay_rect(main_rect, alert)

        self.assertIsNotNone(alert)
        self.assertEqual(rect, main_rect)

    def test_tasker_left_handle_drag_adjusts_width_without_exceeding_track(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        main_rect = QRectF(320.0, 1044.0, 260.0, 34.0)
        track = QRectF(100.0, 1040.0, 820.0, 40.0)

        expanded_width = visual._taskbar_manual_width_for_left_edge(main_rect, track, 240.0)
        clamped_width = visual._taskbar_manual_width_for_left_edge(main_rect, track, -200.0)

        self.assertGreater(expanded_width, main_rect.width())
        self.assertLessEqual(clamped_width, track.width())
        self.assertGreaterEqual(clamped_width, 120.0)

    def test_taskbar_drop_overlay_is_pure_drawn_without_text_label(self):
        self.assertFalse(hasattr(TaskbarDropOverlay, "LABEL"))
        self.assertNotIn("drawText", inspect.getsource(TaskbarDropOverlay.paintEvent))

    def test_taskbar_drop_overlay_accepts_multiple_zones(self):
        overlay = TaskbarDropOverlay()
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        zones = [QRectF(190.0, 1044.0, 360.0, 32.0), QRectF(1460.0, 1044.0, 320.0, 32.0)]

        overlay.show_zones(taskbar, zones, 6.0)

        self.assertEqual(len(overlay.drop_rects), 2)
        overlay.hide()

    def test_taskbar_preview_does_not_create_drop_overlay_frame(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_preview_visible = True
        visual.taskbar_drop_overlay = None
        visual._taskbar_allowed = lambda: True
        visual._hide_taskbar_drop_overlay = lambda: None
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(1200, 1072)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual.taskbar_drop_zones = [QRectF(190.0, 1044.0, 360.0, 32.0)]
        visual._u = lambda value: float(value)

        with patch("local.island_stack_visual.TaskbarDropOverlay") as overlay_class:
            visual._sync_taskbar_drop_overlay()

        overlay_class.assert_not_called()

    def test_taskbar_dock_uses_current_probe_position_without_slot_magnet(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_selected_drop_zone = QRectF(100.0, 1044.0, 320.0, 32.0)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(1500, 1072)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, _taskbar: QRectF(180.0, 1040.0, 1500.0, 40.0)
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_kind = lambda _kind, _stack_w: (300.0, 34.0)
        visual._main_top_for_direction = lambda *_args: 10.0
        visual.move = lambda _pos: None

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._dock_to_taskbar_task_area())

        self.assertGreater(visual.taskbar_position_ratio, 0.75)

    def test_taskbar_compact_plugin_style_routes_to_rail_or_block(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_fusion_mode = "compact"
        visual.plugin_samples = [("TIME", "计时", "10:00", QColor(255, 204, 0), "T", "time", "running", "", "", 30, 0.4)]
        calls: list[str] = []
        visual._active_plugin_alerts = lambda *_args, **_kwargs: []
        visual._plugin_index_for_id = lambda _plugin_id: 0
        visual._plugin_tuple = lambda plugin: plugin
        visual._plugin_theme_colors = lambda: {"text": QColor(255, 255, 255), "alert": QColor(0, 0, 0), "stroke": QColor(0, 0, 0)}
        visual._pure_plugin_color = lambda color: color
        visual._plugin_display_color = lambda _label, color: color
        visual._paint_taskbar_plugin_rail = lambda *_args, **_kwargs: calls.append("rail")
        visual._paint_taskbar_plugin_block = lambda *_args, **_kwargs: calls.append("block")

        visual.taskbar_plugin_compact_style = "rail"
        visual._paint_plugin_alert(QPainter(), QRectF(0, 0, 8, 34), "plugin-alert:time")
        visual.taskbar_plugin_compact_style = "block"
        visual._paint_plugin_alert(QPainter(), QRectF(0, 0, 34, 34), "plugin-alert:time")

        self.assertEqual(calls, ["rail", "block"])

    def test_taskbar_plugin_rail_segments_use_black_and_theme_ratio(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        rect = QRectF(0.0, 0.0, 6.0, 42.0)

        segments = visual._taskbar_plugin_rail_segments(rect, QColor(50, 160, 255), "running", 0.25)

        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["color"], QColor(0, 0, 0, 255))
        self.assertEqual(segments[1]["color"], QColor(50, 160, 255, 235))
        self.assertAlmostEqual(segments[1]["rect"].height(), rect.height() * 0.25, delta=0.1)

    def test_taskbar_material_classifies_light_and_uses_pure_white_background(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.music_ambience_style = "sea_level"
        visual.taskbar_material_is_light = True

        self.assertTrue(visual._taskbar_material_is_light(QColor(242, 244, 241)))
        self.assertEqual(visual._effective_music_ambience_style(), "pure_white")

    def test_pure_white_music_background_uses_dark_lyric_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.music_ambience_style = "pure_white"

        self.assertEqual(visual._music_lyric_text_color().black(), 255)

    def test_pure_white_music_background_remains_available_with_color_fill_enabled(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "sea_level"
        visual.lyric_flow_style = "wave_stream"
        visual.wave_color_fill_enabled = True
        visual._pulse_stack = lambda: None
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        visual.set_music_ambience_style("pure_white", notify=False)

        self.assertEqual(visual.music_ambience_style, "pure_white")
        self.assertEqual(visual._music_lyric_text_color().black(), 255)

    def test_pure_white_expanded_music_title_and_artist_use_dark_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "pure_white"
        visual.track_title = "Title"
        visual.artist = "Artist"
        visual.media_is_playing = True
        visual.music_button_rects = {}
        visual._u = lambda value: float(value)
        visual._font_size = lambda value: int(value)
        visual._should_paint_album_island = lambda: False
        visual._paint_cover = lambda *_args, **_kwargs: None
        visual._paint_lyric_viewport = lambda *_args, **_kwargs: None
        colors: list[QColor] = []
        visual._paint_scrolling_text = lambda _painter, _rect, _text, _size, color, *_args, **_kwargs: colors.append(QColor(color))
        image = QImage(520, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music_expanded(painter, QRectF(12.0, 12.0, 480.0, 176.0))
        painter.end()

        self.assertEqual([color.black() for color in colors[:2]], [255, 255])

    def test_pure_white_album_island_expanded_title_and_artist_use_dark_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "pure_white"
        visual.track_title = "Title"
        visual.artist = "Artist"
        visual.media_is_playing = True
        visual.music_button_rects = {}
        visual._u = lambda value: float(value)
        visual._font_size = lambda value: int(value)
        visual._album_island_slots = lambda *_args, **_kwargs: []
        visual._paint_album_island_slots = lambda *_args, **_kwargs: None
        visual._paint_album_island_listened_text = lambda *_args, **_kwargs: None
        colors: list[QColor] = []
        visual._paint_scrolling_text = lambda _painter, _rect, _text, _size, color, *_args, **_kwargs: colors.append(QColor(color))
        image = QImage(520, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_album_island_expanded(painter, QRectF(12.0, 12.0, 480.0, 176.0))
        painter.end()

        self.assertEqual([color.black() for color in colors[:2]], [255, 255])

    def test_pure_white_wave_stream_uses_black_chunks_without_fill_or_highlight(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "pure_white"
        visual.wave_color_fill_enabled = True
        visual.wave_color_fill_fallback = "#00D7FF"
        visual._u = lambda value: float(value)
        visual._lyric_layout = lambda *_args, **_kwargs: LyricLayout("hello", ("hello",), (42.0,), 42.0, ())
        visual._lyric_progress_width = lambda _layout, _progress: 21.0
        visual._scrolling_text_x = lambda *_args, **_kwargs: 0.0
        visual._accent_pair = lambda: (QColor(255, 0, 0), QColor(0, 0, 255))
        visual._wave_fill_color = lambda: QColor(0, 215, 255)
        calls = []
        visual._paint_wave_stream_lyric_chunks = (
            lambda _painter, _rect, _x, _layout, _size, _bold, base, live, _progress, _confidence, glow=False:
            calls.append((QColor(base), QColor(live), bool(glow)))
        )
        image = QImage(220, 80, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_live_lyric_text(
            painter,
            QRectF(10.0, 10.0, 180.0, 36.0),
            "hello",
            16,
            QColor(0, 0, 0, 132),
            QColor(0, 0, 0, 246),
            True,
            {"progress": 0.5, "confidence": 1.0},
        )
        painter.end()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0].black(), 255)
        self.assertEqual(calls[0][1].black(), 255)
        self.assertFalse(calls[0][2])

    def test_pure_white_gelv_line_uses_black_text_without_color_fill(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "pure_white"
        visual.gelv_color_fill_enabled = True
        visual._gelv_layout = lambda *_args, **_kwargs: LyricLayout("hello", ("hello",), (42.0,), 42.0, ())
        visual._gelv_line_x = lambda *_args, **_kwargs: 0.0
        visual._gelv_spacing_percent = lambda _progress: 100
        captured: list[tuple[QColor, QColor]] = []
        visual._paint_gelv_songscatter_chunks = (
            lambda _painter, _rect, _x, _layout, _size, base, live, *_args, **_kwargs:
            captured.append((QColor(base), QColor(live)))
        )
        image = QImage(220, 80, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_gelv_line(painter, QRectF(10.0, 10.0, 180.0, 36.0), "hello", 16.0, 0.5, True, color_fill=True)
        painter.end()

        self.assertEqual(captured[0][0].black(), 255)
        self.assertEqual(captured[0][1].black(), 255)

    def test_enabled_plugin_ids_filter_plugin_samples_without_count_slider(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enabled_plugin_ids = {"time", "agenda"}
        visual.enabled_plugin_count = 3
        visual.plugin_samples = [
            ("TIME", "", "", QColor(), "T", "time", "normal", "", "", 0, 0.0),
            ("WEATHER", "", "", QColor(), "W", "weather", "normal", "", "", 0, 0.0),
            ("AGENDA", "", "", QColor(), "A", "agenda", "normal", "", "", 0, 0.0),
        ]

        samples = visual._enabled_plugin_samples()

        self.assertEqual([sample[5] for sample in samples], ["time", "agenda"])

    def test_congested_taskbar_slot_shrinks_music_to_cover_only_size(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual._u = lambda value: float(value)
        visual.taskbar_selected_drop_zone = QRectF(0.0, 0.0, 58.0, 40.0)

        width, height = visual._taskbar_compact_music_size(420.0, 44.0)

        self.assertLessEqual(width, 58.0)
        self.assertLessEqual(height, 44.0)

    def test_taskbar_message_rects_use_user_style_and_collapse_blocks_when_crowded(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_message_compact_style = "block"
        visual._u = lambda value: float(value)
        main_rect = QRectF(500.0, 1044.0, 280.0, 34.0)

        block = visual._taskbar_message_rect(main_rect, 0, 2, 1)
        rail = visual._taskbar_message_rect(main_rect, 0, 5, 1)

        self.assertAlmostEqual(block.width(), block.height(), delta=0.1)
        self.assertLess(rail.width(), rail.height())

    def test_tasker_music_cover_radius_matches_outer_radius(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_fusion_mode = "compact"
        visual.taskbar_corner_style = "win11"
        visual._u = lambda value: float(value)
        rect = QRectF(0.0, 0.0, 280.0, 40.0)

        self.assertEqual(
            visual._compact_cover_radius(rect, two_line=False),
            visual._music_shell_radius(rect, expanded=False),
        )

    def test_expanded_tasker_music_uses_windows_corner_radius(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_fusion_mode = "compact"
        visual.taskbar_corner_style = "win11"
        visual._u = lambda value: float(value)
        rect = QRectF(0.0, 0.0, 520.0, 176.0)

        self.assertLess(visual._music_shell_radius(rect, expanded=True), 30.0)

    def test_taskbar_compact_layout_uses_tighter_height_and_keeps_all_surfaces_inside_bounds(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_fusion_mode = "compact"
        visual.taskbar_corner_style = "win11"

        width, height = visual._taskbar_compact_music_size(420.0, 44.0)

        self.assertLessEqual(height, 44.0)
        self.assertGreater(width, 0.0)

    def test_taskbar_corner_style_switches_between_win10_and_win11(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertEqual(visual._taskbar_corner_radius(44.0, "win10"), 0.0)
        self.assertGreater(visual._taskbar_corner_radius(44.0, "win11"), 0.0)

    def test_taskbar_expanded_panel_uses_outer_material_frame_without_replacing_the_shell(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = True
        visual.finder_mode = False
        visual.taskbar_corner_style = "win11"

        self.assertTrue(visual._taskbar_uses_expanded_material_frame())
        self.assertTrue(visual._taskbar_shell_remains_opaque())

    def test_taskbar_expanded_panel_uses_windows_corner_policy(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_corner_style = "win10"

        self.assertEqual(visual._taskbar_panel_corner_policy(), "square")

    def test_gelv_songscatter_font_family_is_source_han_serif_only(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        families = visual._gelv_font_families()

        self.assertEqual(families, ["Source Han Serif SC", "PingFang SC"])

    def test_gelv_font_can_use_pingfang_when_selected(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_font_family = "PingFang SC"

        families = visual._gelv_font_families()

        self.assertEqual(families[0], "PingFang SC")
        self.assertIn("Source Han Serif SC", families)

    def test_source_han_serif_asset_paths_are_embedded_assets_only(self):
        paths = IslandStackVisual._source_han_serif_asset_paths()

        self.assertTrue(any(path.name == "SourceHanSerifSC-Heavy.otf" for path in paths))
        self.assertTrue(all("D:\\DownLoad\\Chrome\\09_SourceHanSerifSC" not in str(path) for path in paths))

    def test_gelv_font_defaults_to_bold_weight(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        font = visual._gelv_font(18)

        self.assertGreaterEqual(font.weight(), QFont.Weight.Bold)

    def test_default_ui_font_is_pingfang(self):
        self.assertEqual(IslandStackVisual.DEFAULT_FONT_FAMILY, "PingFang SC")

    def test_ui_font_fallbacks_are_limited_to_pingfang_and_embedded_serif(self):
        self.assertEqual(IslandStackVisual.CHINESE_FONT_FALLBACKS, ("PingFang SC", "Source Han Serif SC"))

    def test_gelv_chunks_keep_punctuation_with_previous_unit(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertEqual(visual._gelv_chunks("你好，world again!"), ["你", "好，", "world", "again!"])

    def test_gelv_split_long_cjk_line_gives_upper_extra_unit(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        split = visual._gelv_split_line("一二三四五六七", threshold=4)

        self.assertEqual(split, ("一二三四", "五六七"))

    def test_gelv_split_long_english_line_does_not_split_words(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        split = visual._gelv_split_line("one two three four five", threshold=4)

        self.assertEqual(split, ("one two three", "four five"))

    def test_gelv_short_line_does_not_split(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertIsNone(visual._gelv_split_line("短句", threshold=8))

    def test_gelv_mixed_chinese_english_line_splits_into_two_rows(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True
        visual.lyrics = ["A prayer for you my friend 为你祷告我的朋友", "下一句"]
        visual.lyric_index = 0

        lines = visual._gelv_display_lines()

        self.assertEqual(lines, ["A prayer for you my friend", "为你祷告我的朋友"])

    def test_gelv_line_sizes_keep_ten_px_difference(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_font_size = 22

        active, next_size = visual._gelv_line_sizes(taskbar=False)

        self.assertEqual(active, 22)
        self.assertEqual(next_size, 12)

    def test_gelv_line_sizes_use_independent_primary_and_secondary_settings(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_font_size = 18
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 13
        visual.gelv_size_shift_enabled = True

        active, next_size = visual._gelv_line_sizes(taskbar=False)

        self.assertEqual(active, 24)
        self.assertEqual(next_size, 13)

    def test_gelv_secondary_font_size_applies_when_size_shift_is_disabled(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 10
        visual.gelv_size_shift_enabled = False

        active, next_size = visual._gelv_line_sizes(taskbar=False)

        self.assertEqual(active, 24)
        self.assertEqual(next_size, 10)

    def test_gelv_effect_layout_ignores_wave_stream_effect_settings(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_initial_word_spacing = 100
        visual.gelv_word_spacing = 132
        visual.lyric_elastic_strength = 0
        visual.lyric_word_spacing = 60
        base_key = visual._gelv_layout_key("one two", 18)

        visual.lyric_elastic_strength = 160
        visual.lyric_word_spacing = 150
        wave_changed_key = visual._gelv_layout_key("one two", 18)
        visual.gelv_word_spacing = 108
        gelv_changed_key = visual._gelv_layout_key("one two", 18)

        self.assertEqual(base_key, wave_changed_key)
        self.assertNotEqual(base_key, gelv_changed_key)

    def test_gelv_spacing_progress_starts_from_configured_initial_spacing(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_initial_word_spacing = 180
        visual.gelv_word_spacing = 300

        self.assertEqual(visual._gelv_spacing_percent(0.0), 180)
        self.assertEqual(visual._gelv_spacing_percent(0.5), 240)
        self.assertEqual(visual._gelv_spacing_percent(1.0), 300)

    def test_gelv_spacing_target_is_never_tighter_than_initial_spacing(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_initial_word_spacing = 220
        visual.gelv_word_spacing = 160

        self.assertEqual(visual._gelv_spacing_percent(0.0), 220)
        self.assertEqual(visual._gelv_spacing_percent(1.0), 220)

    def test_gelv_initial_spacing_is_visible_for_english_words(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_font_family = "Source Han Serif SC"
        visual.gelv_initial_word_spacing = 100
        visual.gelv_word_spacing = 100
        tight = visual._gelv_layout_width("she said it still us", 18, spacing_progress=0.0)
        visual.gelv_initial_word_spacing = 180
        visual.gelv_word_spacing = 260
        loose_initial = visual._gelv_layout_width("she said it still us", 18, spacing_progress=0.0)

        self.assertGreaterEqual(loose_initial - tight, 28.0)

    def test_gelv_spacing_below_250_is_already_obvious_for_english_words(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_font_family = "Source Han Serif SC"
        visual.gelv_initial_word_spacing = 100
        visual.gelv_word_spacing = 100

        tight = visual._gelv_layout_width("gone go OD", 18, spacing_progress=0.0)
        visual.gelv_initial_word_spacing = 220
        visual.gelv_word_spacing = 240
        loose = visual._gelv_layout_width("gone go OD", 18, spacing_progress=0.0)

        self.assertGreaterEqual(loose - tight, 22.0)

    def test_gelv_word_spacing_is_visibly_large_for_cjk_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_font_family = "Source Han Serif SC"
        visual.gelv_initial_word_spacing = 100
        visual.gelv_word_spacing = 100
        tight = visual._gelv_layout_width("天地玄黄", 22, spacing_progress=1.0)
        visual.gelv_initial_word_spacing = 180
        visual.gelv_word_spacing = 220
        loose = visual._gelv_layout_width("天地玄黄", 22, spacing_progress=1.0)

        self.assertGreaterEqual(loose - tight, 18.0)

    def test_gelv_compact_music_size_stays_tight_for_two_rows(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = False
        visual.lyric_flow_style = "gelv_songscatter"
        visual.lyric_font_size = 22
        visual.lyrics = ["当前歌词", "下一句"]
        visual.lyric_index = 0

        _width, height = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)

        self.assertLessEqual(height, 68.0 * IslandStackVisual.VISUAL_SCALE)

    def test_gelv_compact_rows_leave_room_for_english_descenders(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_font_family = "Source Han Serif SC"
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 13
        visual.lyrics = ["young glory", "gone by"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 58.0), expanded=False)

        for row in rows:
            metrics = QFontMetrics(visual._gelv_font(float(row["size"])))
            self.assertGreaterEqual(row["rect"].height(), metrics.height() + 1.0)

    def test_gelv_compact_music_size_adapts_to_independent_font_settings(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = False
        visual.lyric_flow_style = "gelv_songscatter"
        visual.gelv_size_shift_enabled = True
        visual.lyrics = ["current lyric", "next lyric"]
        visual.lyric_index = 0

        visual.gelv_primary_font_size = 18
        visual.gelv_secondary_font_size = 9
        _small_w, small_h = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)
        visual.gelv_primary_font_size = 28
        visual.gelv_secondary_font_size = 18
        _large_w, large_h = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)

        self.assertGreater(large_h, small_h)

    def test_gelv_compact_rows_are_tighter_than_expanded_rows(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.lyric_font_size = 22
        visual.lyrics = ["当前歌词", "下一句"]
        visual.lyric_index = 0

        compact = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 44.0), expanded=False)
        expanded = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 132.0), expanded=True)

        compact_gap = compact[1]["rect"].top() - compact[0]["rect"].bottom()
        expanded_gap = expanded[1]["rect"].top() - expanded[0]["rect"].bottom()
        self.assertLessEqual(compact_gap, IslandStackVisual.VISUAL_SCALE * 2.0)
        self.assertGreater(expanded_gap, compact_gap)

    def test_gelv_explicit_dual_line_hides_next_line_and_uses_configured_sizes(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.enable_dual_line_lyrics = True
        visual.gelv_size_shift_enabled = True
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 13
        visual.gelv_dual_line_font_size = 18
        visual.lyrics = ["原句歌词\n翻译歌词", "下一句不该出现"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 44.0), expanded=False)

        self.assertEqual([row["text"] for row in rows], ["原句歌词", "翻译歌词"])
        self.assertEqual(rows[0]["size"], 24)
        self.assertEqual(rows[1]["size"], 18)
        self.assertTrue(all(row["active"] for row in rows))

    def test_gelv_single_current_line_keeps_next_line_secondary_size(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.enable_dual_line_lyrics = True
        visual.gelv_size_shift_enabled = True
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 11
        visual.gelv_dual_line_font_size = 19
        visual.lyrics = ["单行当前", "下一句"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 44.0), expanded=False)

        self.assertEqual([row["text"] for row in rows], ["单行当前", "下一句"])
        self.assertEqual(rows[0]["size"], 24)
        self.assertEqual(rows[1]["size"], 11)
        self.assertFalse(rows[1]["active"])

    def test_gelv_dual_line_switch_off_uses_single_current_line(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.enable_dual_line_lyrics = False
        visual.gelv_size_shift_enabled = True
        visual.lyric_font_size = 22
        visual.lyrics = ["原句歌词\n翻译歌词", "下一句"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 44.0), expanded=False)

        self.assertEqual([row["text"] for row in rows], ["原句歌词", "下一句"])
        self.assertFalse(all(row["active"] for row in rows))

    def test_gelv_mixed_language_rows_are_synced_current_pair(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.enable_dual_line_lyrics = True
        visual.gelv_size_shift_enabled = True
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 13
        visual.gelv_dual_line_font_size = 18
        visual.lyrics = ["A prayer for you my friend 为你祷告我的朋友", "下一句"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 44.0), expanded=False)

        self.assertEqual([row["text"] for row in rows], ["A prayer for you my friend", "为你祷告我的朋友"])
        self.assertEqual(rows[0]["size"], 24)
        self.assertEqual(rows[1]["size"], 18)
        self.assertTrue(all(row["active"] for row in rows))

    def test_gelv_mixed_language_switch_off_keeps_single_line(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.enable_dual_line_lyrics = False
        visual.gelv_size_shift_enabled = True
        visual.lyric_font_size = 22
        visual.lyrics = ["A prayer for you my friend 为你祷告我的朋友", "下一句"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 44.0), expanded=False)

        self.assertEqual([row["text"] for row in rows], ["A prayer for you my friend 为你祷告我的朋友", "下一句"])
        self.assertFalse(all(row["active"] for row in rows))

    def test_gelv_size_shift_toggle_does_not_disable_configured_secondary_size(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.enable_dual_line_lyrics = True
        visual.gelv_size_shift_enabled = False
        visual.gelv_primary_font_size = 22
        visual.gelv_secondary_font_size = 11
        visual.gelv_dual_line_font_size = 16
        visual.lyrics = ["原句歌词\n翻译歌词", "下一句"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 44.0), expanded=False)

        self.assertEqual(rows[0]["size"], 22)
        self.assertEqual(rows[1]["size"], 16)

    def test_wave_stream_and_gelv_use_sibling_chunk_painters(self):
        self.assertTrue(hasattr(IslandStackVisual, "_paint_wave_stream_lyric_chunks"))
        self.assertTrue(hasattr(IslandStackVisual, "_paint_gelv_songscatter_chunks"))

    def test_gelv_songscatter_chunks_do_not_scale_focused_words(self):
        class FakePainter:
            def __init__(self):
                self.scale_calls = []

            def setFont(self, *_args):
                pass

            def save(self):
                pass

            def translate(self, *_args):
                pass

            def scale(self, x, y):
                self.scale_calls.append((x, y))

            def setPen(self, *_args):
                pass

            def drawText(self, *_args):
                pass

            def restore(self):
                pass

        visual = IslandStackVisual.__new__(IslandStackVisual)
        layout = LyricLayout(
            text="one two three",
            chunks=("one", "two", "three"),
            widths=(24.0, 24.0, 24.0),
            total_width=72.0,
            font_key=(),
        )
        painter = FakePainter()

        visual._paint_gelv_songscatter_chunks(
            painter,
            QRectF(0.0, 0.0, 120.0, 28.0),
            0.0,
            layout,
            18,
            QColor(255, 255, 255, 120),
            QColor(255, 255, 255, 250),
            0.5,
            True,
        )

        self.assertEqual(painter.scale_calls, [])

    def test_gelv_transition_animates_without_vertical_float(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_changed_at = 10.0
        visual.prev_lyric_index = 0
        visual.lyric_index = 1
        visual.gelv_size_shift_enabled = True

        state = visual._gelv_transition_state(now=10.12)

        self.assertGreater(state["out_alpha"], 0.0)
        self.assertEqual(state["out_y_offset"], 0.0)
        self.assertEqual(state["current_y_offset"], 0.0)
        self.assertGreater(state["current_size_mix"], 0.0)
        self.assertLess(state["current_size_mix"], 1.0)

    def test_gelv_size_shift_toggle_controls_current_line_size_animation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_changed_at = 10.0
        visual.prev_lyric_index = 0
        visual.lyric_index = 1
        visual.lyrics = ["first", "second", "third"]
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 12
        visual.gelv_size_shift_enabled = True

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 58.0), expanded=False, now=10.08)
        incoming = next(row for row in rows if row["text"] == "second" and not row.get("transition_out"))

        self.assertGreater(incoming["size"], 12)
        self.assertLess(incoming["size"], 24)

        visual.gelv_size_shift_enabled = False
        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 58.0), expanded=False, now=10.08)
        incoming = next(row for row in rows if row["text"] == "second" and not row.get("transition_out"))

        self.assertEqual(incoming["size"], 24)

    def test_gelv_line_switch_moves_next_line_up_instead_of_only_fading(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_changed_at = 10.0
        visual.prev_lyric_index = 0
        visual.lyric_index = 1
        visual.lyrics = ["first", "second", "third"]
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 12
        visual.gelv_size_shift_enabled = True
        rect = QRectF(0.0, 0.0, 300.0, 58.0)

        final_rows = visual._gelv_line_rects(rect, expanded=False, now=10.7)
        moving_rows = visual._gelv_line_rects(rect, expanded=False, now=10.08)
        final_current = next(row for row in final_rows if row["text"] == "second" and not row.get("transition_out"))
        final_next = next(row for row in final_rows if row["text"] == "third")
        moving_current = next(row for row in moving_rows if row["text"] == "second" and not row.get("transition_out"))

        self.assertGreater(moving_current["rect"].top(), final_current["rect"].top() + 1.0)
        self.assertLess(moving_current["rect"].top(), final_next["rect"].top())

    def test_gelv_color_fill_marks_only_primary_row(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True
        visual.lyrics = ["Original lyric\nTranslated lyric", "next"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 58.0), expanded=False)

        self.assertEqual([row["color_fill"] for row in rows], [True, False])

    def test_gelv_secondary_row_uses_plain_spacing_not_current_spread(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyrics = ["current line", "next line"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 58.0), expanded=False)

        self.assertIsNone(rows[0]["spacing_percent"])
        self.assertEqual(rows[1]["spacing_percent"], 100)
        self.assertFalse(rows[1].get("progress_fill", False))

    def test_gelv_dual_line_second_row_uses_songscatter_progress_without_color_fill(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True
        visual.lyrics = ["Original lyric\nTranslated lyric", "next line"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 58.0), expanded=False)

        self.assertIsNone(rows[0]["spacing_percent"])
        self.assertIsNone(rows[1]["spacing_percent"])
        self.assertTrue(rows[1]["active"])
        self.assertFalse(rows[1]["color_fill"])
        self.assertTrue(rows[1]["progress_fill"])

    def test_gelv_mixed_language_second_row_uses_songscatter_progress(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True
        visual.lyrics = ["A prayer for you my friend 为你祈祷我的朋友", "next line"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 58.0), expanded=False)

        self.assertEqual([row["text"] for row in rows], ["A prayer for you my friend", "为你祈祷我的朋友"])
        self.assertIsNone(rows[1]["spacing_percent"])
        self.assertTrue(rows[1]["progress_fill"])

    def test_gelv_dual_line_progress_fill_uses_white_highlight_not_album_color(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_color_fill_enabled = True
        visual.gelv_color_fill_fallback = "#00D7FF"
        visual.album_color = QColor(255, 0, 0)
        calls = []
        visual._gelv_layout = lambda text, size, spacing_percent: LyricLayout(
            text=text,
            chunks=("one", "two"),
            widths=(20.0, 20.0),
            total_width=40.0,
            font_key=(),
        )
        visual._gelv_line_x = lambda *_args, **_kwargs: 0.0
        visual._paint_gelv_songscatter_chunks = (
            lambda _painter, _rect, _x, _layout, _size, base, live, _progress, _active: calls.append(
                {"base": QColor(base), "live": QColor(live)}
            )
        )

        visual._paint_gelv_line(
            None,
            QRectF(0.0, 0.0, 120.0, 28.0),
            "one two",
            18.0,
            0.5,
            active=True,
            color_fill=False,
            progress_fill=True,
        )

        self.assertEqual(calls[0]["base"].getRgb()[:3], (255, 255, 255))
        self.assertEqual(calls[0]["live"].getRgb()[:3], (255, 255, 255))
        self.assertLess(calls[0]["base"].alpha(), calls[0]["live"].alpha())

    def test_wave_stream_color_fill_tints_live_highlight(self):
        class FakePainter:
            def save(self):
                pass

            def restore(self):
                pass

            def setClipRect(self, *_args):
                pass

            def setPen(self, *_args):
                pass

            def setBrush(self, *_args):
                pass

            def drawEllipse(self, *_args):
                pass

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.wave_color_fill_enabled = True
        visual.wave_color_fill_fallback = "#00D7FF"
        visual.album_color = QColor(120, 120, 120)
        visual.secondary_color = QColor(100, 100, 100)
        calls = []

        def capture_chunks(_painter, _rect, _x, _layout, _size, _bold, base, live, _progress, _confidence, glow=False):
            calls.append({"base": QColor(base), "live": QColor(live), "glow": glow})

        visual._paint_wave_stream_lyric_chunks = capture_chunks

        visual._paint_live_lyric_text(
            FakePainter(),
            QRectF(0.0, 0.0, 240.0, 32.0),
            "gone go OD",
            18,
            QColor(255, 255, 255, 120),
            QColor(255, 255, 255, 240),
            True,
            {"progress": 0.5, "confidence": 0.8},
            centered=True,
            centered_scroll=True,
        )

        live_call = next(call for call in calls if not call["glow"])
        self.assertEqual(live_call["live"].name().upper(), "#00D7FF")
        self.assertNotEqual(live_call["base"].red(), live_call["base"].green())

    def test_gelv_long_line_uses_synced_scroll_position(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        x_start = visual._gelv_line_x(QRectF(0.0, 0.0, 90.0, 24.0), "one two three four five six seven", 18, progress=0.0)
        x_late = visual._gelv_line_x(QRectF(0.0, 0.0, 90.0, 24.0), "one two three four five six seven", 18, progress=0.85)

        self.assertLess(x_late, x_start)

    def test_compact_lyric_viewport_routes_to_gelv_renderer(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.lyrics = ["当前歌词", "下一句"]
        visual.lyric_index = 0
        calls = []
        visual._paint_gelv_compact_lyrics = lambda *_args: calls.append("gelv")
        image = QImage(300, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_compact_lyric_viewport(painter, QRectF(0.0, 0.0, 260.0, 70.0))
        painter.end()

        self.assertEqual(calls, ["gelv"])

    def test_gelv_spacing_progress_uses_actual_line_duration(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.timed_lyrics = [(0.0, "a"), (4.0, "b")]
        visual.lyric_index = 0
        visual.lyric_offset_seconds = 0.0
        visual._estimated_media_position = lambda _now: 2.0

        progress = visual._gelv_spacing_progress(99.0)

        self.assertAlmostEqual(progress, 0.5, delta=0.02)

    def test_gelv_spacing_progress_matches_wave_stream_phase(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._lyric_phase = lambda _now: {"progress": 0.73, "confidence": 0.78}

        self.assertAlmostEqual(visual._gelv_spacing_progress(12.0), 0.73, delta=0.001)

    def test_gelv_fill_color_uses_fallback_for_neutral_album_color(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_color = QColor(120, 120, 120)
        visual.gelv_color_fill_fallback = "#00D7FF"

        color = visual._gelv_fill_color()

        self.assertEqual(color.name().upper(), "#00D7FF")

    def test_gelv_fill_color_saturates_album_color(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_color = QColor(80, 120, 170)
        visual.gelv_color_fill_fallback = "#00D7FF"

        color = visual._gelv_fill_color()

        self.assertGreater(color.saturation(), visual.album_color.saturation())

    def test_album_island_cover_count_allows_only_odd_choices(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.update = lambda: None
        visual._emit_settings_changed = lambda *_args: None

        visual.set_album_island_cover_count(4, notify=False)
        self.assertEqual(visual.album_island_cover_count, 5)
        visual.set_album_island_cover_count(7, notify=False)
        self.assertEqual(visual.album_island_cover_count, 7)

    def test_should_use_album_island_when_no_lyrics_mode_selected(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.no_lyrics_display_mode = "album_island"
        visual.lyrics = ["暂无歌词"]
        visual.no_lyrics_text = "暂无歌词"

        self.assertTrue(visual._should_paint_album_island())

    def test_should_use_album_island_when_selected_as_lyric_flow_style(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "album_island"
        visual.no_lyrics_display_mode = "text"
        visual.lyrics = ["这是一句真实歌词", "下一句歌词"]
        visual.no_lyrics_text = "暂无歌词"

        self.assertTrue(visual._should_paint_album_island())

    def test_listened_hours_text_has_no_decimals(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.listened_seconds_total = 3660 * 12.4

        self.assertEqual(visual._album_island_listened_text(), "您已听歌 12 小时")

    def test_album_history_moves_previous_track_left_on_change(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_history = []
        visual.media_track_id = "old"
        visual.track_title = "Old"
        visual.album_art = QImage()

        visual._remember_album_history("new")

        self.assertEqual(visual.album_history[0]["track_id"], "old")

    def test_album_history_previous_track_change_moves_old_current_to_right(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        previous_image = QImage(8, 8, QImage.Format.Format_ARGB32)
        previous_image.fill(QColor(210, 80, 45))
        current_image = QImage(8, 8, QImage.Format.Format_ARGB32)
        current_image.fill(QColor(10, 120, 210))
        visual.album_history = [{"track_id": "previous", "title": "Previous", "album_art": previous_image}]
        visual.album_queue = []
        visual.media_track_id = "current"
        visual.track_title = "Current"
        visual.album_art = current_image

        direction = visual._prepare_album_flow_for_track_change("previous")

        self.assertEqual(direction, -1)
        self.assertEqual(visual.album_queue[0]["track_id"], "current")
        self.assertEqual(visual.album_history, [])

    def test_album_island_slots_make_center_dominant(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        old_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_one.fill(QColor(210, 80, 45))
        old_two = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_two.fill(QColor(80, 210, 110))
        next_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        next_one.fill(QColor(230, 160, 80))
        next_two = QImage(8, 8, QImage.Format.Format_ARGB32)
        next_two.fill(QColor(160, 80, 230))
        visual.album_history = [
            {"track_id": "old-one", "album_art": old_one},
            {"track_id": "old-two", "album_art": old_two},
        ]
        visual.album_queue = [
            {"track_id": "next-one", "album_art": next_one},
            {"track_id": "next-two", "album_art": next_two},
        ]

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        center_slot = next(slot for slot in slots if slot["center"])
        left_one = next(slot for slot in slots if slot["side"] == -1)
        left_two = next(slot for slot in slots if slot["side"] == -2)
        right_one = next(slot for slot in slots if slot["side"] == 1)
        center = center_slot["rect"]
        self.assertGreater(center.width(), left_one["rect"].width())
        self.assertGreater(left_one["rect"].width(), left_two["rect"].width())
        self.assertGreater(center_slot["alpha"], left_one["alpha"])
        self.assertIn("perspective", left_one)
        self.assertIn("perspective", right_one)
        center_spacing = center.center().x() - left_one["rect"].center().x()
        self.assertGreater(center_spacing, center.width() * 0.72)

    def test_album_island_secondary_shrink_style_removes_perspective_tilt(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_island_secondary_style = "shrink_dim"
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        old_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_one.fill(QColor(210, 80, 45))
        next_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        next_one.fill(QColor(230, 160, 80))
        visual.album_history = [{"track_id": "old-one", "album_art": old_one}]
        visual.album_queue = [{"track_id": "next-one", "album_art": next_one}]

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        side_slots = [slot for slot in slots if not bool(slot["center"])]
        self.assertTrue(side_slots)
        self.assertTrue(all(abs(float(slot["tilt"])) <= 0.01 for slot in side_slots))

    def test_album_island_secondary_perspective_style_keeps_flatter_side_covers(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_island_secondary_style = "perspective_dim"
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        old_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_one.fill(QColor(210, 80, 45))
        visual.album_history = [{"track_id": "old-one", "album_art": old_one}]
        visual.album_queue = []

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        center = next(slot for slot in slots if slot["center"])
        left = next(slot for slot in slots if slot["side"] == -1)
        self.assertLess(abs(center["rect"].width() - left["rect"].width()), center["rect"].width() * 0.18)
        self.assertEqual(left["tilt"], 0.0)
        self.assertIn("perspective", left)

    def test_album_island_secondary_perspective_uses_horizontal_quad_shape(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 7
        visual.album_island_cover_size = 34
        visual.album_island_spacing = 112
        visual.album_island_secondary_style = "perspective_dim"
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        old_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_one.fill(QColor(210, 80, 45))
        old_two = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_two.fill(QColor(160, 80, 45))
        next_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        next_one.fill(QColor(230, 160, 80))
        visual.album_history = [
            {"track_id": "old-one", "album_art": old_one},
            {"track_id": "old-two", "album_art": old_two},
        ]
        visual.album_queue = [{"track_id": "next-one", "album_art": next_one}]

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        left_one = next(slot for slot in slots if slot["side"] == -1)
        left_two = next(slot for slot in slots if slot["side"] == -2)
        right_one = next(slot for slot in slots if slot["side"] == 1)
        self.assertEqual(left_one["tilt"], 0.0)
        self.assertIn("perspective", left_one)
        self.assertLess(left_one["perspective"]["right_height"], left_one["perspective"]["left_height"])
        self.assertLess(right_one["perspective"]["left_height"], right_one["perspective"]["right_height"])
        self.assertLess(left_two["perspective"]["right_height"], left_one["perspective"]["right_height"])

    def test_album_island_perspective_amount_controls_side_height_delta(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 34
        visual.album_island_spacing = 112
        visual.album_island_secondary_style = "perspective_dim"
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        old_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_one.fill(QColor(210, 80, 45))
        visual.album_history = [{"track_id": "old-one", "album_art": old_one}]
        visual.album_queue = []

        visual.album_island_perspective_amount = 35
        soft_slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))
        visual.album_island_perspective_amount = 155
        strong_slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        soft_left = next(slot for slot in soft_slots if slot["side"] == -1)
        strong_left = next(slot for slot in strong_slots if slot["side"] == -1)
        soft_delta = soft_left["perspective"]["left_height"] - soft_left["perspective"]["right_height"]
        strong_delta = strong_left["perspective"]["left_height"] - strong_left["perspective"]["right_height"]
        self.assertGreater(strong_delta, soft_delta * 2.0)

    def test_album_island_slots_shift_inward_when_left_cover_hits_boundary(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 7
        visual.album_island_cover_size = 44
        visual.album_island_spacing = 170
        visual.album_island_secondary_style = "shrink_dim"
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        visual.album_history = []
        for index in range(3):
            image = QImage(8, 8, QImage.Format.Format_ARGB32)
            image.fill(QColor(210 - index * 30, 80, 45))
            visual.album_history.append({"track_id": f"old-{index}", "album_art": image})
        visual.album_queue = []

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 180.0, 64.0), reserve_text=True)

        leftmost = min(QRectF(slot["rect"]).left() for slot in slots)
        center = next(slot for slot in slots if slot["center"])
        self.assertGreaterEqual(leftmost, -0.01)
        self.assertLessEqual(QRectF(center["rect"]).center().x(), 90.0)

    def test_album_island_slots_use_distinct_album_sources(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        old_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_one.fill(QColor(210, 80, 45))
        old_two = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_two.fill(QColor(80, 210, 110))
        visual.album_history = [
            {"track_id": "old-one", "album_art": old_one},
            {"track_id": "old-two", "album_art": old_two},
        ]

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        center_slot = next(slot for slot in slots if slot["center"])
        left_slots = [slot for slot in slots if int(slot["side"]) < 0]
        self.assertEqual(center_slot["album_art"].cacheKey(), visual.album_art.cacheKey())
        self.assertIn(old_one.cacheKey(), [slot["album_art"].cacheKey() for slot in left_slots if not slot["album_art"].isNull()])
        self.assertNotIn(visual.album_art.cacheKey(), [slot["album_art"].cacheKey() for slot in slots if not slot["center"] and not slot["album_art"].isNull()])

    def test_album_island_slots_skip_empty_album_placeholders(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 7
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        visual.album_history = []
        visual.album_queue = []

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        self.assertEqual([slot["side"] for slot in slots], [0])

    def test_album_island_slots_show_only_real_right_queue_covers(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 7
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        next_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        next_one.fill(QColor(230, 160, 80))
        visual.album_history = []
        visual.album_queue = [{"track_id": "next-one", "album_art": next_one}]

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        self.assertEqual([slot["side"] for slot in slots], [0, 1])
        self.assertEqual(slots[1]["album_art"].cacheKey(), next_one.cacheKey())

    def test_album_island_compact_replaces_standard_music_cover_layout(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.no_lyrics_display_mode = "album_island"
        visual.no_lyrics_text = "no lyrics"
        visual.lyrics = ["no lyrics"]
        visual.lyric_index = 0
        visual.lyric_flow_style = "wave_stream"
        visual.enable_dual_line_lyrics = False
        calls = []
        visual._paint_album_island_compact = lambda *_args: calls.append("album")
        visual._paint_cover = lambda *_args: calls.append("cover")
        visual._paint_compact_lyric_viewport = lambda *_args: calls.append("viewport")
        image = QImage(320, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music_compact(painter, QRectF(12.0, 12.0, 260.0, 54.0))
        painter.end()

        self.assertEqual(calls, ["album"])

    def test_compact_lyric_can_hide_album_cover_and_left_align_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.no_lyrics_display_mode = "text"
        visual.lyric_flow_style = "wave_stream"
        visual.compact_lyric_show_cover = False
        visual.enable_dual_line_lyrics = False
        visual.lyrics = ["line"]
        visual.lyric_index = 0
        visual.track_title = "Song"
        visual.artist = "Artist"
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        calls = []
        visual._paint_cover = lambda *_args: calls.append(("cover",))
        visual._paint_compact_lyric_viewport = lambda _painter, text_rect: calls.append(("viewport", QRectF(text_rect)))
        image = QImage(320, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music_compact(painter, QRectF(12.0, 12.0, 260.0, 54.0))
        painter.end()

        self.assertFalse(any(call[0] == "cover" for call in calls))
        viewport = next(call[1] for call in calls if call[0] == "viewport")
        self.assertLessEqual(viewport.left(), 24.1)

    def test_album_island_expanded_replaces_standard_music_cover_layout(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.no_lyrics_display_mode = "album_island"
        visual.no_lyrics_text = "no lyrics"
        visual.lyrics = ["no lyrics"]
        visual.track_title = "Current Song"
        visual.artist = "Artist"
        visual.media_is_playing = True
        calls = []
        visual._paint_album_island_expanded = lambda *_args: calls.append("album")
        visual._paint_cover = lambda *_args: calls.append("cover")
        visual._paint_scrolling_text = lambda *_args: calls.append("text")
        visual._paint_lyric_viewport = lambda *_args: calls.append("viewport")
        image = QImage(520, 240, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music_expanded(painter, QRectF(18.0, 18.0, 420.0, 160.0))
        painter.end()

        self.assertEqual(calls, ["album"])
        self.assertTrue(getattr(visual, "music_cover_rect", QRectF()).isNull())

    def test_album_island_compact_fallback_paints_heard_hours_without_future_slots(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        previous = QImage(8, 8, QImage.Format.Format_ARGB32)
        previous.fill(QColor(210, 80, 45))
        visual.album_history = [{"track_id": "old-one", "album_art": previous}]
        visual.album_queue = []
        visual.listened_seconds_total = 55 * 3600
        calls = []
        visual._paint_album_island_slot = lambda _painter, slot, _radius: calls.append(("slot", int(slot["side"])))
        visual._paint_album_island_listened_text = lambda _painter, _rect, slots: calls.append(
            ("text", [int(slot["side"]) for slot in slots])
        )
        image = QImage(340, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_album_island_compact(painter, QRectF(12.0, 12.0, 280.0, 56.0))
        painter.end()

        slot_sides = [side for kind, side in calls if kind == "slot"]
        text_calls = [sides for kind, sides in calls if kind == "text"]
        self.assertEqual(slot_sides, [-1, 0])
        self.assertEqual(text_calls, [[-1, 0]])

    def test_album_island_cover_size_and_spacing_are_adjustable(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.update = lambda: None
        visual._emit_settings_changed = lambda *_args: None

        visual.set_album_island_cover_size(34, notify=False)
        visual.set_album_island_spacing(140, notify=False)

        self.assertEqual(visual.album_island_cover_size, 34)
        self.assertEqual(visual.album_island_spacing, 140)

    def test_album_island_spacing_lower_bound_allows_tighter_layout(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.update = lambda: None
        visual._emit_settings_changed = lambda *_args: None

        visual.set_album_island_spacing(48, notify=False)

        self.assertEqual(visual.album_island_spacing, 48)

    def test_apply_visual_data_loads_album_queue_art(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = ""
        visual.media_position_sync_at = 10.0
        visual.media_position_seconds = 0.0
        visual.media_raw_position_seconds = 0.0
        visual.media_raw_position_source = "windows-media"
        visual.lyrics = []
        visual.timed_lyrics = []
        visual.messages = []
        visual.barrage_clock = 0.0
        visual._seen_provider_message_keys = set()
        visual.listened_seconds_total = 0.0
        visual._scaled_album_art_cache = {}
        visual.album_art = QImage()
        visual._estimated_media_position = lambda _now: 0.0
        visual._clear_lyric_metrics_cache = lambda: None
        visual._sync_timed_lyrics = lambda *_args, **_kwargs: None
        queue_bytes = self._png_bytes(QColor(230, 160, 80))

        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("暂无歌词",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
            album_art_bytes=self._png_bytes(QColor(10, 120, 210)),
            track_id="song",
            album_queue=(AlbumQueueEntry("Next", "Artist", album_art_bytes=queue_bytes),),
        )

        visual._apply_visual_data(IslandVisualData(track=track, messages=(), plugins=()), initial=True)

        self.assertEqual(len(visual.album_queue), 1)
        self.assertFalse(visual.album_queue[0]["album_art"].isNull())

    def test_album_queue_from_track_stops_at_first_missing_future_cover(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        cover_bytes = self._png_bytes(QColor(230, 160, 80))

        missing_first = visual._album_queue_from_track(
            (
                AlbumQueueEntry("Missing", "Artist"),
                AlbumQueueEntry("Later", "Artist", album_art_bytes=cover_bytes),
            )
        )
        missing_second = visual._album_queue_from_track(
            (
                AlbumQueueEntry("First", "Artist", album_art_bytes=cover_bytes),
                AlbumQueueEntry("Missing", "Artist"),
                AlbumQueueEntry("Later", "Artist", album_art_bytes=cover_bytes),
            )
        )

        self.assertEqual(missing_first, [])
        self.assertEqual([entry["title"] for entry in missing_second], ["First"])

    def test_light_time_module_shell_uses_neutral_border_not_orange_frame(self):
        class FakePainter:
            def __init__(self):
                self.pens = []

            def setPen(self, pen):
                self.pens.append(pen)

            def setBrush(self, *_args):
                pass

            def drawRoundedRect(self, *_args):
                pass

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._system_dark_mode = lambda: False
        painter = FakePainter()

        visual._paint_time_module_shell(painter, QRectF(0.0, 0.0, 140.0, 80.0), visual._plugin_theme_colors())

        border_color = painter.pens[-1].color()
        self.assertLess(abs(border_color.red() - border_color.green()), 18)
        self.assertLess(abs(border_color.green() - border_color.blue()), 18)

    def test_dual_line_compact_music_size_grows(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = False
        visual.enable_dual_line_lyrics = True
        visual.lyrics = ["First lyric line\nSecond lyric line"]
        visual.lyric_index = 0

        _width, height = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)

        self.assertAlmostEqual(height, 80.0 * IslandStackVisual.VISUAL_SCALE)

    def test_dual_line_compact_music_width_tracks_current_lyric(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        width = visual._compact_music_target_width_from_content(
            content_w=100.0,
            title_w=80.0,
            artist_w=70.0,
            lyric_w=120.0,
            current_dual_w=520.0,
            instrumental=False,
        )

        self.assertGreater(width, 610.0 * IslandStackVisual.VISUAL_SCALE)

    def test_dual_line_compact_cover_grows_and_shifts_down(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True
        visual.lyrics = ["First lyric line\nSecond lyric line"]
        visual.lyric_index = 0
        dual_rect = QRectF(0.0, 0.0, 420.0, 80.0 * IslandStackVisual.VISUAL_SCALE)

        dual_cover = visual._compact_cover_rect(dual_rect)

        visual.lyrics = ["First lyric line"]
        normal_rect = QRectF(0.0, 0.0, 420.0, 46.0 * IslandStackVisual.VISUAL_SCALE)
        normal_cover = visual._compact_cover_rect(normal_rect)

        self.assertAlmostEqual(dual_cover.width(), 50.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertGreater(dual_cover.width(), normal_cover.width())
        self.assertAlmostEqual(dual_cover.center().y(), dual_rect.center().y() + 1.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertAlmostEqual(normal_cover.center().y(), normal_rect.center().y())

    def test_coalesced_timed_lyrics_merge_same_timestamp_as_two_lines(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        lines = visual._coalesced_timed_lyrics(
            [
                (10.0, "第一行歌词"),
                (10.0, "第二行歌词"),
                (14.0, "下一句"),
            ]
        )

        self.assertEqual(lines, [(10.0, "第一行歌词\n第二行歌词"), (14.0, "下一句")])

    def test_coalesced_timed_lyrics_does_not_merge_adjacent_duet_lines(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        lines = visual._coalesced_timed_lyrics(
            [
                (10.0, "薛 : 我等你的雨季 如此安静"),
                (18.0, "刘 : 我记得是雨季 爱上的你"),
                (30.0, "薛 : 下一句歌词"),
            ]
        )

        self.assertEqual(
            lines,
            [
                (10.0, "薛 : 我等你的雨季 如此安静"),
                (18.0, "刘 : 我记得是雨季 爱上的你"),
                (30.0, "薛 : 下一句歌词"),
            ],
        )


    def test_coalesced_timed_lyrics_preserves_provider_newline(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        lines = visual._coalesced_timed_lyrics(
            [
                (10.0, "薛：我记得那草坪 一览无遗\n刘：我等你的草坪 高楼耸起"),
                (18.0, "下一句"),
            ]
        )

        self.assertEqual(lines, [(10.0, "薛：我记得那草坪 一览无遗\n刘：我等你的草坪 高楼耸起"), (18.0, "下一句")])

    def test_new_timed_lyrics_can_resync_back_to_current_position(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_is_playing = True
        visual.media_position_seconds = 9.0
        visual.media_position_sync_at = 100.0
        visual.media_duration_seconds = 120.0
        visual.lyric_offset_seconds = 0.0
        visual.timed_lyrics = [(8.0, "current"), (20.0, "next")]
        visual.lyrics = ["current", "next"]
        visual.lyric_index = 1
        visual.prev_lyric_index = 1
        visual.lyric_last_sync_position = 0.0
        visual.lyric_changed_at = 99.0

        visual._sync_timed_lyrics(100.0, allow_backward=True)

        self.assertEqual(visual.lyric_index, 0)

    def test_plugin_alert_prefers_due_or_soon_plugin(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 3
        visual.plugin_samples = [
            ("TIME", "20:00", "05/26", QColor(67, 162, 255), "◷", "time", "normal"),
            ("WEATHER", "26°C 晴", "北京", QColor(80, 210, 255), "☁", "weather", "normal"),
            ("AGENDA", "3 分钟后", "开会", QColor(118, 236, 160), "□", "agenda", "soon"),
        ]

        alert = visual._active_plugin_alert()

        self.assertIsNotNone(alert)
        self.assertEqual(alert[1], "AGENDA")
        self.assertEqual(alert[6], "agenda")

    def test_plugin_alerts_include_time_status_when_collapsed(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 3
        visual.plugin_samples = [
            ("TIME", "计时结束", "完成", QColor(67, 162, 255), "◷", "time", "done"),
            ("WEATHER", "26°C 晴", "北京", QColor(80, 210, 255), "☁", "weather", "normal"),
            ("AGENDA", "现在", "开会", QColor(118, 236, 160), "□", "agenda", "due"),
        ]

        alerts = visual._active_plugin_alerts(2)

        self.assertEqual([alert[6] for alert in alerts], ["agenda", "time"])

    def test_running_timer_creates_time_side_plugin_alert_when_collapsed(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 3
        visual.plugin_samples = [
            ("TIME", "00:24", "倒计时 0:24", QColor(67, 162, 255), "●", "time", "running"),
            ("WEATHER", "26°C 晴", "北京", QColor(80, 210, 255), "☀", "weather", "normal"),
            ("AGENDA", "暂无日程", "JSON / ICS", QColor(118, 236, 160), "■", "agenda", "normal"),
        ]

        alerts = visual._active_plugin_alerts(1)

        self.assertEqual([alert[6] for alert in alerts], ["time"])

    def test_time_side_alerts_split_future_alarm_and_running_countdown(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = [
            {"time": "23:59", "label": "备注", "enabled": True, "frequency_unit": "daily"},
        ]
        visual.plugin_samples = [
            ("TIME", "10:11", "倒计时 4:59", QColor(255, 184, 0), "T", "time", "running", "计时进行中", "倒计时 4:59", 30, 0.4),
        ]

        alerts = visual._active_plugin_alerts(2)

        self.assertEqual([alert[6] for alert in alerts], ["time:alarm", "time"])
        self.assertEqual(alerts[0][9], "备注")
        self.assertIn("4:59", alerts[1][9])

    def test_time_alarm_side_alert_key_paints_alarm_model_not_original_timer_model(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = [
            {"time": "23:59", "label": "备注", "enabled": True, "frequency_unit": "daily"},
        ]
        visual.plugin_samples = [
            ("TIME", "10:11", "倒计时 4:59", QColor(255, 184, 0), "T", "time", "running", "计时进行中", "倒计时 4:59", 30, 0.4),
        ]
        calls = []
        visual._paint_time_plugin_alert = lambda _painter, _rect, label, detail, summary, _color, status, alert_title, alert_body, progress, reminder_kind="": calls.append(
            (label, detail, summary, status, alert_title, alert_body, progress, reminder_kind)
        )
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin_alert(painter, QRectF(12.0, 12.0, 150.0, 42.0), "plugin-alert:time:alarm")
        painter.end()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "备注")
        self.assertEqual(calls[0][2], "闹钟")
        self.assertEqual(calls[0][3], "running")
        self.assertEqual(calls[0][5], "备注")
        self.assertEqual(calls[0][7], "alarm")

    def test_time_virtual_alert_key_forces_split_alarm_and_countdown_symbols(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.scroll_phase = 0.0
        visual.plugin_samples = [
            ("TIME", "10:11", "Countdown 4:59", QColor(255, 184, 0), "T", "time", "running", "Timer running", "Countdown 4:59", 30, 0.4),
        ]
        forced_alerts = {
            "alarm": [(0, "TIME", "4:59", "4:59", QColor(255, 184, 0), "T", "time:alarm", "running", "Timer running", "4:59", 29)],
            "countdown": [(0, "TIME", "4:59", "闹钟", QColor(255, 184, 0), "T", "time", "running", "闹钟", "4:59", 30)],
        }
        symbols = []
        visual._paint_time_symbol = lambda _painter, _rect, kind, *_args: symbols.append(str(kind))
        image = QImage(360, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._active_plugin_alerts = lambda *_args, **_kwargs: forced_alerts["alarm"]
        visual._paint_plugin_alert(painter, QRectF(12.0, 12.0, 150.0, 42.0), "plugin-alert:time:alarm")
        visual._active_plugin_alerts = lambda *_args, **_kwargs: forced_alerts["countdown"]
        visual._paint_plugin_alert(painter, QRectF(174.0, 12.0, 118.0, 42.0), "plugin-alert:time")
        painter.end()

        self.assertEqual(symbols[:2], ["alarm", "countdown"])

    def test_plugin_tuple_reads_progress_field_compatibly(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        plugin = visual._plugin_tuple(("TIME", "12:05", "倒计时 4:52", QColor(67, 162, 255), "●", "time", "running", "", "", 30, 0.52))

        self.assertAlmostEqual(plugin[10], 0.52)

    def test_plugin_theme_surfaces_are_opaque(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        for dark in (False, True):
            visual._system_dark_mode = lambda dark=dark: dark
            theme = visual._plugin_theme_colors()

            for key in ("shell", "shell_expanded", "cell", "cell_active", "alert"):
                self.assertEqual(theme[key].alpha(), 255, key)

    def test_music_and_black_glass_base_surfaces_are_opaque(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        black_image = QImage(96, 64, QImage.Format.Format_ARGB32)
        black_image.fill(QColor(0, 0, 0, 0))
        black_painter = QPainter(black_image)
        visual._paint_black_glass(black_painter, QRectF(8.0, 8.0, 80.0, 44.0), 22.0)
        black_painter.end()

        self.assertEqual(black_image.pixelColor(48, 30).alpha(), 255)

        music_image = QImage(128, 72, QImage.Format.Format_ARGB32)
        music_image.fill(QColor(0, 0, 0, 0))
        music_painter = QPainter(music_image)
        visual._paint_music_glass_body(music_painter, QRectF(8.0, 8.0, 112.0, 52.0), 26.0)
        music_painter.end()

        self.assertEqual(music_image.pixelColor(64, 34).alpha(), 255)

    def test_compact_plugin_value_is_value_only(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        value = visual._plugin_compact_value("TIME", "12:05", "倒计时 4:52", "running")

        self.assertEqual(value, "4:52")
        self.assertNotIn("倒计时", value)

    def test_expanded_plugin_main_surface_stays_strip_height(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True

        _width, height = visual._main_size_for_kind(IslandKind.PLUGIN, 420.0)

        self.assertAlmostEqual(height, 48.0 * IslandStackVisual.VISUAL_SCALE)

    def test_expanded_plugin_strip_defaults_to_equal_widths_without_hover(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.enabled_plugin_count = 3
        visual.plugin_focus_from = 0
        visual.plugin_focus_target = 0
        visual.plugin_focus_changed_at = 0.0

        cells = visual._plugin_item_rects(QRectF(0.0, 0.0, 420.0, 48.0), 1.0, expand_active=False)

        widths = [round(cell.width(), 2) for _index, cell in cells]
        self.assertEqual(widths, [140.0, 140.0, 140.0])

    def test_expanded_plugin_strip_keeps_unfocused_cells_readable(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.enabled_plugin_count = 3
        visual.plugin_focus_from = 0
        visual.plugin_focus_target = 1
        visual.plugin_focus_changed_at = -10.0

        cells = visual._plugin_item_rects(QRectF(0.0, 0.0, 420.0, 48.0), 1.0, expand_active=True)

        widths = [cell.width() for _index, cell in cells]
        self.assertGreater(widths[1], widths[0])
        self.assertGreaterEqual(widths[0], 90.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertGreaterEqual(widths[2], 90.0 * IslandStackVisual.VISUAL_SCALE)

    def test_expanded_plugin_focus_width_is_capped(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.enabled_plugin_count = 3
        visual.plugin_focus_from = 1
        visual.plugin_focus_target = 1
        visual.plugin_focus_changed_at = -10.0

        cells = visual._plugin_item_rects(QRectF(0.0, 0.0, 420.0, 48.0), 1.0, expand_active=True)

        widths = [cell.width() for _index, cell in cells]
        self.assertLessEqual(widths[1], 220.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertGreaterEqual(widths[0], 120.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertGreaterEqual(widths[2], 120.0 * IslandStackVisual.VISUAL_SCALE)

    def test_plugin_alert_display_value_is_value_only(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        value = visual._plugin_alert_display_value("TIME", "12:05:49", "Countdown 0:24", "running", "Timer running", "Countdown 0:24")

        self.assertEqual(value, "0:24")
        self.assertNotIn("Countdown", value)

    def test_plugin_alert_width_is_small_for_value_only_alerts(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        width = visual._plugin_alert_width_for("TIME", "12:05:49", "0:24")

        self.assertLessEqual(width, 174.0 * IslandStackVisual.VISUAL_SCALE)

    def _expanded_plugin_layout_visual(self, music_playing: bool = False):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = music_playing
        visual.music_auto_detect = False
        visual.persistent_plugin = True
        visual.expanded = True
        visual.plugin_panel_open = False
        visual.enabled_plugin_count = 3
        visual.plugin_focus = 0
        visual.plugin_focus_from = 0
        visual.plugin_focus_target = 0
        visual.plugin_focus_changed_at = 0.0
        visual.plugin_samples = []
        visual.messages = []
        visual.finder_mode = False
        visual.pointer_pos = QPointF(450.0, 96.0)
        visual.motions = {}
        visual.music_button_rects = {}
        visual.music_cover_rect = QRectF()
        visual.width = lambda: 900
        visual.height = lambda: 420
        visual._current_screen_geometry = lambda: None
        visual._stack_target_width = lambda _kind: 420.0
        visual._standalone_width_limit = lambda _center_x, _screen: 420.0
        visual.mapToGlobal = lambda point: point
        return visual

    def test_expanded_plugin_keeps_down_panel_hidden_until_focus(self):
        visual = self._expanded_plugin_layout_visual(music_playing=False)

        visuals = visual._build_visuals()

        self.assertEqual([item.key for item in visuals], ["plugin"])
        strip = visuals[0]
        self.assertAlmostEqual(strip.rect.height(), 48.0 * IslandStackVisual.VISUAL_SCALE)

    def test_collapsed_plugin_main_shows_time_alarm_and_countdown_side_alerts(self):
        visual = self._expanded_plugin_layout_visual(music_playing=False)
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.plugin_panel_open = False
        visual.plugin_alarm_due_pending = {}
        visual.plugin_alarm_due_acknowledged_key = ""
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []
        visual.show_seconds = False
        visual.plugin_samples = [
            ("TIME", "07:31", "倒计时 4:58", QColor(255, 184, 0), "T", "time", "alarm", "闹钟提醒", "晨会", 3, 1.0),
        ]

        visuals = visual._build_visuals()

        keys = [item.key for item in visuals]
        self.assertIn("plugin-alert:time:alarm", keys)
        self.assertIn("plugin-alert:time", keys)
        self.assertEqual([item.kind for item in visuals if item.key.startswith("plugin-alert:")], [IslandKind.PLUGIN_ALERT, IslandKind.PLUGIN_ALERT])

    def test_expanded_empty_click_does_not_collapse_stack(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.scene_suppressed = False
        visual.drag_candidate = True
        visual.dragging = False
        visual.drag_press_key = ""
        visual._hit_test = lambda _point: ""
        visual.update = lambda: None
        visual._save_placement_state = lambda: None
        collapsed = []
        visual._set_expanded = lambda expanded: collapsed.append(expanded)

        class FakeRelease:
            def button(self):
                return Qt.MouseButton.LeftButton

            def position(self):
                return QPointF(800.0, 300.0)

        visual.mouseReleaseEvent(FakeRelease())

        self.assertEqual(collapsed, [])
        self.assertFalse(visual.drag_candidate)

    def test_plugin_island_double_click_does_not_collapse_expanded_stack(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.scene_suppressed = False
        visual._main_kind = lambda: IslandKind.PLUGIN
        visual.update = lambda: None
        pulses = []
        collapsed = []
        visual._pulse_stack = lambda: pulses.append(True)
        visual._set_expanded = lambda expanded: collapsed.append(bool(expanded))

        class FakeDoubleClick:
            def button(self):
                return Qt.MouseButton.LeftButton

            def position(self):
                return QPointF(120.0, 40.0)

        visual.mouseDoubleClickEvent(FakeDoubleClick())

        self.assertEqual(collapsed, [])
        self.assertEqual(pulses, [])

    def test_music_island_double_click_still_collapses_expanded_stack(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.scene_suppressed = False
        visual._main_kind = lambda: IslandKind.MUSIC
        visual.update = lambda: None
        collapsed = []
        visual._pulse_stack = lambda: None
        visual._set_expanded = lambda expanded: collapsed.append(bool(expanded))

        class FakeDoubleClick:
            def button(self):
                return Qt.MouseButton.LeftButton

            def position(self):
                return QPointF(120.0, 40.0)

        visual.mouseDoubleClickEvent(FakeDoubleClick())

        self.assertEqual(collapsed, [False])

    def test_plugin_focus_click_reveals_separate_down_panel_below_strip(self):
        visual = self._expanded_plugin_layout_visual(music_playing=False)

        visual._activate_key("plugin")
        visuals = visual._build_visuals()

        self.assertEqual([item.key for item in visuals], ["plugin", "plugin-panel"])
        strip, panel = visuals
        self.assertAlmostEqual(strip.rect.height(), 48.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertAlmostEqual(panel.rect.width(), strip.rect.width())
        self.assertAlmostEqual(panel.rect.top(), strip.rect.bottom())
        self.assertEqual(panel.kind.value, "plugin_panel")

    def test_plugin_panel_joins_strip_without_gap(self):
        visual = self._expanded_plugin_layout_visual(music_playing=True)
        visual.plugin_panel_open = True

        visuals = visual._build_visuals()

        strip = next(item for item in visuals if item.key == "plugin")
        panel = next(item for item in visuals if item.key == "plugin-panel")
        self.assertAlmostEqual(panel.rect.top(), strip.rect.bottom())

    def test_plugin_panel_shell_rect_includes_strip_and_panel(self):
        visual = self._expanded_plugin_layout_visual(music_playing=True)
        strip = QRectF(10.0, 20.0, 420.0, 48.0)
        panel = QRectF(10.0, 68.0, 420.0, 188.0)

        shell = visual._plugin_panel_shell_rect(panel, strip)

        self.assertAlmostEqual(shell.top(), strip.top())
        self.assertAlmostEqual(shell.bottom(), panel.bottom())
        self.assertAlmostEqual(shell.left(), strip.left())
        self.assertAlmostEqual(shell.right(), strip.right())

    def test_plugin_panel_initial_motion_starts_from_strip_edge(self):
        visual = self._expanded_plugin_layout_visual(music_playing=True)
        visual.plugin_panel_open = True
        visuals = visual._build_visuals()
        strip = next(item for item in visuals if item.key == "plugin")
        panel = next(item for item in visuals if item.key == "plugin-panel")

        start = visual._initial_motion_rect_for_visual(panel, visuals)

        self.assertAlmostEqual(start.top(), strip.rect.bottom())
        self.assertLessEqual(start.height(), visual._u(12.0))

    def test_plugin_hover_keeps_down_panel_hidden_until_delay(self):
        visual = self._expanded_plugin_layout_visual(music_playing=False)
        visual.scene_suppressed = False
        visual.drag_candidate = False
        visual.last_pointer_move_at = -10.0
        visual.left_stack_at = 0.0
        visual.hovered_key = ""
        visual.hover_armed_key = ""
        visual.hover_started_at = 0.0
        visual.hover_bounce_until = 0.0
        visual.update = lambda: None
        strip = visual._build_visuals()[0]

        class MoveEvent:
            def __init__(self, position: QPointF):
                self._position = position

            def position(self):
                return self._position

            def buttons(self):
                return Qt.MouseButton.NoButton

        visual.mouseMoveEvent(MoveEvent(strip.rect.center()))

        self.assertEqual(visual.hovered_key, "plugin")
        self.assertFalse(visual.plugin_panel_open)
        self.assertNotIn("plugin-panel", [item.key for item in visual._build_visuals()])

    def test_plugin_hover_reveals_down_panel_after_half_second_delay(self):
        visual = self._expanded_plugin_layout_visual(music_playing=True)
        visual.scene_suppressed = False
        visual.hovered_key = "plugin"
        visual.hover_armed_key = ""
        visual.hover_started_at = 0.0
        visual.left_stack_at = 0.0
        visual.last_tick_at = 0.48
        visual.animation_speed = 1.0
        visual.barrage_hovered = False
        visual.barrage_clock = 0.0
        visual.pointer_pos = QPointF(450.0, 120.0)
        visual.super_topmost_enabled = False
        visual.media_is_playing = False
        visual.timed_lyrics = []
        visual.lyrics = []
        visual.scroll_phase = 0.0
        visual.ambient_phase = 0.0
        visual._refresh_media_data = lambda _now: None
        visual._update_scene_state = lambda _now: None
        visual._point_in_barrage = lambda _point: False
        visual._update_wave_values = lambda: None
        visual.update = lambda: None

        with patch("local.island_stack_visual.time.monotonic", return_value=0.49):
            visual._tick()

        self.assertFalse(visual.plugin_panel_open)
        self.assertNotIn("plugin-panel", [item.key for item in visual._build_visuals()])

        with patch("local.island_stack_visual.time.monotonic", return_value=0.51):
            visual._tick()

        self.assertTrue(visual.plugin_panel_open)
        self.assertIn("plugin-panel", [item.key for item in visual._build_visuals()])

    def test_plugin_panel_focus_model_uses_active_plugin_only(self):
        visual = self._expanded_plugin_layout_visual(music_playing=False)
        visual.plugin_focus = 1
        visual.plugin_focus_from = 1
        visual.plugin_focus_target = 1
        visual.plugin_samples = [
            ("TIME", "19:04", "时间", QColor(67, 162, 255), "T", "time", "normal", "", "", 50, 0.0),
            ("WEATHER", "24.6°C", "小雨", QColor(80, 210, 255), "W", "weather", "normal", "", "", 50, 0.0),
            ("AGENDA", "暂无日程", "今天", QColor(118, 236, 160), "A", "agenda", "normal", "", "", 50, 0.0),
        ]

        model = visual._plugin_panel_focus_model()

        self.assertIsNotNone(model)
        self.assertEqual(model["label"], "WEATHER")
        self.assertEqual(model["plugin_id"], "weather")
        self.assertEqual(model["action"]["command"], "weather-refresh")

    def test_plugin_hit_test_falls_back_to_layout_rect_for_hover_activation(self):
        visual = self._expanded_plugin_layout_visual(music_playing=False)
        visual.expanded = False
        visual.motions = {}

        plugin = next(item for item in visual._build_visuals() if item.key == "plugin")

        self.assertEqual(visual._hit_test(plugin.rect.center()), "plugin")

    def test_expanded_music_keeps_plugin_strip_without_panel_until_focus(self):
        visual = self._expanded_plugin_layout_visual(music_playing=True)

        visuals = visual._build_visuals()

        self.assertEqual([item.key for item in visuals], ["music", "plugin"])
        music, strip = visuals
        self.assertGreater(strip.rect.top(), music.rect.bottom())
        self.assertAlmostEqual(strip.rect.height(), 48.0 * IslandStackVisual.VISUAL_SCALE)

        visual._activate_key("plugin")
        visuals = visual._build_visuals()

        self.assertEqual([item.key for item in visuals], ["music", "plugin", "plugin-panel"])
        self.assertAlmostEqual(visuals[2].rect.top(), visuals[1].rect.bottom())

    def test_plugin_panel_closes_when_hover_returns_to_music(self):
        visual = self._expanded_plugin_layout_visual(music_playing=True)
        visual.plugin_panel_open = True
        visual.scene_suppressed = False
        visual.drag_candidate = False
        visual.last_pointer_move_at = -10.0
        visual.left_stack_at = 0.0
        visual.hovered_key = "plugin-panel"
        visual.update = lambda: None
        music = next(item for item in visual._build_visuals() if item.key == "music")

        class MoveEvent:
            def __init__(self, position: QPointF):
                self._position = position

            def position(self):
                return self._position

            def buttons(self):
                return Qt.MouseButton.NoButton

        visual.mouseMoveEvent(MoveEvent(music.rect.center()))

        self.assertTrue(visual.expanded)
        self.assertFalse(visual.plugin_panel_open)

    def test_plugin_panel_stays_open_when_hover_moves_down_to_message(self):
        visual = self._expanded_plugin_layout_visual(music_playing=True)
        visual.plugin_panel_open = True
        visual.scene_suppressed = False
        visual.drag_candidate = False
        visual.last_pointer_move_at = -10.0
        visual.left_stack_at = 0.0
        visual.hovered_key = "plugin-panel"
        visual.update = lambda: None
        visual.messages = [
            Message("QQ", "Alice", "hello", QColor(80, 170, 255), QColor(80, 170, 255), serial=1),
        ]
        message = next(item for item in visual._build_visuals() if item.key.startswith("message:"))

        class MoveEvent:
            def __init__(self, position: QPointF):
                self._position = position

            def position(self):
                return self._position

            def buttons(self):
                return Qt.MouseButton.NoButton

        visual.mouseMoveEvent(MoveEvent(message.rect.center()))

        self.assertTrue(visual.plugin_panel_open)

    def test_finder_full_screen_texture_source_uses_global_xy(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        image = QImage(800, 600, QImage.Format.Format_ARGB32)
        visual.finder_material = FinderGlassMaterial(
            QColor(240, 240, 240, 110),
            QColor(190, 190, 190, 132),
            QColor(110, 110, 110, 128),
            88,
            166,
            50,
            28.0,
            11,
            "test",
            texture_image=image,
            texture_screen_left=100,
            texture_screen_top=50,
            texture_is_full_screen=True,
        )
        visual.mapToGlobal = lambda point: QPoint(point.x() + 120, point.y() + 70)
        target = QRectF(20.0, 30.0, 200.0, 100.0)

        source = visual._finder_texture_source_rect(target, image)

        self.assertAlmostEqual(source.left(), 40.0)
        self.assertAlmostEqual(source.top(), 50.0)
        self.assertAlmostEqual(source.width(), 200.0)
        self.assertAlmostEqual(source.height(), 100.0)

    def test_finder_mode_keeps_normal_music_shell_and_border(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = True
        visual.group_frame_mask_path = object()
        visual.music_ambience_style = "sea_level"
        calls = []
        visual._paint_music_glass_body = lambda *_args: calls.append("shell")
        visual._paint_bottom_ambience = lambda *_args: calls.append("ambience")
        visual._paint_music_blur_mask = lambda *_args: calls.append("blur")
        visual._paint_music_light_wave = lambda *_args: calls.append("wave")
        visual._paint_music_border = lambda *_args: calls.append("border")
        visual._paint_music_compact = lambda *_args: calls.append("compact")
        image = QImage(320, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music(painter, QRectF(20.0, 20.0, 240.0, 48.0), False)
        painter.end()

        self.assertEqual(calls, ["shell", "ambience", "blur", "wave", "compact", "border"])

    def test_wave_color_fill_paints_pure_black_music_background(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "wave_stream"
        visual.wave_color_fill_enabled = True
        visual.music_ambience_style = "sea_level"
        calls = []
        visual._paint_music_glass_body = lambda *_args: calls.append("shell")
        visual._paint_bottom_ambience = lambda *_args: calls.append("ambience")
        visual._paint_music_blur_mask = lambda *_args: calls.append("blur")
        visual._paint_music_light_wave = lambda *_args: calls.append("wave")
        visual._paint_music_compact = lambda *_args: calls.append("compact")
        visual._paint_music_border = lambda *_args: calls.append("border")
        image = QImage(320, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music(painter, QRectF(20.0, 20.0, 240.0, 48.0), False)
        painter.end()

        center = image.pixelColor(140, 44)
        self.assertEqual((center.red(), center.green(), center.blue(), center.alpha()), (0, 0, 0, 255))
        self.assertEqual(calls, ["compact", "border"])

    def test_same_track_black_placeholder_album_art_does_not_replace_existing_cover(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "same-track"
        visual.media_position_sync_at = 10.0
        visual.media_position_seconds = 12.0
        visual.media_raw_position_seconds = 12.0
        visual.media_raw_position_source = "windows-media"
        visual.lyrics = ["line"]
        visual.timed_lyrics = []
        visual.messages = []
        visual._seen_provider_message_keys = set()
        visual.listened_seconds_total = 0.0
        visual._scaled_album_art_cache = {}
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(210, 36, 44))
        visual._estimated_media_position = lambda _now: 12.0
        visual._clear_lyric_metrics_cache = lambda: None
        visual._sync_timed_lyrics = lambda *_args, **_kwargs: None

        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("line",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
            album_art_bytes=self._png_bytes(QColor(0, 0, 0)),
            track_id="same-track",
            position_seconds=12.0,
        )

        visual._apply_visual_data(IslandVisualData(track=track, messages=(), plugins=()))

        kept = visual.album_art.pixelColor(0, 0)
        self.assertEqual((kept.red(), kept.green(), kept.blue()), (210, 36, 44))

    def test_message_refresh_black_placeholder_album_art_does_not_replace_existing_cover(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "old-track"
        visual.track_title = "Song"
        visual.artist = "Artist"
        visual.media_position_sync_at = 10.0
        visual.media_position_seconds = 12.0
        visual.media_raw_position_seconds = 12.0
        visual.media_raw_position_source = "windows-media"
        visual.lyrics = ["line"]
        visual.timed_lyrics = []
        visual.messages = []
        visual._seen_provider_message_keys = set()
        visual.listened_seconds_total = 0.0
        visual._scaled_album_art_cache = {}
        visual.album_history = []
        visual.album_queue = []
        visual.album_island_transition_started_at = 0.0
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(210, 36, 44))
        visual._estimated_media_position = lambda _now: 12.0
        visual._clear_lyric_metrics_cache = lambda: None
        visual._sync_timed_lyrics = lambda *_args, **_kwargs: None

        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("line",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
            album_art_bytes=self._png_bytes(QColor(0, 0, 0)),
            track_id="message-refresh",
            position_seconds=12.0,
        )

        visual._apply_visual_data(
            IslandVisualData(
                track=track,
                messages=(MessageData("WeChat", "Alice", "new", ColorToken(75, 210, 110), ColorToken(75, 210, 110)),),
                plugins=(),
            )
        )

        kept = visual.album_art.pixelColor(0, 0)
        self.assertEqual((kept.red(), kept.green(), kept.blue()), (210, 36, 44))

    def test_message_refresh_missing_album_art_does_not_clear_existing_cover_for_same_song(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "old-track"
        visual.track_title = "Song"
        visual.artist = "Artist"
        visual.media_position_sync_at = 10.0
        visual.media_position_seconds = 12.0
        visual.media_raw_position_seconds = 12.0
        visual.media_raw_position_source = "windows-media"
        visual.lyrics = ["line"]
        visual.timed_lyrics = []
        visual.messages = []
        visual._seen_provider_message_keys = set()
        visual.listened_seconds_total = 0.0
        visual._scaled_album_art_cache = {}
        visual.album_history = []
        visual.album_queue = []
        visual.album_island_transition_started_at = 0.0
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(210, 36, 44))
        visual._estimated_media_position = lambda _now: 12.0
        visual._clear_lyric_metrics_cache = lambda: None
        visual._sync_timed_lyrics = lambda *_args, **_kwargs: None

        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("line",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
            album_art_bytes=b"",
            track_id="message-refresh",
            position_seconds=12.0,
        )

        visual._apply_visual_data(
            IslandVisualData(
                track=track,
                messages=(MessageData("WeChat", "Alice", "new", ColorToken(75, 210, 110), ColorToken(75, 210, 110)),),
                plugins=(),
            )
        )

        kept = visual.album_art.pixelColor(0, 0)
        self.assertEqual((kept.red(), kept.green(), kept.blue()), (210, 36, 44))

    def test_music_ambience_accepts_color_fill_black_styles(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        visual.set_music_ambience_style("pure_black", notify=False)
        self.assertEqual(visual.music_ambience_style, "pure_black")

        visual.set_music_ambience_style("bilateral_diffraction", notify=False)
        self.assertEqual(visual.music_ambience_style, "bilateral_diffraction")

    def test_color_fill_rejects_legacy_music_ambience_styles(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "wave_stream"
        visual.wave_color_fill_enabled = True
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        visual.set_music_ambience_style("sea_level", notify=False)
        self.assertEqual(visual.music_ambience_style, "pure_black")

        visual.set_music_ambience_style("duotone_full", notify=False)
        self.assertEqual(visual.music_ambience_style, "pure_black")

    def test_enabling_active_color_fill_moves_legacy_ambience_to_pure_black(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "wave_stream"
        visual.wave_color_fill_enabled = False
        visual.gelv_color_fill_enabled = False
        visual.music_ambience_style = "sea_level"
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        visual.set_wave_color_fill_enabled(True, notify=False)

        self.assertEqual(visual.music_ambience_style, "pure_black")

    def test_bilateral_diffraction_background_is_used_for_color_fill(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.gelv_color_fill_enabled = True
        visual.music_ambience_style = "bilateral_diffraction"
        calls = []
        visual._paint_bilateral_diffraction_ambience = lambda *_args: calls.append("diffraction")
        visual._paint_music_glass_body = lambda *_args: calls.append("shell")
        visual._paint_bottom_ambience = lambda *_args: calls.append("ambience")
        visual._paint_music_blur_mask = lambda *_args: calls.append("blur")
        visual._paint_music_light_wave = lambda *_args: calls.append("wave")
        visual._paint_music_compact = lambda *_args: calls.append("compact")
        visual._paint_music_border = lambda *_args: calls.append("border")
        image = QImage(320, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music(painter, QRectF(20.0, 20.0, 240.0, 48.0), False)
        painter.end()

        self.assertEqual(calls, ["diffraction", "compact", "border"])

    def test_compact_bilateral_diffraction_uses_circular_rings(self):
        class FakePainter:
            def __init__(self):
                self.arc_rects = []

            def save(self):
                pass

            def restore(self):
                pass

            def setClipPath(self, *_args):
                pass

            def setPen(self, *_args):
                pass

            def setBrush(self, *_args):
                pass

            def fillRect(self, *_args):
                pass

            def translate(self, *_args):
                pass

            def scale(self, *_args):
                pass

            def drawEllipse(self, *_args):
                pass

            def drawArc(self, rect, *_args):
                self.arc_rects.append(QRectF(rect))

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "wave_stream"
        visual.wave_color_fill_enabled = True
        visual.wave_color_fill_fallback = "#00D7FF"
        visual.album_color = QColor(22, 188, 255)
        visual.secondary_color = QColor(255, 58, 148)
        visual.current_audio_bands = (0.24, 0.32, 0.18)
        visual.ambient_intensity = 0.0
        visual.music_flow_intensity = 0.74
        visual.media_is_playing = True
        visual.wave_values = [0.25] * 36
        visual.ambient_phase = 0.0
        painter = FakePainter()

        visual._paint_bilateral_diffraction_ambience(painter, QRectF(20.0, 20.0, 240.0, 48.0), 24.0, False)

        self.assertTrue(painter.arc_rects)
        self.assertEqual(len(painter.arc_rects), 14)
        for rect in painter.arc_rects:
            self.assertAlmostEqual(rect.width(), rect.height(), delta=0.01)

    def test_finder_time_side_alert_keeps_own_background_instead_of_material_replacement(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = True
        visual.scroll_phase = 0.0
        visual._system_dark_mode = lambda: True
        visual._paint_plugin_alert_material_background = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Finder mode must not replace the side alert's own background")
        )
        visual._paint_time_symbol = lambda *_args, **_kwargs: None
        visual._paint_plugin_progress_ring = lambda *_args, **_kwargs: None
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(12.0, 12.0, 156.0, 42.0)

        visual._paint_time_plugin_alert(
            painter,
            rect,
            "TIME",
            "0:26",
            "Countdown 0:26",
            QColor(255, 184, 0),
            "running",
            "Timer running",
            "Countdown 0:26",
            0.75,
            "countdown",
        )
        painter.end()

    def test_finder_generic_side_alert_keeps_own_background_for_future_plugins(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = True
        visual.enabled_plugin_count = 1
        visual.scroll_phase = 0.0
        visual._system_dark_mode = lambda: True
        visual.plugin_samples = [
            ("WEATHER", "Storm", "Right side content", QColor(80, 210, 255), "W", "weather", "severe", "Weather", "Right side content", 20, 1.0),
        ]
        visual._paint_plugin_alert_material_background = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Finder mode must not replace the side alert's own background")
        )
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin_alert(painter, QRectF(12.0, 12.0, 180.0, 42.0), "plugin-alert:weather")
        painter.end()

    def test_finder_outer_frame_expands_to_cover_plugin_side_alerts(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = True
        visual.expanded = False
        visual.group_frame_motion = None
        visual.finder_hover_rect = QRectF()
        visual.finder_material = FinderGlassMaterial(
            QColor(190, 198, 194, 255),
            QColor(170, 184, 178, 255),
            QColor(146, 164, 158, 255),
            92,
            150,
            54,
            28.0,
            11,
            "test",
            available=True,
        )
        visual.width = lambda: 900
        visual._has_current_two_line_lyric = lambda: False
        main_rect = QRectF(180.0, 42.0, 420.0, 48.0)
        alert_rect = QRectF(612.0, 45.0, 132.0, 42.0)
        main_motion = SpringRect(main_rect)
        main_motion.opacity = 1.0
        alert_motion = SpringRect(alert_rect)
        alert_motion.opacity = 1.0
        items = [
            (StackVisual("music", IslandKind.MUSIC, main_rect, False, None, 10), main_motion),
            (StackVisual("plugin-alert:time", IslandKind.PLUGIN_ALERT, alert_rect, False, None, 8), alert_motion),
        ]
        image = QImage(900, 180, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_myfinder_group_frame(painter, items, 1.0)
        painter.end()

        self.assertGreater(visual.finder_hover_rect.right(), alert_rect.right())
        self.assertLess(visual.finder_hover_rect.left(), main_rect.left())

    def test_expanded_plugin_panel_is_not_overlong(self):
        visual = self._expanded_plugin_layout_visual(music_playing=False)
        visual.expanded = True
        visual.plugin_panel_open = True

        panels = [item for item in visual._build_visuals() if item.key == "plugin-panel"]
        self.assertEqual(len(panels), 1)
        panel = panels[0]

        self.assertEqual(panel.rect.height(), visual._plugin_panel_height(panel.rect.width()))
        self.assertLessEqual(panel.rect.height(), visual._u(240.0))

    def test_plugin_function_lines_are_runtime_not_settings(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        title, lines = visual._plugin_function_lines("WEATHER", "26C Sunny", "Beijing wind humidity 40%", "normal")

        self.assertIn("天气", title)
        self.assertIn("26C Sunny", lines)
        self.assertNotIn("手动城市", " ".join(lines))

    def test_running_timer_alert_prefers_timer_summary(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        title, body = visual._plugin_alert_text_parts("TIME", "20:00:05", "倒计时 0:24", "running")

        self.assertIn("计时", title)
        self.assertEqual(body, "倒计时 0:24")

    def test_weather_loading_creates_plugin_alert(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 3
        visual.plugin_samples = [
            ("TIME", "20:00", "05/27", QColor(67, 162, 255), "●", "time", "normal", "", "", 50),
            ("WEATHER", "北京", "天气刷新中", QColor(80, 210, 255), "☁", "weather", "loading", "天气刷新中", "北京", 40),
            ("AGENDA", "暂无日程", "JSON / ICS", QColor(118, 236, 160), "■", "agenda", "normal", "", "", 50),
        ]

        alerts = visual._active_plugin_alerts(1)

        self.assertEqual([alert[6] for alert in alerts], ["weather"])

    def test_plugin_alert_text_prefers_provider_alert_copy(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        title, body = visual._plugin_alert_text_parts("WEATHER", "北京", "天气刷新中", "loading", "天气刷新中", "北京")

        self.assertEqual(title, "WEATHER · 天气刷新中")
        self.assertEqual(body, "北京")

    def test_plugin_alert_priority_comes_before_status_order(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 3
        visual.plugin_samples = [
            ("TIME", "00:24", "倒计时 0:24", QColor(67, 162, 255), "●", "time", "running", "计时进行中", "倒计时 0:24", 30),
            ("WEATHER", "北京", "天气刷新中", QColor(80, 210, 255), "☁", "weather", "loading", "天气刷新中", "北京", 40),
            ("AGENDA", "4 分钟后", "准备开会", QColor(118, 236, 160), "■", "agenda", "soon", "日程将近", "4 分钟后 · 准备开会", 10),
        ]

        alerts = visual._active_plugin_alerts(2)

        self.assertEqual([alert[6] for alert in alerts], ["agenda", "time"])

    def test_plugin_alert_width_adapts_to_content(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        short_width = visual._plugin_alert_width_for("TIME", "00:24", "倒计时")
        long_width = visual._plugin_alert_width_for("AGENDA", "12 分钟后", "一个很长很长的会议提醒")

        self.assertGreater(long_width, short_width)
        self.assertLessEqual(long_width, 252.0 * IslandStackVisual.VISUAL_SCALE)

    def test_plugin_alert_rect_stays_inside_narrow_window(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual.width = lambda: 620
        visual.height = lambda: 260
        main_rect = QRectF(120.0, 82.0, 480.0, 48.0)

        rect = visual._plugin_alert_rect(main_rect, 0, 1)

        self.assertGreaterEqual(rect.left(), visual._u(8.0))
        self.assertLessEqual(rect.right(), visual.width() - visual._u(8.0))

    def test_plugin_alert_screen_bounds_do_not_exceed_local_canvas(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.width = lambda: 705
        visual.mapToGlobal = lambda point: QPoint(point.x() + 100, point.y())
        visual._current_screen_geometry = lambda: QRect(0, 0, 2000, 600)

        _left, right = visual._screen_bounds_in_local()

        self.assertLessEqual(right, visual.width() - visual._u(8.0))

    def test_time_alarm_and_countdown_side_alerts_stay_in_one_stable_row(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [
            (0, "TIME", "所爱衬所爱", "闹钟", QColor(255, 184, 0), "T", "time:alarm", "alarm", "闹钟提醒", "所爱衬所爱", 3),
            (0, "TIME", "4:58", "倒计时 4:58", QColor(255, 184, 0), "T", "time", "running", "计时进行中", "倒计时 4:58", 30),
        ]
        visual._screen_bounds_in_local = lambda: (0.0, 760.0)
        visual._main_rect_screen_pressure = lambda *_args: ""
        visual.height = lambda: 260
        main_rect = QRectF(150.0, 82.0, 460.0, 48.0)

        first = visual._plugin_alert_rect(main_rect, 0, 2)
        second = visual._plugin_alert_rect(main_rect, 1, 2)

        self.assertGreater(first.center().x(), main_rect.right())
        self.assertGreater(second.center().x(), main_rect.right())
        self.assertFalse(first.intersects(second))
        self.assertFalse(first.intersects(main_rect))
        self.assertFalse(second.intersects(main_rect))

    def test_time_alarm_and_countdown_side_alerts_do_not_split_left_right_when_cramped(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [
            (0, "TIME", "晨会", "闹钟", QColor(255, 184, 0), "T", "time:alarm", "alarm", "闹钟提醒", "晨会", 3),
            (0, "TIME", "4:58", "倒计时 4:58", QColor(255, 184, 0), "T", "time", "running", "计时进行中", "倒计时 4:58", 30),
        ]
        visual._screen_bounds_in_local = lambda: (0.0, 720.0)
        visual._main_rect_screen_pressure = lambda *_args: ""
        visual.height = lambda: 260
        main_rect = QRectF(300.0, 82.0, 350.0, 48.0)

        first = visual._plugin_alert_rect(main_rect, 0, 2)
        second = visual._plugin_alert_rect(main_rect, 1, 2)

        first_on_right = first.center().x() > main_rect.center().x()
        second_on_right = second.center().x() > main_rect.center().x()
        self.assertEqual(first_on_right, second_on_right)

    def test_standalone_canvas_reserves_room_for_side_alerts(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        width = visual._standalone_canvas_width()

        self.assertGreaterEqual(width, visual._u(1160.0))

    def test_multiple_alerts_split_full_rectangles_before_boundary_instead_of_shrinking(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [
            (0, "TIME", "0:00", "时间到", QColor(255, 184, 0), "", "time", "done", "计时结束", "时间到", 10),
            (1, "WEATHER", "雷雨", "right side content", QColor(80, 210, 255), "☁", "weather", "severe", "严重天气", "right side content", 20),
        ]
        visual._screen_bounds_in_local = lambda: (0.0, 900.0)
        visual._main_rect_screen_pressure = lambda *_args: ""
        visual.height = lambda: 260
        main_rect = QRectF(260.0, 82.0, 380.0, 48.0)

        first = visual._plugin_alert_rect(main_rect, 0, 2)
        second = visual._plugin_alert_rect(main_rect, 1, 2)

        self.assertGreater(first.width(), first.height() * 2.4)
        self.assertGreater(second.width(), second.height() * 2.4)
        self.assertFalse(first.intersects(main_rect))
        self.assertFalse(second.intersects(main_rect))
        self.assertNotEqual(first.center().x() > main_rect.center().x(), second.center().x() > main_rect.center().x())

    def test_multiple_circular_plugin_alerts_split_sides_when_no_side_can_fit_row(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [
            (0, "TIME", "0:00", "时间到", QColor(255, 184, 0), "", "time", "done", "计时结束", "时间到", 10),
            (1, "WEATHER", "雷雨", "北京", QColor(80, 210, 255), "☁", "weather", "severe", "严重天气", "北京", 20),
        ]
        visual._screen_bounds_in_local = lambda: (0.0, 800.0)
        visual._main_rect_screen_pressure = lambda *_args: ""
        visual.height = lambda: 260
        main_rect = QRectF(40.0, 82.0, 720.0, 48.0)

        first = visual._plugin_alert_rect(main_rect, 0, 2)
        second = visual._plugin_alert_rect(main_rect, 1, 2)

        self.assertAlmostEqual(first.width(), first.height(), delta=0.5)
        self.assertAlmostEqual(second.width(), second.height(), delta=0.5)
        self.assertFalse(first.intersects(second))
        self.assertFalse(first.intersects(main_rect))
        self.assertFalse(second.intersects(main_rect))

    def test_plugin_alert_moves_left_when_right_screen_edge_would_clip_it(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual.finder_mode = False
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda point: QPoint(point.x() + 240, point.y())
        visual._current_screen_geometry = lambda: QRect(0, 0, 800, 600)
        main_rect = QRectF(520.0, 80.0, 220.0, 48.0)

        rect = visual._plugin_alert_rect(main_rect, 0, 1)

        self.assertLessEqual(rect.right(), main_rect.left() - visual._u(9.0))
        self.assertAlmostEqual(rect.height(), 42.0 * IslandStackVisual.VISUAL_SCALE)

    def test_plugin_alert_becomes_circular_left_when_main_shrinks_against_right_edge(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual.finder_mode = False
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda point: QPoint(point.x() + 320, point.y())
        visual._current_screen_geometry = lambda: QRect(0, 0, 800, 600)
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        main_rect = QRectF(365.0, 80.0, 210.0, 48.0)

        rect = visual._plugin_alert_rect(main_rect, 0, 1)

        self.assertAlmostEqual(rect.width(), rect.height())
        self.assertLessEqual(rect.right(), main_rect.left() - visual._u(9.0))

    def test_plugin_alert_becomes_circular_right_when_main_shrinks_against_left_edge(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual.finder_mode = False
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda point: QPoint(point.x() - 460, point.y())
        visual._current_screen_geometry = lambda: QRect(0, 0, 800, 600)
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        main_rect = QRectF(365.0, 80.0, 210.0, 48.0)

        rect = visual._plugin_alert_rect(main_rect, 0, 1)

        self.assertAlmostEqual(rect.width(), rect.height())
        self.assertGreaterEqual(rect.left(), main_rect.right() + visual._u(9.0))

    def test_multiple_circular_plugin_alerts_layout_horizontally(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual.finder_mode = False
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda point: QPoint(point.x() + 320, point.y())
        visual._current_screen_geometry = lambda: QRect(0, 0, 800, 600)
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        main_rect = QRectF(365.0, 80.0, 210.0, 48.0)

        first = visual._plugin_alert_rect(main_rect, 0, 2)
        second = visual._plugin_alert_rect(main_rect, 1, 2)

        self.assertAlmostEqual(first.width(), first.height())
        self.assertAlmostEqual(second.width(), second.height())
        self.assertAlmostEqual(first.center().y(), second.center().y())
        self.assertGreaterEqual(first.left(), second.right() + visual._u(6.0))

    def test_multiple_rectangular_plugin_alerts_layout_horizontally(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual.finder_mode = False
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda point: QPoint(point.x(), point.y())
        visual._current_screen_geometry = lambda: QRect(0, 0, 1200, 600)
        main_rect = QRectF(80.0, 80.0, 220.0, 48.0)

        first = visual._plugin_alert_rect(main_rect, 0, 2)
        second = visual._plugin_alert_rect(main_rect, 1, 2)

        self.assertAlmostEqual(first.center().y(), second.center().y())
        self.assertGreaterEqual(second.left(), first.right() + visual._u(6.0))

    def test_plugin_alert_drag_anchor_uses_main_center(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        main_rect = QRectF(100.0, 70.0, 300.0, 48.0)

        offset = visual._drag_main_offset_for_press(
            QPointF(430.0, 94.0),
            main_rect,
            "plugin-alert:time",
        )

        self.assertAlmostEqual(offset.x(), main_rect.width() / 2.0)
        self.assertAlmostEqual(offset.y(), main_rect.height() / 2.0)

    def test_disabling_super_topmost_releases_win32_topmost_state(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        calls = []
        visual._force_super_topmost = lambda: calls.append("force")
        visual._release_super_topmost = lambda: calls.append("release")
        visual._notify_settings_changed = lambda: calls.append("notify")
        visual.update = lambda: calls.append("update")

        visual.set_super_topmost(False)

        self.assertFalse(visual.super_topmost_enabled)
        self.assertEqual(calls, ["release", "notify", "update"])

    def test_standalone_drag_window_position_clamps_to_screen_edges(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        screen_geo = QRect(0, 0, 800, 600)
        main_left = 365.0
        main_top = 54.0
        main_w = 210.0
        main_h = 48.0

        top_left = visual._clamp_standalone_window_pos(-999, -999, main_left, main_top, main_w, main_h, screen_geo)
        bottom_right = visual._clamp_standalone_window_pos(9999, 9999, main_left, main_top, main_w, main_h, screen_geo)

        self.assertEqual(top_left.x(), int(round(screen_geo.left() + visual._u(6.0) - main_left)))
        self.assertEqual(top_left.y(), int(round(screen_geo.top() + visual._u(6.0) - main_top)))
        self.assertEqual(bottom_right.x(), int(round(screen_geo.right() - visual._u(6.0) - main_left - main_w)))
        self.assertEqual(bottom_right.y(), int(round(screen_geo.bottom() - visual._u(6.0) - main_top - main_h)))

    def test_plugin_alert_click_expands_and_focuses_plugin(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 3
        visual.plugin_focus = 0
        visual.plugin_focus_from = 0
        visual.plugin_focus_target = 0
        visual.plugin_focus_changed_at = 0.0
        visual.plugin_samples = [
            ("TIME", "20:00", "05/26", QColor(67, 162, 255), "◷", "time", "normal"),
            ("WEATHER", "26°C 晴", "北京", QColor(80, 210, 255), "☁", "weather", "normal"),
            ("AGENDA", "现在", "开会", QColor(118, 236, 160), "□", "agenda", "due"),
        ]
        visual._set_expanded = lambda value: setattr(visual, "expanded", bool(value))
        visual._pulse_stack = lambda: None

        visual._activate_key("plugin-alert:agenda")

        self.assertTrue(visual.expanded)
        self.assertEqual(visual.plugin_focus_target, 2)

    def test_expanded_plugin_keeps_time_status_inside_time_panel(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = True
        visual.plugin_panel_open = True
        visual.enabled_plugin_count = 3
        visual.plugin_focus = 0
        visual.plugin_focus_from = 0
        visual.plugin_focus_target = 0
        visual.plugin_focus_changed_at = 0.0
        visual.music_playing = False
        visual.messages = []
        visual.finder_mode = False
        visual.width = lambda: 900
        visual.height = lambda: 420
        visual._current_screen_geometry = lambda: None
        visual._stack_target_width = lambda _kind: 420.0
        visual._standalone_width_limit = lambda _center_x, _screen: 420.0
        visual.mapToGlobal = lambda point: point
        visual.plugin_samples = [
            ("TIME", "00:24", "Countdown 0:24", QColor(67, 162, 255), "●", "time", "running", "Timer running", "Countdown 0:24", 30, 0.4),
            ("WEATHER", "26°C 晴", "北京", QColor(80, 210, 255), "☀", "weather", "normal", "", "", 50, 0.0),
            ("AGENDA", "暂无日程", "JSON / ICS", QColor(118, 236, 160), "■", "agenda", "normal", "", "", 50, 0.0),
        ]

        visuals = visual._build_visuals()

        self.assertEqual([item.key for item in visuals], ["plugin", "plugin-panel"])
        self.assertEqual(visual._plugin_panel_alert_progress(0), 1.0)

    def test_plugin_ring_style_is_thick_for_animation_first_language(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        style = visual._plugin_ring_style("running")

        self.assertGreaterEqual(style["width"], 3.4 * IslandStackVisual.VISUAL_SCALE)
        self.assertGreater(style["pulse"], 0.0)

    def test_plugin_function_lines_include_alert_context(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        _title, lines = visual._plugin_function_lines("TIME", "00:24", "Countdown 0:24", "running", "Timer running", "Countdown 0:24")

        self.assertIn("Countdown 0:24", lines)

    def test_plugin_action_model_time_weather_agenda(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        time_action = visual._plugin_action_model("TIME", "00:24", "倒计时 0:24", "running")
        weather_action = visual._plugin_action_model("WEATHER", "26°C 晴", "北京 · 东南风", "normal")
        agenda_action = visual._plugin_action_model("AGENDA", "4 分钟后", "准备上课", "soon")

        self.assertEqual(time_action["command"], "timer-clear")
        self.assertIn("清除", time_action["label"])
        self.assertEqual(weather_action["command"], "weather-refresh")
        self.assertIn("刷新", weather_action["label"])
        self.assertEqual(agenda_action["command"], "agenda-open")
        self.assertIn("日程", agenda_action["label"])

    def test_plugin_panel_layout_has_nav_body_action_and_reminder_rail(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        rect = QRectF(0.0, 0.0, 420.0, 188.0)

        layout = visual._plugin_panel_layout(rect)

        self.assertGreaterEqual(layout["nav"].height(), 30.0)
        self.assertGreater(layout["body"].top(), layout["nav"].bottom())
        self.assertGreater(layout["action"].top(), layout["body"].top())
        self.assertGreater(layout["reminders"].top(), layout["nav"].bottom())
        self.assertLessEqual(layout["action"].right(), rect.right())

    def test_time_plugin_panel_has_clock_countdown_and_alarm_sections(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        sections = visual._plugin_panel_sections(
            {
                "label": "TIME",
                "detail": "19:04:12",
                "summary": "05/27 Wed",
                "status": "idle",
                "alert_title": "",
                "alert_body": "",
            }
        )
        layout = visual._plugin_panel_layout(QRectF(0.0, 0.0, 420.0, 188.0))

        self.assertEqual([section["id"] for section in sections], ["clock", "countdown", "alarm"])
        self.assertIn("倒计时", sections[1]["title"])
        self.assertIn("闹钟", sections[2]["title"])
        for key in ("time_clock", "time_countdown", "time_alarm"):
            self.assertIn(key, layout)
            self.assertGreater(layout[key].width(), 48.0)

    def test_time_plugin_panel_model_defaults_to_three_reference_modules(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        model = visual._time_plugin_panel_model(
            {
                "label": "TIME",
                "detail": "22:52",
                "summary": "05/27 Wed",
                "status": "idle",
                "progress": 0.0,
            }
        )

        self.assertEqual(model["mode"], "dashboard")
        self.assertEqual(model["clock"]["location"], "中国 北京")
        self.assertEqual([item["minutes"] for item in model["countdown_presets"]], [5, 15, 10, 20])
        self.assertFalse(model["countdown_active"]["active"])
        self.assertEqual(model["alarms"], [])

    def test_time_plugin_running_model_uses_center_ring(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        model = visual._time_plugin_panel_model(
            {
                "label": "TIME",
                "detail": "22:52",
                "summary": "倒计时 4:59",
                "status": "running",
                "progress": 0.45,
                "alert_body": "倒计时 4:59",
            }
        )

        self.assertTrue(model["countdown_active"]["active"])
        self.assertEqual(model["countdown_active"]["text"], "4:59")
        self.assertAlmostEqual(model["countdown_active"]["progress"], 0.45)

    def test_time_reference_palette_translates_solaris_tokens(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._system_dark_mode = lambda: True

        palette = visual._time_reference_palette(visual._plugin_theme_colors(), QColor(255, 204, 0))

        self.assertEqual(palette["surface"].getRgb()[:3], (19, 19, 19))
        self.assertEqual(palette["module"].getRgb()[:3], (28, 27, 27))
        self.assertEqual(palette["handle"].getRgb()[:3], (42, 42, 42))
        self.assertEqual(palette["accent"].getRgb()[:3], (255, 184, 0))
        self.assertEqual(palette["active"].getRgb()[:3], (2, 201, 83))
        self.assertEqual(palette["text"].getRgb()[:3], (229, 226, 225))

    def test_time_reference_palette_uses_light_alarm_panel_colors(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._system_dark_mode = lambda: False

        palette = visual._time_reference_palette(visual._plugin_theme_colors(), QColor(255, 204, 0))

        self.assertGreater(palette["surface"].red(), 220)
        self.assertGreater(palette["module"].red(), 245)
        self.assertLess(palette["text"].red(), 60)
        self.assertGreater(palette["divider"].alpha(), 120)

    def test_time_countdown_presets_paint_as_two_by_two_grid(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_time_mode = "dashboard"
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_structured_plugin_panel(
            painter,
            QRectF(20.0, 20.0, 420.0, 188.0),
            {
                "label": "TIME",
                "detail": "22:52",
                "summary": "05/27 Wed",
                "status": "idle",
                "color": QColor(255, 204, 0),
                "progress": 0.0,
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        rect5 = visual.plugin_action_rects["timer-start-5"]
        rect15 = visual.plugin_action_rects["timer-start-15"]
        rect10 = visual.plugin_action_rects["timer-start-10"]
        rect20 = visual.plugin_action_rects["timer-start-20"]
        self.assertAlmostEqual(rect5.top(), rect15.top(), delta=2.0)
        self.assertAlmostEqual(rect10.top(), rect20.top(), delta=2.0)
        self.assertGreater(rect10.top(), rect5.top() + 8.0)

    def test_time_countdown_presets_use_reference_pill_ratio(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        image = QImage(280, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_countdown_module(
            painter,
            QRectF(20.0, 20.0, 230.0, 100.0),
            {
                "countdown_active": {"active": False, "text": "", "progress": 0.0, "status": "idle"},
                "countdown_presets": [
                    {"minutes": 5, "label": "5"},
                    {"minutes": 15, "label": "15"},
                    {"minutes": 10, "label": "10"},
                    {"minutes": 20, "label": "20"},
                ],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        button = visual.plugin_action_rects["timer-start-5"]
        self.assertGreater(button.width(), button.height() * 1.8)

    def test_time_countdown_double_layout_presets_are_full_capsules_with_minutes(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        texts = []
        visual._paint_static_elided_text = lambda _painter, _rect, text, *_args, **_kwargs: texts.append(str(text))
        image = QImage(300, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_countdown_module(
            painter,
            QRectF(20.0, 20.0, 210.0, 100.0),
            {
                "time_layout_mode": "double",
                "countdown_active": {"active": False, "text": "", "progress": 0.0, "status": "idle"},
                "countdown_presets": [
                    {"minutes": 5, "label": "5"},
                    {"minutes": 15, "label": "15"},
                    {"minutes": 10, "label": "10"},
                    {"minutes": 20, "label": "20"},
                ],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        button = visual.plugin_action_rects["timer-start-5"]
        self.assertGreater(button.width(), button.height() * 1.8)
        self.assertIn("5分钟", texts)

    def test_time_countdown_stretch_presets_are_four_large_rings(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_countdown_module(
            painter,
            QRectF(20.0, 20.0, 380.0, 150.0),
            {
                "time_layout_mode": "stretch",
                "countdown_active": {"active": False, "text": "", "progress": 0.0, "status": "idle"},
                "countdown_presets": [
                    {"minutes": 5, "label": "5"},
                    {"minutes": 15, "label": "15"},
                    {"minutes": 10, "label": "10"},
                    {"minutes": 20, "label": "20"},
                ],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        buttons = [visual.plugin_action_rects[f"timer-start-{minutes}"] for minutes in (5, 15, 10, 20)]
        self.assertTrue(all(button.width() > visual._u(66.0) for button in buttons))
        self.assertTrue(all(abs(button.width() - button.height()) <= visual._u(8.0) for button in buttons))
        self.assertLess(buttons[0].left(), buttons[1].left())
        self.assertLess(buttons[1].left(), buttons[2].left())
        self.assertLess(buttons[2].left(), buttons[3].left())
        self.assertGreater(buttons[-1].right() - buttons[0].left(), visual._u(280.0))

    def test_time_panel_default_and_stretch_layouts_are_distinct(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        default_layout = visual._time_plugin_panel_layout(QRectF(0.0, 0.0, 560.0, 188.0))
        stretch_layout = visual._time_plugin_panel_layout(QRectF(0.0, 0.0, 880.0, 220.0))

        self.assertEqual(default_layout["time_layout_mode"], "triple")
        self.assertEqual(stretch_layout["time_layout_mode"], "stretch")
        self.assertGreater(default_layout["time_clock"].width(), 120.0)
        self.assertGreater(stretch_layout["time_countdown"].width(), default_layout["time_countdown"].width() + 40.0)
        self.assertGreater(stretch_layout["time_alarm"].width(), stretch_layout["time_countdown"].width())

    def test_time_panel_uses_double_layout_before_triple_modules_crush(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        layout = visual._time_plugin_panel_layout(QRectF(0.0, 0.0, 380.0, 156.0))

        self.assertEqual(layout["time_layout_mode"], "double")
        self.assertEqual(layout["time_visible_module"], "countdown")
        self.assertGreater(layout["time_clock"].width(), 120.0)
        self.assertGreater(layout["time_countdown"].width(), 120.0)
        self.assertEqual(layout["time_alarm"].width(), 0.0)
        self.assertIn("time_pager", layout)

    def test_time_dashboard_layout_keeps_only_title_not_split_strip(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        layout = visual._time_plugin_panel_layout(QRectF(0.0, 0.0, 560.0, 188.0))

        self.assertLessEqual(layout["body"].top() - layout["time_title"].bottom(), visual._u(8.0))
        self.assertLess(layout["time_title"].height(), visual._u(24.0))
        self.assertGreater(layout["time_countdown"].top(), layout["time_title"].bottom())

    def test_plugin_panel_height_adapts_for_narrow_time_and_editor(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"

        narrow_height = visual._plugin_panel_height(260.0)
        wide_height = visual._plugin_panel_height(620.0)
        visual.plugin_time_mode = "alarm_editor"
        editor_height = visual._plugin_panel_height(420.0)

        self.assertLess(narrow_height, wide_height)
        self.assertGreater(editor_height, narrow_height)
        self.assertGreaterEqual(editor_height, visual._u(168.0))

    def test_time_panel_wide_height_preserves_reference_module_depth(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        height = visual._plugin_panel_height(620.0)
        layout = visual._time_plugin_panel_layout(QRectF(0.0, 0.0, 620.0, height))

        self.assertGreaterEqual(height, visual._u(216.0))
        self.assertGreaterEqual(layout["time_countdown"].height(), visual._u(82.0))
        self.assertGreaterEqual(layout["time_alarm"].height(), visual._u(82.0))

    def test_time_panel_default_layout_is_taller_and_dense(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"

        height = visual._plugin_panel_height(560.0)
        layout = visual._time_plugin_panel_layout(QRectF(0.0, 0.0, 560.0, height))

        self.assertGreaterEqual(height, visual._u(236.0))
        self.assertEqual(layout["time_layout_mode"], "triple")
        self.assertGreaterEqual(layout["time_countdown"].height(), visual._u(140.0))
        self.assertLessEqual(height - layout["time_alarm"].bottom(), visual._u(10.0))

    def test_time_panel_layout_uses_height_without_large_bottom_gap(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        rect = QRectF(0.0, 0.0, 620.0, visual._plugin_panel_height(620.0))
        layout = visual._time_plugin_panel_layout(rect)

        self.assertLessEqual(rect.bottom() - layout["time_countdown"].bottom(), visual._u(12.0))
        self.assertGreaterEqual(layout["time_countdown"].height(), visual._u(120.0))

    def test_time_panel_stretch_layout_gives_timer_more_depth(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        layout = visual._time_plugin_panel_layout(QRectF(0.0, 0.0, 980.0, 236.0))

        self.assertEqual(layout["time_layout_mode"], "stretch")
        self.assertGreaterEqual(layout["time_countdown"].height(), visual._u(128.0))
        self.assertGreater(layout["time_countdown"].width(), visual._u(260.0))
        self.assertGreater(layout["time_alarm"].width(), visual._u(260.0))

    def test_time_countdown_running_ring_is_centered_like_reference(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        rect = QRectF(120.0, 40.0, 142.0, 88.0)

        ring = visual._time_countdown_ring_rect(rect)

        self.assertAlmostEqual(ring.center().x(), rect.center().x(), delta=1.0)
        self.assertAlmostEqual(ring.center().y(), rect.center().y() + visual._u(4.0), delta=2.0)
        self.assertLessEqual(ring.width(), rect.height() - visual._u(18.0))

    def test_time_countdown_expanded_ring_uses_double_weight(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        widths = visual._time_countdown_ring_widths()

        self.assertGreaterEqual(widths["width"], visual._u(7.6))
        self.assertGreaterEqual(widths["track_width"], visual._u(6.4))

    def test_time_panel_narrow_active_module_defaults_to_countdown(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertEqual(visual._time_visible_module_for_width(260.0), "countdown")

    def test_time_panel_wheel_pages_double_layout_right_module(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"
        visual.plugin_panel_last_width = 380.0
        visual.plugin_time_visible_module = "countdown"
        visual.update = lambda: None

        class FakeDelta:
            def __init__(self, y):
                self._y = y

            def y(self):
                return self._y

        class FakeWheel:
            def __init__(self, y):
                self.accepted = False
                self._delta = FakeDelta(y)

            def angleDelta(self):
                return self._delta

            def accept(self):
                self.accepted = True

        event = FakeWheel(-120)

        self.assertTrue(visual._handle_time_panel_module_wheel(event))
        self.assertEqual(visual.plugin_time_visible_module, "alarm")
        self.assertTrue(event.accepted)

        second = FakeWheel(-120)
        self.assertTrue(visual._handle_time_panel_module_wheel(second))
        self.assertEqual(visual.plugin_time_visible_module, "countdown")

    def test_time_alarm_save_caps_rows_at_eight(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_rows = [
            {"time": f"06:{index:02d}", "label": f"Alarm {index}", "enabled": True, "frequency": "daily"}
            for index in range(8)
        ]
        visual.update = lambda: None

        visual.plugin_alarm_editor = {"hour": 7, "minute": 45, "note": "New", "frequency": "每天", "frequency_unit": "daily", "weekdays": []}
        visual._activate_plugin_action("alarm-editor-save")

        self.assertEqual(len(visual.plugin_alarm_rows), 8)
        self.assertEqual(visual.plugin_alarm_rows[0]["time"], "07:45")

    def test_time_alarm_module_paints_scrollable_rows_and_yellow_add_button(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_rows = [
            {"time": f"06:{index:02d}", "label": f"Alarm {index}", "enabled": index % 2 == 0, "frequency": "daily"}
            for index in range(8)
        ]
        model = {"alarms": [dict(row) for row in visual.plugin_alarm_rows]}
        image = QImage(320, 180, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 240.0, 120.0)

        visual._paint_time_alarm_module(painter, rect, model, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        visible_edits = [key for key in visual.plugin_action_rects if re.fullmatch(r"alarm-edit-\d+", key)]
        self.assertLess(len(visible_edits), 8)
        self.assertIn("alarm-list", visual.plugin_action_rects)
        self.assertEqual(visual.plugin_alarm_visible_range[0], 0)
        self.assertEqual(visual.plugin_alarm_visible_range[1], 4)
        add = visual.plugin_action_rects["alarm-add-open"]
        self.assertGreaterEqual(add.height(), visual._u(27.0))
        pixel = image.pixelColor(QPoint(int(add.left() + visual._u(10.0)), int(add.center().y())))
        self.assertGreater(pixel.red(), 210)
        self.assertGreater(pixel.green(), 140)
        self.assertLess(pixel.blue(), 80)
        self.assertLess(visual.plugin_alarm_list_rect.bottom(), add.bottom())
        self.assertGreater(visual.plugin_alarm_list_max_scroll, 4.0)

    def test_alarm_due_confirmation_replaces_scroll_list_and_add_button(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_rows = [
            {"time": f"06:{index:02d}", "label": f"Alarm {index}", "enabled": True, "frequency": "daily"}
            for index in range(6)
        ]
        module_rect = QRectF(20.0, 20.0, 280.0, 150.0)
        image = QImage(360, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(
            painter,
            module_rect,
            {
                "alarms": [dict(row) for row in visual.plugin_alarm_rows],
                "alarm_due_pending": {"time": "07:30", "label": "晨会"},
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        due = visual.plugin_action_rects["alarm-due-confirm"]
        self.assertNotIn("alarm-list", visual.plugin_action_rects)
        self.assertNotIn("alarm-add-open", visual.plugin_action_rects)
        self.assertGreaterEqual(due.height(), module_rect.height() - visual._u(48.0))
        self.assertGreaterEqual(due.height(), module_rect.height())
        self.assertEqual(getattr(visual, "plugin_alarm_visible_range", None), (0, 0))

    def test_alarm_due_confirmation_paints_full_module_block(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        module_rect = QRectF(20.0, 20.0, 280.0, 150.0)
        image = QImage(360, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(
            painter,
            module_rect,
            {
                "alarms": [],
                "alarm_due_pending": {"time": "07:30", "label": "晨会"},
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        header_pixel = image.pixelColor(QPoint(int(module_rect.right() - visual._u(18.0)), int(module_rect.top() + visual._u(14.0))))
        self.assertGreater(header_pixel.red(), 210)
        self.assertGreater(header_pixel.green(), 150)
        self.assertLess(header_pixel.blue(), 90)

    def test_time_alarm_add_button_text_is_spaced_and_bold(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_rows = [{"time": "06:43", "label": "起床", "enabled": True, "frequency": "daily"}]
        calls = []
        visual._paint_static_elided_text = lambda _painter, _rect, text, size, _color, bold, *_args, **_kwargs: calls.append((str(text), int(size), bool(bold)))
        image = QImage(320, 180, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(painter, QRectF(20.0, 20.0, 240.0, 120.0), {"alarms": visual.plugin_alarm_rows}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertIn(("添 加 闹 钟", 10, True), calls)

    def test_time_alarm_edit_button_is_removed_and_row_body_opens_editor(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.plugin_time_mode = "dashboard"
        visual._main_kind = lambda: IslandKind.PLUGIN
        visual.plugin_action_rects = {}
        visual.plugin_alarm_rows = [{"time": "06:43", "label": "起床", "enabled": True, "frequency": "daily"}]
        image = QImage(320, 180, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(painter, QRectF(20.0, 20.0, 240.0, 120.0), {"alarms": visual.plugin_alarm_rows}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertNotIn("alarm-edit-button-0", visual.plugin_action_rects)
        edit = visual.plugin_action_rects["alarm-edit-0"]
        toggle = visual.plugin_action_rects["alarm-toggle-0"]
        self.assertLess(edit.right(), toggle.left())
        self.assertEqual(visual._hit_test(edit.center()), "plugin-action:alarm-edit-0")

    def test_time_alarm_list_long_notes_scroll_without_scrolling_time(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_rows = [{"time": "15:32", "label": "吃点东西搞点", "enabled": True, "frequency": "daily"}]
        calls = []
        visual._paint_scrolling_text = lambda _painter, _rect, text, size, _color, bold, *_args, **_kwargs: calls.append((str(text), int(size), bool(bold)))
        image = QImage(320, 180, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(painter, QRectF(20.0, 20.0, 240.0, 120.0), {"alarms": visual.plugin_alarm_rows}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertIn(("吃点东西搞点", 10, True), calls)
        self.assertFalse(any("15:32" in text for text, _size, _bold in calls))

    def test_time_alarm_list_wheel_scrolls_until_bounds_then_pages_module(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"
        visual.plugin_panel_last_width = 380.0
        visual.plugin_time_visible_module = "alarm"
        visual.plugin_alarm_list_scroll_index = 0.0
        visual.plugin_alarm_list_rect = QRectF(100.0, 50.0, 180.0, 70.0)
        visual.plugin_alarm_visible_range = (0, 4)
        visual.plugin_alarm_rows = [
            {"time": f"06:{index:02d}", "label": f"Alarm {index}", "enabled": True, "frequency": "daily"}
            for index in range(8)
        ]
        visual.update = lambda: None

        class FakeDelta:
            def __init__(self, y):
                self._y = y

            def y(self):
                return self._y

        class FakeWheel:
            def __init__(self, y, pos):
                self.accepted = False
                self._delta = FakeDelta(y)
                self._pos = pos

            def angleDelta(self):
                return self._delta

            def position(self):
                return self._pos

            def accept(self):
                self.accepted = True

        down = FakeWheel(-120, QPointF(130.0, 70.0))
        self.assertTrue(visual._handle_time_panel_module_wheel(down))
        self.assertEqual(visual.plugin_time_visible_module, "alarm")
        self.assertEqual(visual.plugin_alarm_list_scroll_index, 1.0)
        self.assertTrue(down.accepted)

        visual.plugin_alarm_list_scroll_index = 4.0
        visual.plugin_alarm_list_bottom_spacer_rows = 1.0
        bottom_down = FakeWheel(-120, QPointF(130.0, 70.0))
        self.assertTrue(visual._handle_time_panel_module_wheel(bottom_down))
        self.assertGreater(visual.plugin_alarm_list_scroll_index, 4.0)
        self.assertEqual(visual.plugin_time_visible_module, "alarm")

        visual.plugin_alarm_list_scroll_index = 5.0
        true_bottom_down = FakeWheel(-120, QPointF(130.0, 70.0))
        self.assertTrue(visual._handle_time_panel_module_wheel(true_bottom_down))
        self.assertEqual(visual.plugin_alarm_list_scroll_index, 5.0)
        self.assertEqual(visual.plugin_time_visible_module, "countdown")

        visual.plugin_time_visible_module = "alarm"
        visual.plugin_alarm_list_scroll_index = 0
        outside = FakeWheel(-120, QPointF(20.0, 20.0))
        self.assertTrue(visual._handle_time_panel_module_wheel(outside))
        self.assertEqual(visual.plugin_time_visible_module, "countdown")

    def test_time_alarm_list_wheel_supports_continuous_fractional_scroll(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"
        visual.plugin_panel_last_width = 380.0
        visual.plugin_time_visible_module = "alarm"
        visual.plugin_alarm_list_scroll_index = 0.0
        visual.plugin_alarm_list_rect = QRectF(100.0, 50.0, 180.0, 70.0)
        visual.plugin_alarm_visible_range = (0, 4)
        visual.plugin_alarm_rows = [
            {"time": f"06:{index:02d}", "label": f"Alarm {index}", "enabled": True, "frequency": "daily"}
            for index in range(8)
        ]
        visual.update = lambda: None

        class FakeDelta:
            def y(self):
                return -60

        class FakeWheel:
            accepted = False

            def angleDelta(self):
                return FakeDelta()

            def position(self):
                return QPointF(130.0, 70.0)

            def accept(self):
                self.accepted = True

        event = FakeWheel()
        self.assertTrue(visual._handle_time_panel_module_wheel(event))
        self.assertGreater(visual.plugin_alarm_list_scroll_index, 0.0)
        self.assertLess(visual.plugin_alarm_list_scroll_index, 1.0)
        self.assertEqual(visual.plugin_time_visible_module, "alarm")

    def test_alarm_list_can_scroll_into_bottom_spacer_when_row_count_matches_view(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"
        visual.plugin_panel_last_width = 380.0
        visual.plugin_time_visible_module = "alarm"
        visual.plugin_alarm_list_scroll_index = 0.0
        visual.plugin_alarm_list_bottom_spacer_rows = 1.0
        visual.plugin_alarm_list_rect = QRectF(100.0, 50.0, 180.0, 70.0)
        visual.plugin_alarm_visible_range = (0, 4)
        visual.plugin_alarm_rows = [
            {"time": f"06:{index:02d}", "label": f"Alarm {index}", "enabled": True, "frequency": "daily"}
            for index in range(4)
        ]
        visual.update = lambda: None

        class FakeDelta:
            def y(self):
                return -120

        class FakeWheel:
            def angleDelta(self):
                return FakeDelta()

            def position(self):
                return QPointF(130.0, 70.0)

            def accept(self):
                pass

        self.assertTrue(visual._handle_time_panel_module_wheel(FakeWheel()))

        self.assertGreater(visual.plugin_alarm_list_scroll_index, 0.0)
        self.assertEqual(visual.plugin_time_visible_module, "alarm")

    def test_time_alarm_list_wheel_scrolls_in_stretch_layout(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"
        visual.plugin_panel_last_width = 980.0
        visual.plugin_time_visible_module = "countdown"
        visual.plugin_alarm_list_scroll_index = 0.0
        visual.plugin_alarm_list_rect = QRectF(100.0, 50.0, 260.0, 100.0)
        visual.plugin_alarm_visible_range = (0, 4)
        visual.plugin_alarm_rows = [
            {"time": f"06:{index:02d}", "label": f"Alarm {index}", "enabled": True, "frequency": "daily"}
            for index in range(8)
        ]
        visual.update = lambda: None

        class FakeDelta:
            def y(self):
                return -120

        class FakeWheel:
            accepted = False

            def angleDelta(self):
                return FakeDelta()

            def position(self):
                return QPointF(130.0, 70.0)

            def accept(self):
                self.accepted = True

        event = FakeWheel()

        self.assertTrue(visual._handle_time_alarm_list_wheel(event))
        self.assertEqual(visual.plugin_alarm_list_scroll_index, 1.0)
        self.assertEqual(visual.plugin_time_visible_module, "countdown")
        self.assertTrue(event.accepted)

    def test_time_panel_module_wheel_starts_vertical_transition_animation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"
        visual.plugin_panel_last_width = 380.0
        visual.plugin_time_visible_module = "countdown"
        visual.update = lambda: None

        class FakeDelta:
            def y(self):
                return -120

        class FakeWheel:
            def angleDelta(self):
                return FakeDelta()

            def position(self):
                return QPointF(20.0, 20.0)

            def accept(self):
                pass

        self.assertTrue(visual._handle_time_panel_module_wheel(FakeWheel()))

        self.assertEqual(visual.plugin_time_visible_module, "alarm")
        self.assertEqual(visual.plugin_time_module_animation["from"], "countdown")
        self.assertEqual(visual.plugin_time_module_animation["to"], "alarm")

    def test_time_module_shell_uses_dark_fill_without_yellow_overlay(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        image = QImage(220, 130, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 160.0, 84.0)

        visual._paint_time_module_shell(painter, rect, visual._plugin_theme_colors(), active=True)
        painter.end()

        pixel = image.pixelColor(rect.center().toPoint())
        self.assertLess(pixel.red() - pixel.blue(), 20)
        self.assertLess(pixel.green() - pixel.blue(), 20)

    def test_time_countdown_idle_does_not_register_pause_resume_or_cancel(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        image = QImage(280, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_countdown_module(
            painter,
            QRectF(20.0, 20.0, 150.0, 100.0),
            {
                "countdown_active": {"active": False, "text": "", "progress": 0.0, "status": "idle"},
                "countdown_presets": [
                    {"minutes": 5, "label": "5"},
                    {"minutes": 15, "label": "15"},
                    {"minutes": 10, "label": "10"},
                    {"minutes": 20, "label": "20"},
                ],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        self.assertIn("timer-start-5", visual.plugin_action_rects)
        for command in ("timer-pause", "timer-resume", "timer-clear"):
            self.assertNotIn(command, visual.plugin_action_rects)

    def test_time_countdown_running_default_ring_only_registers_pause(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        image = QImage(280, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 150.0, 100.0)

        visual._paint_time_countdown_module(
            painter,
            rect,
            {
                "countdown_active": {"active": True, "text": "4:59", "progress": 0.45, "status": "running"},
                "countdown_presets": [],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        self.assertIn("timer-pause", visual.plugin_action_rects)
        self.assertNotIn("timer-clear", visual.plugin_action_rects)
        self.assertLess(abs(visual.plugin_action_rects["timer-pause"].center().x() - rect.center().x()), 8.0)

    def test_time_countdown_paused_default_shows_resume_and_cancel(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        image = QImage(280, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_countdown_module(
            painter,
            QRectF(20.0, 20.0, 150.0, 100.0),
            {
                "countdown_active": {"active": True, "text": "4:59", "progress": 0.45, "status": "paused"},
                "countdown_presets": [],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        self.assertIn("timer-resume", visual.plugin_action_rects)
        self.assertIn("timer-clear", visual.plugin_action_rects)

    def test_time_countdown_paused_ring_body_is_not_clickable(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual._main_kind = lambda: IslandKind.PLUGIN
        visual.plugin_action_rects = {}
        visual.motions = {}
        image = QImage(280, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 150.0, 100.0)

        visual._paint_time_countdown_module(
            painter,
            rect,
            {
                "countdown_active": {"active": True, "text": "4:59", "progress": 0.45, "status": "paused"},
                "countdown_presets": [],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()
        ring = visual._time_countdown_ring_rect(rect)

        self.assertEqual(visual._hit_test(ring.center()), "")
        self.assertEqual(visual._hit_test(visual.plugin_action_rects["timer-resume"].center()), "plugin-action:timer-resume")
        self.assertEqual(visual._hit_test(visual.plugin_action_rects["timer-clear"].center()), "plugin-action:timer-clear")

    def test_time_countdown_done_ring_click_clears_to_presets(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual._main_kind = lambda: IslandKind.PLUGIN
        visual.plugin_action_rects = {}
        image = QImage(280, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 150.0, 100.0)

        visual._paint_time_countdown_module(
            painter,
            rect,
            {
                "countdown_active": {"active": True, "text": "0:00", "progress": 1.0, "status": "done"},
                "countdown_presets": [],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()
        ring = visual._time_countdown_ring_rect(rect)

        self.assertNotIn("timer-pause", visual.plugin_action_rects)
        self.assertEqual(visual._hit_test(ring.center()), "plugin-action:timer-clear")

    def test_time_countdown_ring_click_pauses_through_hit_test_dispatch(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual._main_kind = lambda: IslandKind.PLUGIN
        visual.plugin_action_rects = {}
        visual._pulse_stack = lambda: None
        now = datetime.now().astimezone()
        settings = {
            "v2_timer_started_at": now.isoformat(timespec="seconds"),
            "v2_timer_end_at": (now + timedelta(minutes=5)).isoformat(timespec="seconds"),
        }
        visual.plugin_countdown_entries = [
            {
                "minutes": 5,
                "started_at": settings["v2_timer_started_at"],
                "end_at": settings["v2_timer_end_at"],
                "status": "running",
            }
        ]
        visual.settings_getter = lambda key, default=None: settings.get(key, default)
        visual.settings_setter = lambda key, value: settings.__setitem__(key, value)
        visual.refresh_plugin_data = lambda: None
        image = QImage(280, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 150.0, 100.0)

        visual._paint_time_countdown_module(
            painter,
            rect,
            {
                "countdown_active": {"active": True, "text": "4:59", "progress": 0.45, "status": "running"},
                "countdown_presets": [],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()
        key = visual._hit_test(visual.plugin_action_rects["timer-pause"].center())

        self.assertEqual(key, "plugin-action:timer-pause")
        visual._activate_key(key)
        active = visual._time_countdown_active_model({"status": "idle"})
        entries = visual._time_countdown_entries({"countdown_active": active})

        self.assertEqual(entries[0]["status"], "paused")
        self.assertEqual(settings["v2_timer_end_at"], "")

    def test_time_countdown_cancel_clears_stale_entries(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_timer_paused_remaining_seconds = 120
        visual.plugin_countdown_entries = [{"text": "2:00", "status": "paused", "progress": 0.5}]
        settings = {"v2_timer_started_at": "old", "v2_timer_end_at": "old"}
        visual.settings_setter = lambda key, value: settings.__setitem__(key, value)
        visual.refresh_plugin_data = lambda: None

        visual._activate_plugin_action("timer-clear")

        self.assertEqual(visual.plugin_countdown_entries, [])
        self.assertEqual(settings["v2_timer_started_at"], "")
        self.assertEqual(settings["v2_timer_end_at"], "")

    def test_time_countdown_entries_keep_two_most_recent(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_countdown_entries = [
            {"text": "14:59", "progress": 0.10, "status": "running"},
            {"text": "9:59", "progress": 0.20, "status": "running"},
            {"text": "4:59", "progress": 0.45, "status": "running"},
        ]

        entries = visual._time_countdown_entries({"countdown_active": {"active": False}})

        self.assertEqual([entry["text"] for entry in entries], ["4:59", "9:59"])

    def test_time_countdown_paused_model_uses_remaining_seconds(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_timer_paused_remaining_seconds = 249
        visual.plugin_timer_paused_progress = 0.42

        active = visual._time_countdown_active_model({"status": "idle", "progress": 0.0})

        self.assertTrue(active["active"])
        self.assertEqual(active["status"], "paused")
        self.assertEqual(active["text"], "4:09")
        self.assertAlmostEqual(active["progress"], 0.42)

    def test_time_countdown_pause_resume_and_cancel_update_timer_settings(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        now = datetime.now().astimezone()
        settings = {
            "v2_timer_started_at": now.isoformat(timespec="seconds"),
            "v2_timer_end_at": (now + timedelta(minutes=5)).isoformat(timespec="seconds"),
        }
        calls = []
        visual.settings_getter = lambda key, default=None: settings.get(key, default)

        def set_setting(key, value):
            settings[key] = value
            calls.append((key, value))

        visual.settings_setter = set_setting
        visual.refresh_plugin_data = lambda: calls.append(("refresh", "plugins"))
        visual.update = lambda: None

        visual._activate_plugin_action("timer-pause")

        self.assertGreater(visual.plugin_timer_paused_remaining_seconds, 0)
        self.assertEqual(settings["v2_timer_end_at"], "")

        visual._activate_plugin_action("timer-resume")

        self.assertEqual(visual.plugin_timer_paused_remaining_seconds, 0)
        self.assertNotEqual(settings["v2_timer_end_at"], "")

        visual._activate_plugin_action("timer-clear")

        self.assertEqual(settings["v2_timer_end_at"], "")
        self.assertEqual(visual.plugin_timer_paused_remaining_seconds, 0)

    def test_time_clock_hand_angles_use_displayed_time(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        angles = visual._time_clock_hand_angles("15:22:30")

        self.assertAlmostEqual(angles["hour"], 101.25, places=2)
        self.assertAlmostEqual(angles["minute"], 135.0, places=2)
        self.assertAlmostEqual(angles["second"], 180.0, places=2)

    def test_alarm_picker_display_values_show_neighbor_rows(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency": "每周", "weekdays": "二、四"}

        hour_values = visual._alarm_picker_display_values("hour")
        frequency_values = visual._alarm_picker_display_values("frequency")

        hour_center = len(hour_values) // 2
        frequency_center = len(frequency_values) // 2
        self.assertEqual(len(hour_values), 7)
        self.assertEqual([item["text"] for item in hour_values[hour_center - 1 : hour_center + 2]], ["14", "15", "16"])
        self.assertFalse(hour_values[hour_center - 1]["current"])
        self.assertTrue(hour_values[hour_center]["current"])
        self.assertLess(hour_values[hour_center - 1]["opacity"], hour_values[hour_center]["opacity"])
        self.assertEqual(len(frequency_values), 7)
        self.assertEqual([item["text"] for item in frequency_values[frequency_center - 1 : frequency_center + 2]], ["仅一次", "每周", "每天"])

    def test_time_countdown_preset_action_starts_timer(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        calls = []
        visual.settings_setter = lambda key, value: calls.append((key, value))
        visual.refresh_plugin_data = lambda: calls.append(("refresh", "plugins"))

        visual._activate_plugin_action("timer-start-5")

        keys = [key for key, _value in calls if key != "refresh"]
        self.assertIn("v2_timer_started_at", keys)
        self.assertIn("v2_timer_end_at", keys)
        self.assertEqual(calls[-1], ("refresh", "plugins"))

    def test_time_countdown_preset_action_keeps_two_recent_timers(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_countdown_entries = []
        visual.settings_setter = lambda _key, _value: None
        visual.refresh_plugin_data = lambda: None

        visual._activate_plugin_action("timer-start-5")
        visual._activate_plugin_action("timer-start-10")
        visual._activate_plugin_action("timer-start-20")

        self.assertEqual(len(visual.plugin_countdown_entries), 2)
        self.assertEqual([entry["minutes"] for entry in visual.plugin_countdown_entries], [10, 20])

    def test_alarm_add_opens_inline_editor(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        visual._activate_plugin_action("alarm-add-open")

        self.assertEqual(visual.plugin_time_mode, "alarm_editor")
        editor = visual._time_plugin_panel_model({"label": "TIME", "detail": "22:52", "summary": "", "status": "idle"})
        self.assertEqual(editor["mode"], "alarm_editor")
        self.assertIn("hour", editor["editor"])

    def test_alarm_editor_wheel_updates_hour_minute_and_frequency(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency": "每周", "weekdays": "二、四"}

        visual._adjust_alarm_editor_field("hour", 1)
        visual._adjust_alarm_editor_field("minute", -2)
        visual._adjust_alarm_editor_field("frequency", 1)

        self.assertEqual(visual.plugin_alarm_editor["hour"], 16)
        self.assertEqual(visual.plugin_alarm_editor["minute"], 20)
        self.assertEqual(visual.plugin_alarm_editor["frequency"], "每天")

    def test_alarm_editor_note_accepts_keyboard_text_and_backspace(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency": "每周", "weekdays": "二、四"}
        visual.plugin_alarm_editor_focus = "note"
        visual.update = lambda: None

        visual._handle_alarm_editor_key("早")
        visual._handle_alarm_editor_key("课")
        visual._handle_alarm_editor_key("\b")

        self.assertEqual(visual.plugin_alarm_editor["note"], "早")

    def test_alarm_editor_note_accepts_input_method_commit_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency": "每周", "weekdays": "二、四"}
        visual.plugin_alarm_editor_focus = "note"
        visual.update = lambda: None

        class FakeInputMethodEvent:
            accepted = False

            def commitString(self):
                return "早课"

            def accept(self):
                self.accepted = True

        self.assertIn("inputMethodEvent", IslandStackVisual.__dict__)
        event = FakeInputMethodEvent()
        IslandStackVisual.inputMethodEvent(visual, event)

        self.assertEqual(visual.plugin_alarm_editor["note"], "早课")
        self.assertTrue(event.accepted)

    def test_alarm_editor_note_is_limited_to_ten_chars(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {"hour": 7, "minute": 30, "note": "", "frequency": "每天", "weekdays": "二、四"}
        visual.plugin_alarm_editor_focus = "note"
        visual.update = lambda: None

        for char in "一二三四五六七八九十十一":
            visual._handle_alarm_editor_key(char)

        self.assertEqual(visual.plugin_alarm_editor["note"], "一二三四五六七八九十")

    def test_alarm_editor_note_over_limit_sets_hint_without_changing_note(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {"hour": 7, "minute": 30, "note": "一二三四五六七八九十", "frequency": "每天", "weekdays": "二、四"}
        visual.plugin_alarm_editor_focus = "note"
        visual.update = lambda: None

        with patch("local.island_stack_visual.time.monotonic", return_value=44.0):
            self.assertTrue(visual._handle_alarm_editor_key("余"))

        self.assertEqual(visual.plugin_alarm_editor["note"], "一二三四五六七八九十")
        self.assertEqual(visual.plugin_alarm_editor_note_hint, "最多10字")
        self.assertGreater(visual.plugin_alarm_editor_note_hint_until, 44.0)

    def test_alarm_editor_note_focus_paints_caret(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor = {"hour": 7, "minute": 30, "note": "早课", "frequency_unit": "daily", "editor_page": "note"}
        visual.plugin_alarm_editor_focus = "note"
        calls = []
        visual._paint_alarm_editor_caret = lambda _painter, _rect, field, _ref: calls.append(field)
        image = QImage(520, 160, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_editor(painter, QRectF(20.0, 20.0, 400.0, 120.0), {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertIn("note", calls)

    def test_alarm_editor_long_note_scrolls_inside_note_field(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor = {"hour": 7, "minute": 30, "note": "一二三四五六", "frequency_unit": "daily", "editor_page": "note"}
        visual.plugin_alarm_editor_focus = "note"
        calls = []
        visual._paint_scrolling_text = lambda _painter, _rect, text, size, _color, bold, *_args, **_kwargs: calls.append((str(text), int(size), bool(bold)))
        image = QImage(520, 160, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_editor(painter, QRectF(20.0, 20.0, 400.0, 120.0), {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertIn(("一二三四五六", 15, True), calls)

    def test_alarm_note_display_budget_is_five_chars_and_long_notes_scroll(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertEqual(visual._time_alarm_note_display_text("提醒一二三四五六"), "提醒一二三四五六")
        self.assertFalse(visual._time_alarm_note_should_scroll("五字内!", QRectF(0.0, 0.0, 300.0, 32.0), 10, True, 5))
        self.assertTrue(visual._time_alarm_note_should_scroll("超过五字了啊", QRectF(0.0, 0.0, 300.0, 32.0), 10, True, 5))

    def test_alarm_recurrence_label_handles_week_month_and_year(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertEqual(visual._alarm_recurrence_label({"frequency_unit": "weekly", "weekdays": [2, 4, 6]}), "每周")
        self.assertEqual(visual._alarm_recurrence_value_label({"frequency_unit": "weekly", "weekdays": [2, 4, 6]}), "2 4 6")
        self.assertEqual(visual._alarm_recurrence_value_label({"frequency_unit": "weekly", "weekdays": [1, 2, 3, 4, 5, 6, 7]}), "整周")
        self.assertEqual(visual._alarm_recurrence_label({"frequency_unit": "monthly", "month_day": 29}), "每非明年二月")
        self.assertEqual(visual._alarm_recurrence_label({"frequency_unit": "monthly", "month_day": 31}), "每大月")
        self.assertEqual(visual._alarm_recurrence_value_label({"frequency_unit": "monthly", "month_day": 28}), "28号")
        self.assertEqual(visual._alarm_recurrence_label({"frequency_unit": "yearly", "year_month": 4, "year_day": 28}), "每年")
        self.assertEqual(visual._alarm_recurrence_value_label({"frequency_unit": "yearly", "year_month": 4, "year_day": 28}), "4月28日")

    def test_alarm_editor_save_uses_note_as_alarm_label(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_rows = []
        visual.plugin_alarm_editor = {"hour": 7, "minute": 30, "note": "早课", "frequency": "每天", "weekdays": "每天"}
        visual.update = lambda: None

        visual._activate_plugin_action("alarm-editor-save")

        self.assertEqual(visual.plugin_alarm_rows[0]["time"], "07:30")
        self.assertEqual(visual.plugin_alarm_rows[0]["label"], "早课")
        self.assertEqual(visual.plugin_time_mode, "dashboard")

    def test_alarm_editor_save_persists_recurrence_fields(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_rows = []
        visual.plugin_alarm_editor = {
            "hour": 15,
            "minute": 22,
            "note": "喝水",
            "frequency_unit": "weekly",
            "weekdays": [2, 4, 6],
        }
        visual.update = lambda: None

        visual._activate_plugin_action("alarm-editor-save")

        row = visual.plugin_alarm_rows[0]
        self.assertEqual(row["frequency"], "每周")
        self.assertEqual(row["weekdays"], [2, 4, 6])
        self.assertEqual(row["label"], "喝水")

    def test_alarm_editor_reference_layout_uses_single_row_fields(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        layout = visual._time_alarm_editor_layout(QRectF(0.0, 0.0, 480.0, 142.0))

        self.assertEqual([name for name in ("time", "note", "frequency", "weekdays") if name in layout], ["time", "note", "frequency", "weekdays"])
        tops = [layout[name].top() for name in ("time", "note", "frequency", "weekdays")]
        self.assertLess(max(tops) - min(tops), 1.0)
        self.assertLess(layout["title"].top(), layout["time"].top())
        self.assertGreater(layout["time_label"].top(), layout["time"].bottom())
        self.assertGreater(layout["note"].left(), layout["time"].right())
        self.assertGreater(layout["frequency"].left(), layout["note"].right())
        self.assertGreater(layout["weekdays"].left(), layout["frequency"].right())
        self.assertGreater(layout["time"].width(), layout["frequency"].width())

    def test_alarm_editor_reference_actions_are_bottom_wide_buttons(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        rect = QRectF(0.0, 0.0, 500.0, 168.0)

        layout = visual._time_alarm_editor_layout(rect)

        self.assertGreater(layout["save"].top(), layout["time_label"].bottom())
        self.assertGreater(layout["cancel"].top(), layout["time_label"].bottom())
        self.assertGreaterEqual(layout["save"].width(), visual._u(118.0))
        self.assertGreaterEqual(layout["cancel"].width(), visual._u(118.0))
        self.assertGreaterEqual(layout["save"].height(), visual._u(30.0))
        self.assertLess(layout["cancel"].right(), layout["save"].left())
        self.assertLessEqual(layout["save"].bottom(), rect.bottom() - visual._u(7.0))

    def test_alarm_editor_reference_width_uses_paged_narrow_layout(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        layout = visual._time_alarm_editor_layout(QRectF(0.0, 0.0, 420.0, 156.0))

        self.assertEqual(layout["mode"], "narrow")
        self.assertIn("main_note_dot", layout)
        self.assertLess(layout["main_frequency"].left(), layout["main_note_dot"].left())
        self.assertGreater(layout["note"].width(), layout["main_time"].width() * 2.4)

    def test_alarm_editor_narrow_actions_are_bottom_wide_buttons(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        rect = QRectF(0.0, 0.0, 420.0, 190.0)

        layout = visual._time_alarm_editor_layout(rect)

        self.assertEqual(layout["mode"], "narrow")
        self.assertGreater(layout["save"].top(), layout["time_label"].bottom())
        self.assertGreaterEqual(layout["save"].width(), visual._u(118.0))
        self.assertGreaterEqual(layout["cancel"].width(), visual._u(118.0))
        self.assertAlmostEqual(layout["save"].top(), layout["cancel"].top(), delta=1.0)
        self.assertLessEqual(layout["save"].bottom(), rect.bottom() - visual._u(7.0))

    def test_alarm_editor_narrow_layout_uses_available_height(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        rect = QRectF(0.0, 0.0, 420.0, 156.0)

        layout = visual._time_alarm_editor_layout(rect)

        self.assertGreaterEqual(layout["main_time"].height(), visual._u(68.0))
        self.assertGreaterEqual(layout["time_label"].bottom(), rect.top() + rect.height() * 0.72)
        self.assertLessEqual(layout["time_label"].bottom(), rect.bottom() - visual._u(18.0))

    def test_alarm_editor_narrow_layout_has_main_and_note_pages(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        layout = visual._time_alarm_editor_layout(QRectF(0.0, 0.0, 320.0, 156.0))

        self.assertEqual(layout["mode"], "narrow")
        self.assertIn("main_page", layout)
        self.assertIn("note_page", layout)
        self.assertIn("main_frequency", layout)
        self.assertLess(layout["main_frequency"].left(), layout["main_note_dot"].left())
        self.assertGreater(layout["note"].width(), layout["time"].width())

    def test_alarm_editor_narrow_paint_registers_paged_fields(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "weekly", "weekdays": [2, 4, 6]}
        model = {"editor": visual.plugin_alarm_editor}
        image = QImage(360, 190, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_editor(painter, QRectF(12.0, 12.0, 320.0, 156.0), model, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertIn("hour", visual.plugin_alarm_editor_rects)
        self.assertIn("frequency_unit", visual.plugin_alarm_editor_rects)
        self.assertIn("alarm-editor-page-note", visual.plugin_action_rects)
        self.assertIn("alarm-editor-open-weekdays", visual.plugin_action_rects)

    def test_alarm_editor_daily_frequency_reflows_center_without_value_field(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "daily", "editor_page": "main"}
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 14.0, 420.0, 190.0)

        visual._paint_time_alarm_editor(painter, rect, {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        layout = visual._time_alarm_editor_layout(rect)
        frequency = visual.plugin_alarm_editor_rects["frequency_unit"]
        slot_left = layout["main_time"].right() + visual._u(16.0)
        slot_right = layout["main_note_dot"].left() - visual._u(8.0)
        slot_center = (slot_left + slot_right) / 2.0
        self.assertAlmostEqual(frequency.center().x(), slot_center, delta=visual._u(4.0))
        self.assertGreater(frequency.width(), layout["main_frequency"].width())

    def test_alarm_editor_frequency_unit_reflow_records_animation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_editor = {"frequency_unit": "weekly", "hour": 15, "minute": 22, "note": ""}
        visual.update = lambda: None

        with patch("local.island_stack_visual.time.monotonic", return_value=123.0):
            visual._adjust_alarm_editor_field("frequency_unit", -1)

        self.assertEqual(visual.plugin_alarm_editor["frequency_unit"], "daily")
        self.assertEqual(visual.plugin_alarm_editor_reflow_animation["from"], "weekly")
        self.assertEqual(visual.plugin_alarm_editor_reflow_animation["to"], "daily")
        self.assertEqual(visual.plugin_alarm_editor_reflow_animation["started_at"], 123.0)

    def test_alarm_editor_paint_registers_figure_two_fields_without_handle_strip(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        model = {"editor": {"hour": 15, "minute": 22, "note": "", "frequency": "每周", "weekdays": "二、四"}}
        image = QImage(540, 170, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 14.0, 500.0, 142.0)

        visual._paint_time_alarm_editor(painter, rect, model, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        layout = visual._time_alarm_editor_layout(rect)
        self.assertIn("hour", visual.plugin_alarm_editor_rects)
        self.assertIn("minute", visual.plugin_alarm_editor_rects)
        self.assertIn("frequency", visual.plugin_alarm_editor_rects)
        self.assertIn("weekdays", visual.plugin_alarm_editor_rects)
        self.assertEqual(visual.plugin_action_rects["alarm-editor-note"], layout["note"])
        self.assertGreaterEqual(visual.plugin_alarm_editor_rects["hour"].left(), layout["time"].left())
        self.assertLessEqual(visual.plugin_alarm_editor_rects["minute"].right(), layout["time"].right())
        self.assertLess(layout["title"].height(), visual._u(24.0))

    def test_alarm_editor_stretch_uses_dynamic_frequency_fields(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {
            "hour": 15,
            "minute": 22,
            "note": "Tea",
            "frequency_unit": "monthly",
            "month_day": 31,
            "editor_page": "main",
            "detail_mode": "",
        }
        image = QImage(800, 230, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_editor(
            painter,
            QRectF(20.0, 16.0, 720.0, 190.0),
            {"editor": visual.plugin_alarm_editor},
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        self.assertIn("frequency_unit", visual.plugin_alarm_editor_rects)
        self.assertIn("month_day", visual.plugin_alarm_editor_rects)
        self.assertIn("alarm-editor-focus-frequency_unit", visual.plugin_action_rects)
        self.assertIn("alarm-editor-open-month-day", visual.plugin_action_rects)

    def test_alarm_editor_paints_visible_confirm_button(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        model = {"editor": {"hour": 19, "minute": 0, "note": "起床", "frequency": "每天", "weekdays": "二、四"}}
        image = QImage(540, 170, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 14.0, 500.0, 142.0)

        visual._paint_time_alarm_editor(painter, rect, model, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        save = visual.plugin_action_rects["alarm-editor-save"]
        pixel = image.pixelColor(save.center().toPoint())
        self.assertGreater(pixel.red(), 210)
        self.assertGreater(pixel.green(), 150)
        self.assertLess(pixel.blue(), 60)

    def test_alarm_editor_adjustment_records_spin_animation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency": "每周", "weekdays": "二、四"}

        with patch("local.island_stack_visual.time.monotonic", return_value=12.5):
            visual._adjust_alarm_editor_field("hour", 1)

        animation = visual.plugin_alarm_editor_spin_animations["hour"]
        self.assertEqual(animation["direction"], 1)
        self.assertEqual(animation["started_at"], 12.5)

    def test_alarm_picker_display_values_include_adjustment_offsets(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_editor = {"hour": 16, "minute": 22, "note": "", "frequency": "每周", "weekdays": "二、四"}
        visual.plugin_alarm_editor_spin_animations = {"hour": {"direction": 1, "started_at": 20.0}}

        with patch("local.island_stack_visual.time.monotonic", return_value=20.08):
            values = visual._alarm_picker_display_values("hour")

        self.assertTrue(any(abs(float(item.get("offset", 0.0))) > 0.01 for item in values))

    def test_alarm_picker_weekdays_uses_whole_week_label(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_editor = {"frequency_unit": "weekly", "weekdays": [1, 2, 3, 4, 5, 6, 7]}

        values = visual._alarm_picker_display_values("weekdays")

        center = len(values) // 2
        self.assertEqual(values[center]["text"], "整周")

    def test_alarm_picker_field_uses_larger_legible_type(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_editor = {"frequency_unit": "daily", "hour": 15, "minute": 22, "note": ""}
        visual.plugin_alarm_editor_rects = {}
        calls = []
        original_font = visual._font

        def record_font(size, weight=QFont.Weight.Normal, shrink=True):
            calls.append(float(size))
            return original_font(size, weight, shrink)

        visual._font = record_font
        image = QImage(260, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_alarm_picker_field(painter, QRectF(20.0, 20.0, 160.0, 74.0), "frequency_unit", "每天", "频次", visual._plugin_theme_colors(), QColor(255, 204, 0))
        painter.end()

        self.assertGreaterEqual(max(calls), 14.0)
        self.assertGreaterEqual(min(calls), 10.0)

    def test_alarm_editor_accepts_direct_numeric_input_for_time_fields(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {"hour": 0, "minute": 0, "note": "", "frequency": "每周", "weekdays": "二、四"}
        visual.update = lambda: None

        visual._activate_plugin_action("alarm-editor-focus-hour")
        visual._handle_alarm_editor_key("2")
        visual._handle_alarm_editor_key("3")
        visual._activate_plugin_action("alarm-editor-focus-minute")
        visual._handle_alarm_editor_key("4")
        visual._handle_alarm_editor_key("5")

        self.assertEqual(visual.plugin_alarm_editor["hour"], 23)
        self.assertEqual(visual.plugin_alarm_editor["minute"], 45)

    def test_alarm_editor_accepts_direct_numeric_input_for_recurrence_fields(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {
            "hour": 15,
            "minute": 22,
            "note": "",
            "frequency_unit": "monthly",
            "month_day": 1,
            "year_month": 1,
            "year_day": 1,
        }
        visual.plugin_alarm_editor_input_buffers = {}
        visual._alarm_year_boundary_year = lambda: 2027
        visual.update = lambda: None

        visual._activate_plugin_action("alarm-editor-focus-month_day")
        visual._handle_alarm_editor_key("3")
        visual._handle_alarm_editor_key("1")
        visual._activate_plugin_action("alarm-editor-focus-year_month")
        visual._handle_alarm_editor_key("0")
        visual._handle_alarm_editor_key("2")
        visual._activate_plugin_action("alarm-editor-focus-year_day")
        visual._handle_alarm_editor_key("3")
        visual._handle_alarm_editor_key("0")

        self.assertEqual(visual.plugin_alarm_editor["month_day"], 31)
        self.assertEqual(visual.plugin_alarm_editor["year_month"], 2)
        self.assertEqual(visual.plugin_alarm_editor["year_day"], 29)

    def test_weekly_detail_mode_toggles_selected_weekdays(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {"frequency_unit": "weekly", "weekdays": [2, 4], "hour": 15, "minute": 22, "note": ""}
        visual.update = lambda: None

        visual._activate_plugin_action("alarm-editor-open-weekdays")
        visual._activate_plugin_action("alarm-editor-toggle-weekday-6")

        self.assertEqual(visual.plugin_alarm_editor["detail_mode"], "weekly")
        self.assertEqual(visual.plugin_alarm_editor["weekdays"], [2, 4, 6])

    def test_monthly_and_yearly_day_adjustment_clamps_to_calendar_boundaries(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_editor = {"frequency_unit": "monthly", "month_day": 31}

        visual._adjust_alarm_editor_field("month_day", 1)
        self.assertEqual(visual.plugin_alarm_editor["month_day"], 1)

        visual.plugin_alarm_editor = {"frequency_unit": "yearly", "year_month": 2, "year_day": 30}
        visual._normalize_alarm_editor_recurrence(visual.plugin_alarm_editor)
        self.assertEqual(visual.plugin_alarm_editor["year_day"], 29)

    def test_yearly_feb_twenty_nine_is_preserved_as_leap_year_alarm(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._alarm_year_boundary_year = lambda: 2027
        editor = {"frequency_unit": "yearly", "year_month": 2, "year_day": 29}

        normalized = visual._normalize_alarm_editor_recurrence(editor)

        self.assertEqual(normalized["year_day"], 29)
        self.assertEqual(visual._alarm_recurrence_label(normalized), "每闰年")
        self.assertEqual(visual._alarm_recurrence_value_label(normalized), "2月29日")

    def test_yearly_month_adjustment_clamps_day_for_target_month(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_editor = {"frequency_unit": "yearly", "year_month": 3, "year_day": 31}

        visual._adjust_alarm_editor_field("year_month", -1)

        self.assertEqual(visual.plugin_alarm_editor["year_month"], 2)
        self.assertLessEqual(visual.plugin_alarm_editor["year_day"], 29)

    def test_alarm_editor_narrow_detail_paint_registers_month_and_year_fields(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        image = QImage(360, 190, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(12.0, 12.0, 320.0, 156.0)

        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "monthly", "month_day": 29, "detail_mode": "monthly"}
        visual._paint_time_alarm_editor(painter, rect, {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        self.assertIn("month_day", visual.plugin_alarm_editor_rects)

        visual.plugin_alarm_editor_rects = {}
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "yearly", "year_month": 2, "year_day": 28, "detail_mode": "yearly"}
        visual._paint_time_alarm_editor(painter, rect, {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertIn("year_month", visual.plugin_alarm_editor_rects)
        self.assertIn("year_day", visual.plugin_alarm_editor_rects)

    def test_alarm_editor_detail_mode_is_standalone_without_editor_actions(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "weekly", "weekdays": [2, 4, 6], "detail_mode": "weekly"}
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_editor(painter, QRectF(20.0, 14.0, 420.0, 190.0), {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertNotIn("alarm-editor-save", visual.plugin_action_rects)
        self.assertNotIn("alarm-editor-cancel", visual.plugin_action_rects)
        self.assertNotIn("alarm-editor-page-note", visual.plugin_action_rects)

    def test_alarm_editor_detail_page_has_top_right_confirm_to_return(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "yearly", "year_month": 2, "year_day": 29, "detail_mode": "yearly"}
        visual.update = lambda: None
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 14.0, 420.0, 190.0)

        visual._paint_time_alarm_editor(painter, rect, {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        confirm = visual.plugin_action_rects["alarm-editor-detail-confirm"]
        self.assertGreater(confirm.right(), rect.right() - visual._u(48.0))
        self.assertLess(confirm.top(), rect.top() + visual._u(28.0))

        visual._activate_plugin_action("alarm-editor-detail-confirm")

        self.assertEqual(visual.plugin_alarm_editor["detail_mode"], "")
        self.assertEqual(visual.plugin_alarm_editor["editor_page"], "main")

    def test_alarm_editor_recurrence_value_click_opens_dedicated_page(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.music_playing = False
        visual.persistent_plugin = True
        visual.enabled_plugin_count = 3
        visual.messages = []
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "weekly", "weekdays": [2, 4, 6], "editor_page": "main", "detail_mode": ""}
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_editor(painter, QRectF(20.0, 14.0, 420.0, 190.0), {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        open_rect = visual.plugin_action_rects["alarm-editor-open-weekdays"]
        self.assertEqual(visual._hit_test(open_rect.center()), "plugin-action:alarm-editor-open-weekdays")

    def test_alarm_editor_detail_input_fields_paint_caret_when_focused(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor_focus = "month_day"
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "monthly", "month_day": 28, "detail_mode": "monthly"}
        caret_fields = []
        visual._paint_alarm_editor_caret = lambda _painter, _rect, field, _ref: caret_fields.append(field)
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_editor(painter, QRectF(20.0, 14.0, 420.0, 190.0), {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertIn("month_day", caret_fields)

    def test_alarm_editor_weekly_detail_uses_left_aligned_weekday_picker(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "weekly", "weekdays": [2, 4, 6], "detail_mode": "weekly"}
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 14.0, 420.0, 190.0)

        visual._paint_time_alarm_editor(painter, rect, {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        chips = [visual.plugin_action_rects[f"alarm-editor-toggle-weekday-{day}"] for day in range(1, 8)]
        self.assertEqual(len(chips), 7)
        self.assertLess(chips[0].left(), rect.left() + visual._u(56.0))
        for previous, current in zip(chips, chips[1:]):
            self.assertGreater(current.left(), previous.right())
            self.assertAlmostEqual(current.top(), chips[0].top(), delta=1.0)

    def test_alarm_editor_month_and_year_detail_match_input_units(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        image = QImage(460, 230, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 14.0, 420.0, 198.0)

        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "monthly", "month_day": 31, "detail_mode": "monthly"}
        visual._paint_time_alarm_editor(painter, rect, {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        month_day = visual.plugin_alarm_editor_rects["month_day"]
        self.assertGreater(month_day.width(), visual._u(92.0))
        self.assertIn("alarm-editor-focus-month_day", visual.plugin_action_rects)

        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "yearly", "year_month": 2, "year_day": 28, "detail_mode": "yearly"}
        visual._paint_time_alarm_editor(painter, rect, {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        month = visual.plugin_alarm_editor_rects["year_month"]
        day = visual.plugin_alarm_editor_rects["year_day"]
        self.assertGreater(month.width(), visual._u(78.0))
        self.assertGreater(day.width(), visual._u(78.0))
        self.assertGreater(day.left(), month.right())
        self.assertIn("alarm-editor-focus-year_month", visual.plugin_action_rects)
        self.assertIn("alarm-editor-focus-year_day", visual.plugin_action_rects)

    def test_alarm_editor_narrow_page_wheel_switches_pages_with_animation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_panel_last_width = 420.0
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "weekly", "weekdays": [2, 4, 6], "editor_page": "main"}
        visual.plugin_alarm_editor_rects = {"hour": QRectF(20.0, 40.0, 80.0, 68.0)}
        visual.update = lambda: None

        class FakeDelta:
            def __init__(self, y):
                self._y = y

            def y(self):
                return self._y

        class FakeWheel:
            def __init__(self, y):
                self.accepted = False
                self._delta = FakeDelta(y)

            def position(self):
                return QPointF(400.0, 120.0)

            def angleDelta(self):
                return self._delta

            def accept(self):
                self.accepted = True

        with patch("local.island_stack_visual.time.monotonic", return_value=91.0):
            event = FakeWheel(-120)
            self.assertTrue(visual._handle_time_alarm_editor_wheel(event))

        self.assertEqual(visual.plugin_alarm_editor["editor_page"], "note")
        self.assertTrue(event.accepted)
        self.assertEqual(visual.plugin_alarm_editor_page_animation["from"], "main")
        self.assertEqual(visual.plugin_alarm_editor_page_animation["to"], "note")
        self.assertEqual(visual.plugin_alarm_editor_page_animation["started_at"], 91.0)

    def test_alarm_edit_opens_editor_prefilled_and_save_updates_same_row(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_rows = [
            {"time": "06:45", "label": "Gym", "enabled": False, "frequency": "workdays", "weekdays": "Mon-Fri"},
            {"time": "09:30", "label": "Standup", "enabled": True, "frequency": "daily", "weekdays": "Every day"},
        ]
        visual.update = lambda: None

        visual._activate_plugin_action("alarm-edit-0")

        editor = visual._time_alarm_editor_state()
        self.assertEqual(visual.plugin_time_mode, "alarm_editor")
        self.assertEqual(editor["hour"], 6)
        self.assertEqual(editor["minute"], 45)
        self.assertEqual(editor["note"], "Gym")
        self.assertEqual(editor["frequency"], "workdays")
        self.assertEqual(editor["weekdays"], "Mon-Fri")
        self.assertEqual(editor["edit_index"], 0)

        editor["hour"] = 7
        editor["minute"] = 5
        editor["note"] = "Stretch"
        visual.plugin_alarm_editor = editor
        visual._activate_plugin_action("alarm-editor-save")

        self.assertEqual(len(visual.plugin_alarm_rows), 2)
        self.assertEqual(visual.plugin_alarm_rows[0]["time"], "07:05")
        self.assertEqual(visual.plugin_alarm_rows[0]["label"], "Stretch")
        self.assertFalse(visual.plugin_alarm_rows[0]["enabled"])
        self.assertEqual(visual.plugin_alarm_rows[1]["label"], "Standup")

    def test_alarm_rows_load_from_persisted_settings_without_fake_defaults(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        rows = [
            {
                "time": "06:45",
                "label": "Gym",
                "enabled": True,
                "frequency_unit": "weekly",
                "weekdays": [1, 3, 5],
            }
        ]
        settings = {"v2_alarm_rows": json.dumps(rows, ensure_ascii=False)}
        visual.settings_getter = lambda key, default=None: settings.get(key, default)

        loaded = visual._time_alarm_rows()

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["time"], "06:45")
        self.assertEqual(loaded[0]["label"], "Gym")
        self.assertEqual(loaded[0]["weekdays"], [1, 3, 5])

        settings["v2_alarm_rows"] = ""
        visual.plugin_alarm_rows = [{"time": "15:22", "label": "示例", "enabled": True}]
        self.assertEqual(visual._time_alarm_rows(), [])

    def test_alarm_editor_save_persists_rows_for_provider(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        settings = {"v2_alarm_rows": ""}
        visual.settings_getter = lambda key, default=None: settings.get(key, default)
        visual.settings_setter = lambda key, value: settings.__setitem__(key, value)
        visual.plugin_alarm_rows = []
        visual.plugin_alarm_editor = {
            "hour": 7,
            "minute": 30,
            "note": "早课",
            "frequency_unit": "daily",
            "weekdays": [],
            "month_day": 28,
            "year_month": 4,
            "year_day": 28,
            "edit_index": None,
        }
        visual.update = lambda: None

        visual._activate_plugin_action("alarm-editor-save")

        payload = json.loads(settings["v2_alarm_rows"])
        self.assertEqual(payload[0]["time"], "07:30")
        self.assertEqual(payload[0]["label"], "早课")
        self.assertEqual(payload[0]["frequency_unit"], "daily")

    def test_alarm_toggle_and_delete_persist_rows_for_provider(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        rows = [{"time": "06:45", "label": "Gym", "enabled": True, "frequency_unit": "daily"}]
        settings = {"v2_alarm_rows": json.dumps(rows, ensure_ascii=False)}
        visual.settings_getter = lambda key, default=None: settings.get(key, default)
        visual.settings_setter = lambda key, value: settings.__setitem__(key, value)
        visual.update = lambda: None

        visual._activate_plugin_action("alarm-toggle-0")

        payload = json.loads(settings["v2_alarm_rows"])
        self.assertFalse(payload[0]["enabled"])

        visual._activate_plugin_action("alarm-delete-0")

        self.assertEqual(json.loads(settings["v2_alarm_rows"]), [])

    def test_alarm_reminder_due_respects_yearly_next_leap_day(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        now = datetime(2027, 3, 1, 8, 0, tzinfo=timezone.utc)

        due = visual._time_reminder_due_from_alarm(
            {
                "time": "09:00",
                "enabled": True,
                "frequency_unit": "yearly",
                "year_month": 2,
                "year_day": 29,
            },
            now,
        )

        self.assertEqual(due, datetime(2028, 2, 29, 9, 0, tzinfo=timezone.utc))

    def test_alarm_module_registers_row_edit_and_separate_toggle_targets(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        image = QImage(260, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(
            painter,
            QRectF(20.0, 20.0, 210.0, 100.0),
            {
                "alarms": [
                    {"time": "06:45", "label": "Gym", "enabled": False, "frequency": "workdays", "weekdays": "Mon-Fri"},
                ],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        edit_rect = visual.plugin_action_rects["alarm-edit-0"]
        toggle_rect = visual.plugin_action_rects["alarm-toggle-0"]
        self.assertLess(edit_rect.right(), toggle_rect.left())
        self.assertNotIn("alarm-edit-button-0", visual.plugin_action_rects)

    def test_alarm_module_hit_test_prioritizes_row_actions_over_list_backdrop(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.plugin_time_mode = "dashboard"
        visual.plugin_action_rects = {}
        visual._main_kind = lambda: IslandKind.PLUGIN
        image = QImage(260, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(
            painter,
            QRectF(20.0, 20.0, 210.0, 100.0),
            {
                "alarms": [
                    {"time": "06:45", "label": "Gym", "enabled": False, "frequency": "workdays", "weekdays": "Mon-Fri"},
                ],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        edit_rect = visual.plugin_action_rects["alarm-edit-0"]
        toggle_rect = visual.plugin_action_rects["alarm-toggle-0"]
        self.assertEqual(visual._hit_test(edit_rect.center()), "plugin-action:alarm-edit-0")
        self.assertEqual(visual._hit_test(toggle_rect.center()), "plugin-action:alarm-toggle-0")

    def test_alarm_row_long_press_replaces_switch_with_delete_button(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_rows = [
            {"time": "06:45", "label": "Gym", "enabled": False, "frequency": "daily"},
            {"time": "07:30", "label": "Tea", "enabled": True, "frequency": "daily"},
        ]
        visual.update = lambda: None

        self.assertTrue(visual._handle_alarm_row_long_press("alarm-edit-0"))

        image = QImage(260, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        visual._paint_time_alarm_module(painter, QRectF(20.0, 20.0, 210.0, 100.0), {"alarms": visual.plugin_alarm_rows}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertEqual(visual.plugin_alarm_delete_index, 0)
        self.assertIn("alarm-delete-0", visual.plugin_action_rects)
        self.assertNotIn("alarm-toggle-0", visual.plugin_action_rects)
        delete = visual.plugin_action_rects["alarm-delete-0"]
        pixel = image.pixelColor(QPoint(int(delete.left() + visual._u(5.0)), int(delete.center().y())))
        self.assertGreater(pixel.red(), 160)
        self.assertLess(pixel.green(), 90)
        self.assertLess(pixel.blue(), 90)

        visual._activate_plugin_action("alarm-delete-0")

        self.assertEqual(len(visual.plugin_alarm_rows), 1)
        self.assertEqual(visual.plugin_alarm_rows[0]["time"], "07:30")
        self.assertIsNone(visual.plugin_alarm_delete_index)

    def test_alarm_list_scroll_clears_delete_mode(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"
        visual.plugin_panel_last_width = 380.0
        visual.plugin_time_visible_module = "alarm"
        visual.plugin_alarm_delete_index = 0
        visual.plugin_alarm_list_scroll_index = 0.0
        visual.plugin_alarm_list_rect = QRectF(100.0, 50.0, 180.0, 70.0)
        visual.plugin_alarm_visible_range = (0, 4)
        visual.plugin_alarm_rows = [
            {"time": f"06:{index:02d}", "label": f"Alarm {index}", "enabled": True, "frequency": "daily"}
            for index in range(8)
        ]
        visual.update = lambda: None

        class FakeDelta:
            def y(self):
                return -120

        class FakeWheel:
            def angleDelta(self):
                return FakeDelta()

            def position(self):
                return QPointF(130.0, 70.0)

            def accept(self):
                pass

        self.assertTrue(visual._handle_time_panel_module_wheel(FakeWheel()))

        self.assertIsNone(visual.plugin_alarm_delete_index)

    def test_alarm_due_pending_is_captured_until_confirmed(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_due_pending = {}

        alarm_model = visual._time_plugin_panel_model(
            {
                "label": "TIME",
                "detail": "07:30",
                "summary": "07:30",
                "status": "alarm",
                "alert_title": "闹钟提醒",
                "alert_body": "晨会",
            }
        )
        idle_model = visual._time_plugin_panel_model(
            {"label": "TIME", "detail": "07:31", "summary": "05/29 Fri", "status": "idle"}
        )

        self.assertEqual(alarm_model["alarm_due_pending"]["label"], "晨会")
        self.assertEqual(idle_model["alarm_due_pending"]["label"], "晨会")

        visual.update = lambda: None
        visual._activate_plugin_action("alarm-due-confirm")

        self.assertEqual(getattr(visual, "plugin_alarm_due_pending", {}), {})

    def test_confirmed_alarm_due_is_not_recaptured_from_same_provider_alarm(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_due_pending = {}
        visual.update = lambda: None
        alarm_payload = {
            "label": "TIME",
            "detail": "07:31",
            "summary": "07:30",
            "status": "alarm",
            "alert_title": "闹钟提醒",
            "alert_body": "晨会",
        }

        first = visual._time_plugin_panel_model(alarm_payload)
        visual._activate_plugin_action("alarm-due-confirm")
        second = visual._time_plugin_panel_model(alarm_payload)
        new_alarm = visual._time_plugin_panel_model({**alarm_payload, "summary": "07:45", "alert_body": "站会"})

        self.assertEqual(first["alarm_due_pending"]["label"], "晨会")
        self.assertNotIn("alarm_due_pending", second)
        self.assertEqual(new_alarm["alarm_due_pending"]["label"], "站会")

    def test_alarm_due_pending_keeps_alarm_note_when_countdown_starts(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_due_pending = {"time": "07:30", "label": "晨会", "key": "07:30|晨会"}
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []

        model = visual._time_plugin_panel_model(
            {
                "label": "TIME",
                "detail": "00:52",
                "summary": "倒计时 4:58",
                "status": "alarm",
                "alert_title": "闹钟提醒",
                "alert_body": "倒计时 4:58",
                "progress": 0.16,
            }
        )

        self.assertEqual(model["alarm_due_pending"]["label"], "晨会")
        self.assertEqual(model["alarm_due_pending"]["time"], "07:30")
        self.assertEqual(getattr(visual, "plugin_alarm_due_pending", {})["label"], "晨会")

    def test_alarm_status_with_countdown_text_does_not_create_generic_alarm_side_alert(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_due_pending = {}
        visual.plugin_alarm_due_acknowledged_key = "07:30|晨会"
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []

        candidates = visual._time_side_alert_candidates(
            0,
            "TIME",
            "00:52",
            "倒计时 4:58",
            QColor(255, 184, 0),
            "T",
            "time",
            "alarm",
            "闹钟提醒",
            "倒计时 4:58",
            3,
        )

        self.assertNotIn("time:alarm", [candidate[8] for candidate in candidates])
        self.assertEqual([candidate[8] for candidate in candidates], ["time"])
        self.assertEqual(candidates[0][11], "4:58")

    def test_alarm_module_paints_due_confirmation_until_user_confirms(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        module_rect = QRectF(20.0, 20.0, 260.0, 132.0)
        image = QImage(340, 190, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(
            painter,
            module_rect,
            {
                "alarms": [{"time": "07:30", "label": "晨会", "enabled": True, "frequency": "daily"}],
                "alarm_due_pending": {"time": "07:30", "label": "晨会"},
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        self.assertIn("alarm-due-confirm", visual.plugin_action_rects)
        self.assertNotIn("alarm-add-open", visual.plugin_action_rects)
        self.assertNotIn("alarm-list", visual.plugin_action_rects)
        self.assertFalse(any(key.startswith(("alarm-toggle-", "alarm-edit-", "alarm-delete-")) for key in visual.plugin_action_rects))
        due = visual.plugin_action_rects["alarm-due-confirm"]
        self.assertGreaterEqual(due.width(), module_rect.width() - visual._u(24.0))
        self.assertGreaterEqual(due.height(), module_rect.height() - visual._u(48.0))
        self.assertTrue(due.contains(QPointF(module_rect.left() + visual._u(4.0), module_rect.top() + visual._u(4.0))))
        self.assertTrue(due.contains(QPointF(module_rect.right() - visual._u(4.0), module_rect.bottom() - visual._u(4.0))))

    def test_alarm_due_confirmation_hit_test_covers_whole_module(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.plugin_time_mode = "dashboard"
        visual._main_kind = lambda: IslandKind.PLUGIN
        visual.plugin_action_rects = {}
        module_rect = QRectF(20.0, 20.0, 260.0, 132.0)
        image = QImage(340, 190, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(
            painter,
            module_rect,
            {
                "alarms": [{"time": "07:30", "label": "晨会", "enabled": True, "frequency": "daily"}],
                "alarm_due_pending": {"time": "07:30", "label": "晨会"},
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        for point in (
            QPointF(module_rect.left() + visual._u(5.0), module_rect.top() + visual._u(5.0)),
            QPointF(module_rect.center()),
            QPointF(module_rect.right() - visual._u(5.0), module_rect.bottom() - visual._u(5.0)),
        ):
            self.assertEqual(visual._hit_test(point), "plugin-action:alarm-due-confirm")

    def test_pending_alarm_due_keeps_side_alert_until_confirmed(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_due_pending = {"time": "07:30", "label": "晨会", "key": "07:30|晨会"}
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []

        candidates = visual._time_side_alert_candidates(
            0,
            "TIME",
            "07:31",
            "05/29 Fri",
            QColor(255, 184, 0),
            "T",
            "time",
            "idle",
            "",
            "",
            50,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][8], "time:alarm")
        self.assertEqual(candidates[0][9], "alarm")
        self.assertEqual(candidates[0][11], "晨会")

    def test_done_alarm_note_candidate_uses_due_alarm_style(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []

        candidates = visual._time_side_alert_candidates(
            0,
            "TIME",
            "07:30",
            "05/29 Fri",
            QColor(255, 184, 0),
            "T",
            "time",
            "done",
            "闹钟提醒",
            "晨会",
            3,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][8], "time:alarm")
        self.assertEqual(candidates[0][9], "alarm")
        self.assertEqual(candidates[0][11], "晨会")

    def test_done_countdown_and_due_alarm_can_show_as_separate_time_side_alerts(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.plugin_samples = [
            ("TIME", "07:30", "计时结束", QColor(255, 184, 0), "T", "time", "alarm", "闹钟提醒", "晨会", 3, 1.0),
        ]

        alerts = visual._active_plugin_alerts(limit=2)

        self.assertEqual([alert[6] for alert in alerts], ["time:alarm", "time"])
        self.assertEqual([alert[7] for alert in alerts], ["alarm", "done"])
        self.assertEqual(alerts[0][9], "晨会")
        self.assertEqual(alerts[1][9], "时间到")

    def test_alarm_side_alert_stays_after_countdown_replaces_alarm_payload_summary(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.plugin_alarm_due_pending = {}
        visual.plugin_alarm_due_acknowledged_key = ""
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []
        visual.plugin_samples = [
            ("TIME", "07:31", "倒计时 4:58", QColor(255, 184, 0), "T", "time", "alarm", "闹钟提醒", "晨会", 3, 1.0),
        ]

        initial_alerts = visual._active_plugin_alerts(limit=2)
        visual.plugin_samples = [
            ("TIME", "07:32", "倒计时 4:12", QColor(255, 184, 0), "T", "time", "running", "计时进行中", "倒计时 4:12", 30, 0.2),
        ]
        next_alerts = visual._active_plugin_alerts(limit=2)

        self.assertEqual([alert[6] for alert in initial_alerts], ["time:alarm", "time"])
        self.assertEqual([alert[6] for alert in next_alerts], ["time:alarm", "time"])
        self.assertEqual(next_alerts[0][9], "晨会")
        self.assertEqual(next_alerts[1][9], "4:12")

    def test_default_alarm_side_alert_is_not_swallowed_by_running_countdown(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.plugin_alarm_due_pending = {}
        visual.plugin_alarm_due_acknowledged_key = ""
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []
        visual.plugin_samples = [
            ("TIME", "07:31", "倒计时 4:58", QColor(255, 184, 0), "T", "time", "alarm", "闹钟提醒", "闹钟", 3, 1.0),
        ]

        alerts = visual._active_plugin_alerts(limit=2)

        self.assertEqual([alert[6] for alert in alerts], ["time:alarm", "time"])
        self.assertEqual(alerts[0][9], "闹钟")
        self.assertEqual(alerts[1][9], "4:58")

    def test_due_alarm_bare_time_summary_does_not_create_countdown_side_alert(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.plugin_samples = [
            ("TIME", "00:21", "00:21", QColor(255, 184, 0), "T", "time", "alarm", "闹钟提醒", "看大夫", 3, 1.0),
        ]

        alerts = visual._active_plugin_alerts(limit=2)

        self.assertEqual([alert[6] for alert in alerts], ["time:alarm"])
        self.assertEqual(alerts[0][9], "看大夫")

    def test_new_done_alarm_note_replaces_pending_alarm_side_alert(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_due_pending = {"time": "07:30", "label": "旧提醒", "key": "07:30|旧提醒"}
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []

        candidates = visual._time_side_alert_candidates(
            0,
            "TIME",
            "07:31",
            "05/29 Fri",
            QColor(255, 184, 0),
            "T",
            "time",
            "done",
            "闹钟提醒",
            "新提醒",
            3,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][8], "time:alarm")
        self.assertEqual(candidates[0][11], "新提醒")

    def test_time_panel_paint_registers_reference_actions_without_reminder_rail(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_time_mode = "dashboard"
        visual.plugin_alarm_rows = [{"time": "06:45", "label": "Gym", "enabled": True, "frequency_unit": "daily"}]
        visual._paint_plugin_panel_reminders = lambda _painter, _rect: (_ for _ in ()).throw(AssertionError("old reminder rail should not paint TIME"))
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_structured_plugin_panel(
            painter,
            QRectF(20.0, 20.0, 420.0, 188.0),
            {
                "label": "TIME",
                "detail": "22:52",
                "summary": "05/27 Wed",
                "status": "idle",
                "color": QColor(255, 204, 0),
                "progress": 0.0,
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        for command in ("timer-start-5", "timer-start-15", "timer-start-10", "timer-start-20", "alarm-toggle-0", "alarm-add-open"):
            self.assertIn(command, visual.plugin_action_rects)

    def test_weather_and_agenda_panel_sections_are_functional(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        weather = visual._plugin_panel_sections(
            {
                "label": "WEATHER",
                "detail": "26°C 晴",
                "summary": "北京 · 东南风12km/h · 湿度 40%",
                "status": "normal",
                "alert_title": "",
                "alert_body": "",
            }
        )
        agenda = visual._plugin_panel_sections(
            {
                "label": "AGENDA",
                "detail": "28 分钟后",
                "summary": "准备上课",
                "status": "soon",
                "alert_title": "日程将近",
                "alert_body": "28 分钟后 · 准备上课",
            }
        )

        self.assertEqual([section["id"] for section in weather], ["current", "metrics", "source"])
        self.assertIn("风", weather[1]["body"])
        self.assertEqual([section["id"] for section in agenda], ["next", "todo", "reminder"])
        self.assertIn("课表", agenda[1]["body"])

    def test_plugin_panel_reminder_models_include_active_alerts(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = True
        visual.enabled_plugin_count = 3
        visual.plugin_panel_open = True
        visual.plugin_samples = [
            ("TIME", "00:24", "倒计时 0:24", QColor(67, 162, 255), "●", "time", "running", "计时进行中", "倒计时 0:24", 30, 0.4),
            ("WEATHER", "雷雨", "北京", QColor(80, 210, 255), "☁", "weather", "severe", "严重天气提醒", "北京 · 雷雨", 2, 1.0),
            ("AGENDA", "4 分钟后", "准备上课", QColor(118, 236, 160), "■", "agenda", "soon", "日程将近", "4 分钟后 · 准备上课", 10, 0.6),
        ]

        reminders = visual._plugin_panel_reminder_models(limit=3)

        self.assertEqual([item["plugin_id"] for item in reminders], ["weather", "agenda", "time"])
        self.assertIn("雷雨", reminders[0]["body"])

    def test_time_statuses_show_side_alerts_until_plugin_panel_expands(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 2
        visual.plugin_samples = [
            ("TIME", "00:24", "倒计时 0:24", QColor(67, 162, 255), "●", "time", "running", "计时进行中", "倒计时 0:24", 30, 0.42),
            ("WEATHER", "北京", "天气刷新中", QColor(80, 210, 255), "☁", "weather", "loading", "天气刷新中", "北京", 40, 0.0),
        ]

        alerts = visual._active_plugin_alerts(limit=3, include_expanded=True)

        self.assertEqual([alert[6] for alert in alerts], ["time", "weather"])

        visual.expanded = True
        visual.plugin_panel_open = True
        visual._main_kind = lambda: IslandKind.PLUGIN
        side_alerts = visual._active_plugin_alerts(limit=3)
        reminders = visual._plugin_panel_reminder_models(limit=3)

        self.assertEqual([alert[6] for alert in side_alerts], [])
        self.assertEqual([item["plugin_id"] for item in reminders], ["time", "weather"])

    def test_time_reminder_icon_paths_use_local_svg_assets(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        running = visual._time_reminder_icon_path("running", "countdown")
        done = visual._time_reminder_icon_path("done", "alarm")

        self.assertTrue(running.endswith("倒计时黄.svg"))
        self.assertTrue(done.endswith("闹钟黑.svg"))

    def test_time_reminder_display_text_caps_long_notes(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        text = visual._time_reminder_display_text("prepare materials and stretch the note across the island")

        self.assertLessEqual(len(text), 18)
        self.assertTrue(text.endswith("..."))

    def test_time_alert_painter_does_not_use_generic_status_dot(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enabled_plugin_count = 1
        visual.scroll_phase = 0.0
        visual.plugin_samples = [
            ("TIME", "04:20", "Countdown 4:20", QColor(255, 184, 0), "", "time", "running", "Timer running", "Countdown 4:20", 10, 0.4),
        ]
        visual._paint_plugin_status_dot = lambda *_args: (_ for _ in ()).throw(AssertionError("TIME alert must not paint generic status dot"))
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin_alert(painter, QRectF(12.0, 12.0, 220.0, 42.0), "plugin-alert:time")
        painter.end()

    def test_time_alert_width_is_capped_for_long_side_content(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        width = visual._plugin_alert_width_for("TIME", "07:30", "alarm note that is far too long for the island")
        agenda_width = visual._plugin_alert_width_for("AGENDA", "07:30", "alarm note that is far too long for the island")

        self.assertLessEqual(width, 168.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertGreater(agenda_width, width)

    def test_time_alert_rect_ignores_generic_width_growth_for_time(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 240.0
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.plugin_samples = [
            ("TIME", "07:30", "alarm note that is far too long for the island", QColor(255, 184, 0), "", "time", "running", "alarm note that is far too long for the island", "alarm 07:30", 10, 0.4),
        ]
        visual._screen_bounds_in_local = lambda: (0.0, 620.0)
        visual._main_rect_screen_pressure = lambda *_args: ""
        visual.height = lambda: 260

        rect = visual._plugin_alert_rect(QRectF(160.0, 82.0, 180.0, 48.0), 0, 1)

        self.assertLessEqual(rect.width(), 168.0 * IslandStackVisual.VISUAL_SCALE)

    def test_time_side_countdown_alert_uses_static_text_not_scrolling(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        calls = []
        visual._paint_scrolling_text = lambda _painter, _rect, text, *_args, **_kwargs: calls.append(str(text))
        image = QImage(240, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 156.0, 42.0),
            "TIME",
            "10:11:08",
            "Countdown 4:47",
            QColor(255, 184, 0),
            "running",
            "Timer running",
            "Countdown 4:47",
            0.4,
        )
        painter.end()

        self.assertEqual(calls, [])

    def test_time_side_countdown_alert_only_paints_icon_and_value_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        static_texts = []
        visual._paint_static_elided_text = lambda _painter, _rect, text, *_args, **_kwargs: static_texts.append(str(text))
        image = QImage(240, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 118.0, 42.0),
            "TIME",
            "10:11:08",
            "Countdown 4:59",
            QColor(255, 184, 0),
            "running",
            "Timer running",
            "Countdown 4:59",
            0.4,
        )
        painter.end()

        self.assertEqual(static_texts, ["4:59"])

    def test_time_side_alert_text_is_two_sizes_smaller_but_bold(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        static_calls = []
        done_calls = []
        scroll_calls = []
        visual._paint_static_elided_text = lambda _painter, _rect, text, size, _color, bold, *_args, **_kwargs: static_calls.append((str(text), int(size), bool(bold)))
        visual._paint_time_done_alert_text = lambda _painter, _rect, text, _color, base_size=23, boost=2.0: done_calls.append((str(text), int(base_size), float(boost)))
        visual._paint_scrolling_text = lambda _painter, _rect, text, size, _color, bold, *_args, **_kwargs: scroll_calls.append((str(text), int(size), bool(bold)))
        image = QImage(260, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 156.0, 42.0),
            "TIME",
            "10:11:08",
            "Countdown 4:59",
            QColor(255, 184, 0),
            "running",
            "Timer running",
            "Countdown 4:59",
            0.4,
        )
        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 50.0, 118.0, 42.0),
            "TIME",
            "0:00",
            "Countdown 0:00",
            QColor(255, 184, 0),
            "done",
            "Timer done",
            "Countdown 0:00",
            1.0,
        )
        painter.end()

        self.assertIn(("4:59", 20, True), static_calls)
        self.assertIn(("时间到", 20, True), static_calls)
        self.assertEqual(done_calls, [])
        self.assertEqual(scroll_calls, [])

    def test_alarm_note_with_done_status_still_uses_alarm_side_alert_style(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        done_texts = []
        scroll_texts = []
        visual._paint_time_done_alert_text = lambda _painter, _rect, text, *_args, **_kwargs: done_texts.append(str(text))
        static_texts = []
        visual._paint_static_elided_text = lambda _painter, _rect, text, *_args, **_kwargs: static_texts.append(str(text))
        visual._paint_alarm_scrolling_text = lambda _painter, _rect, text, *_args, **_kwargs: scroll_texts.append(str(text))
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 168.0, 42.0),
            "TIME",
            "07:30",
            "计时结束",
            QColor(255, 184, 0),
            "done",
            "计时结束",
            "所爱衬所爱",
            1.0,
        )
        painter.end()

        self.assertEqual(static_texts, ["所爱衬所爱"])
        self.assertEqual(scroll_texts, [])
        self.assertEqual(done_texts, [])

    def test_alarm_side_alert_long_note_uses_unified_static_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        calls = []
        alarm_scrolls = []
        visual._paint_scrolling_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("alarm side alert should not use generic scrolling text"))
        visual._paint_alarm_scrolling_text = lambda _painter, _rect, text, *_args, **_kwargs: alarm_scrolls.append(str(text))
        visual._paint_static_elided_text = lambda _painter, _rect, text, size, color, bold, *_args, **_kwargs: calls.append((str(text), int(size), QColor(color), bool(bold)))
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 168.0, 42.0),
            "TIME",
            "07:30",
            "alarm 07:30",
            QColor(255, 184, 0),
            "running",
            "alarm note that is far too long",
            "alarm 07:30",
            0.4,
        )
        painter.end()

        self.assertEqual(len(calls), 1)
        self.assertIn("alarm", calls[0][0].lower())
        self.assertEqual(calls[0][1], 20)
        self.assertTrue(calls[0][3])
        self.assertEqual(alarm_scrolls, [])

    def test_plugin_alert_exit_rect_shrinks_towards_main_island(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        main = StackVisual("music", IslandKind.MUSIC, QRectF(160.0, 82.0, 420.0, 48.0), False, None, 10)
        right_alert = QRectF(600.0, 85.0, 156.0, 42.0)
        left_alert = QRectF(0.0, 85.0, 126.0, 42.0)

        right_target = visual._plugin_alert_exit_rect_towards_main(right_alert, [main])
        left_target = visual._plugin_alert_exit_rect_towards_main(left_alert, [main])

        self.assertLessEqual(right_target.width(), visual._u(12.0))
        self.assertLess(right_target.center().x(), right_alert.center().x())
        self.assertAlmostEqual(right_target.left(), main.rect.right() + visual._u(4.0), delta=1.0)
        self.assertLessEqual(left_target.width(), visual._u(12.0))
        self.assertGreater(left_target.center().x(), left_alert.center().x())
        self.assertAlmostEqual(left_target.right(), main.rect.left() - visual._u(4.0), delta=1.0)

    def test_time_side_alert_renderer_has_no_nested_value_chip(self):
        source = inspect.getsource(IslandStackVisual._paint_time_plugin_alert)

        self.assertNotIn("drawRoundedRect(value_rect", source)
        self.assertNotIn("drawRoundedRect(due_rect", source)
        self.assertNotIn("mini_icon", source)

    def test_time_done_side_alert_uses_same_icon_and_text_pill_as_waiting_state(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        symbols = []
        visual._paint_time_symbol = lambda _painter, _rect, kind, *_args, **_kwargs: symbols.append(str(kind))
        static_texts = []
        done_texts = []
        visual._paint_static_elided_text = lambda _painter, _rect, text, *_args, **_kwargs: static_texts.append(str(text))
        visual._paint_time_done_alert_text = lambda _painter, _rect, text, *_args, **_kwargs: done_texts.append(str(text))
        image = QImage(240, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 118.0, 42.0),
            "TIME",
            "0:00",
            "Countdown 0:00",
            QColor(255, 184, 0),
            "done",
            "Timer done",
            "Countdown 0:00",
            1.0,
        )
        painter.end()

        self.assertEqual(symbols, ["countdown"])
        self.assertEqual(static_texts, ["\u65f6\u95f4\u5230"])
        self.assertEqual(done_texts, [])

    def test_time_side_alarm_note_uses_same_static_text_channel_as_countdown(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        calls = []
        visual._paint_scrolling_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("alarm side alert should not use generic scrolling text"))
        visual._paint_alarm_scrolling_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("alarm side alert should not use focus-character scrolling"))
        visual._paint_static_elided_text = lambda _painter, _rect, text, *_args, **_kwargs: calls.append(str(text))
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 168.0, 42.0),
            "TIME",
            "07:30",
            "alarm 07:30",
            QColor(255, 184, 0),
            "running",
            "alarm note that is far too long",
            "alarm 07:30",
            0.4,
        )
        painter.end()

        self.assertEqual(len(calls), 1)
        self.assertIn("alarm", calls[0].lower())
        self.assertNotIn("07:30", calls)

    def test_alarm_side_alert_uses_note_or_default_alarm_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertEqual(visual._time_alarm_alert_text("下午茶时间很长"), "下午茶时间很长")
        self.assertEqual(visual._time_alarm_alert_text(""), "闹钟")

    def test_alarm_side_display_setting_can_show_time_instead_of_note(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = [
            {"time": "23:59", "label": "Yoga", "enabled": True, "frequency_unit": "daily"},
        ]
        visual.settings_getter = lambda key, default=None: "time" if key == "v2_time_alarm_side_display" else default

        candidates = visual._time_side_alert_candidates(
            0,
            "TIME",
            "10:11",
            "05/29 Fri",
            QColor(255, 184, 0),
            "T",
            "time",
            "idle",
            "",
            "",
            50,
        )

        alarm_candidates = [candidate for candidate in candidates if candidate[8] == "time:alarm"]
        self.assertEqual(len(alarm_candidates), 1)
        self.assertEqual(alarm_candidates[0][4], "23:59")
        self.assertEqual(alarm_candidates[0][11], "23:59")

    def test_alarm_side_alert_width_fits_at_least_five_chars(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        width = visual._time_plugin_alert_width_for("07:30", "闹钟")

        self.assertGreaterEqual(width, visual._u(150.0))

    def test_alarm_done_side_alert_uses_static_note_not_focus_character_scroll(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        static_texts = []
        visual._paint_static_elided_text = lambda _painter, _rect, text, *_args, **_kwargs: static_texts.append(str(text))
        visual._paint_time_done_alert_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("due alarm should not use countdown done text"))
        visual._paint_alarm_scrolling_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("due alarm should not use focus-character scrolling"))
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 168.0, 42.0),
            "TIME",
            "07:30",
            "alarm 07:30",
            QColor(255, 184, 0),
            "alarm",
            "闹钟提醒",
            "下午茶",
            1.0,
        )
        painter.end()

        self.assertEqual(static_texts, ["下午茶"])

    def test_time_compact_countdown_is_static_but_long_alarm_note_scrolls(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        visual._time_compact_segments = lambda *_args, **_kwargs: [
            {"kind": "time", "text": "10:11", "status": "normal"},
            {"kind": "countdown", "text": "4:47", "status": "running"},
            {"kind": "alarm", "text": "alarm note that is far too long", "status": "alarm"},
        ]
        calls = []
        visual._paint_scrolling_text = lambda _painter, _rect, text, *_args, **_kwargs: calls.append(str(text))
        image = QImage(320, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_compact_cell(painter, QRectF(8.0, 8.0, 286.0, 36.0), "10:11", "", QColor(255, 184, 0), True, "running", 0.4)
        painter.end()

        self.assertEqual(len(calls), 1)
        self.assertIn("alarm", calls[0].lower())
        self.assertNotIn("4:47", calls)

    def test_time_compact_layout_reserves_seconds_width_and_fixed_reminder_chips(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.show_seconds = True
        segments = [
            {"kind": "time", "text": "10:11:08", "status": "normal"},
            {"kind": "countdown", "text": "4:59", "status": "running"},
            {"kind": "alarm", "text": "Gym", "status": "alarm"},
        ]

        layout = visual._time_compact_cell_layout(QRectF(8.0, 8.0, 250.0, 36.0), segments)

        self.assertGreaterEqual(layout[0]["rect"].width(), visual._u(82.0))
        self.assertAlmostEqual(layout[1]["rect"].width(), visual._u(66.0), delta=2.0)
        self.assertAlmostEqual(layout[2]["rect"].width(), visual._u(74.0), delta=2.0)
        self.assertFalse(layout[1]["icon_only"])
        self.assertFalse(layout[2]["icon_only"])

    def test_time_compact_layout_expands_long_countdown_value(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.font_shrink_scale = 1.0
        visual.ui_font_family = IslandStackVisual.best_available_font_family()
        segments = [
            {"kind": "time", "text": "10:11", "status": "normal"},
            {"kind": "countdown", "text": "1:02:03", "status": "running"},
        ]

        layout = visual._time_compact_cell_layout(QRectF(8.0, 8.0, 360.0, 36.0), segments)

        countdown_rect = next(item["rect"] for item in layout if item["kind"] == "countdown")
        font_metrics = QFontMetrics(visual._font(10, QFont.Weight.Bold))
        text_rect_width = countdown_rect.width() - visual._u(6.0 + 20.0 + 4.0 + 7.0)
        available_width = max(1, int(text_rect_width - visual._u(4.0)))
        self.assertEqual(
            font_metrics.elidedText("1:02:03", Qt.TextElideMode.ElideRight, available_width),
            "1:02:03",
        )

    def test_time_compact_layout_collapses_reminders_to_circles_when_space_is_tight(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.show_seconds = True
        segments = [
            {"kind": "time", "text": "10:11:08", "status": "normal"},
            {"kind": "countdown", "text": "4:59", "status": "running"},
            {"kind": "alarm", "text": "Gym", "status": "alarm"},
        ]

        layout = visual._time_compact_cell_layout(QRectF(8.0, 8.0, 174.0, 36.0), segments)

        self.assertEqual([item["kind"] for item in layout], ["time", "countdown", "alarm"])
        self.assertTrue(layout[1]["icon_only"])
        self.assertTrue(layout[2]["icon_only"])
        self.assertAlmostEqual(layout[1]["rect"].width(), layout[1]["rect"].height(), delta=1.0)
        self.assertAlmostEqual(layout[2]["rect"].width(), layout[2]["rect"].height(), delta=1.0)

    def test_time_compact_future_alarm_segment_uses_waiting_state_not_due_alarm_state(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = [
            {"time": "23:59", "label": "拉伸", "enabled": True, "frequency_unit": "daily"},
        ]
        now = datetime(2026, 5, 31, 10, 11, 8).astimezone()

        segments = visual._time_compact_segments("10:11", "05/31 Sun", "idle", now=now)

        alarm = next(segment for segment in segments if segment["kind"] == "alarm")
        self.assertEqual(alarm["status"], "running")

    def test_time_compact_idle_date_summary_does_not_create_countdown_segment(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []
        now = datetime(2026, 5, 28, 5, 7, 12).astimezone()

        segments = visual._time_compact_segments("05:07", "05/28 Thu", "idle", now=now)

        self.assertEqual(segments, [{"kind": "time", "text": "05:07", "due_at": now}])

    def test_time_compact_idle_cell_does_not_paint_fake_countdown_icon(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []
        symbol_calls = []
        visual._paint_time_symbol = lambda *_args: symbol_calls.append(_args)
        image = QImage(240, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_compact_cell(painter, QRectF(8.0, 8.0, 210.0, 36.0), "05:07", "05/28 Thu", QColor(255, 184, 0), True, "idle", 0.0)
        painter.end()

        self.assertEqual(symbol_calls, [])

    def test_time_side_alert_running_icon_uses_larger_reference_scale(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        symbols = []
        visual._paint_time_symbol = lambda _painter, rect, kind, color, *_args: symbols.append((QRectF(rect), kind, QColor(color)))
        image = QImage(240, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 156.0, 42.0),
            "TIME",
            "10:11:08",
            "Countdown 4:47",
            QColor(255, 184, 0),
            "running",
            "Timer running",
            "Countdown 4:47",
            0.4,
        )
        painter.end()

        self.assertEqual(symbols[0][1], "countdown")
        self.assertGreaterEqual(symbols[0][0].width(), visual._u(30.0))

    def test_time_side_alert_circular_waiting_icon_is_centered_without_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        symbols = []
        static_texts = []
        alarm_scrolls = []
        visual._paint_time_symbol = lambda _painter, rect, kind, color, *_args: symbols.append((QRectF(rect), kind, QColor(color)))
        visual._paint_static_elided_text = lambda _painter, _rect, text, *_args, **_kwargs: static_texts.append(str(text))
        visual._paint_alarm_scrolling_text = lambda _painter, _rect, text, *_args, **_kwargs: alarm_scrolls.append(str(text))
        visual._paint_plugin_progress_ring = lambda *_args, **_kwargs: None
        image = QImage(90, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(12.0, 12.0, 42.0, 42.0)

        visual._paint_time_plugin_alert(
            painter,
            rect,
            "TIME",
            "07:30",
            "晨会",
            QColor(255, 184, 0),
            "running",
            "闹钟",
            "晨会",
            0.4,
            "alarm",
        )
        painter.end()

        self.assertEqual(symbols[0][1], "alarm")
        self.assertAlmostEqual(symbols[0][0].center().x(), rect.center().x(), delta=0.5)
        self.assertAlmostEqual(symbols[0][0].center().y(), rect.center().y(), delta=0.5)
        self.assertEqual(static_texts, [])
        self.assertEqual(alarm_scrolls, [])

    def test_time_side_alert_circular_done_uses_yellow_background_with_black_countdown_icon(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        symbols = []
        visual._paint_time_symbol = lambda _painter, rect, kind, color, *_args: symbols.append((QRectF(rect), kind, QColor(color)))
        visual._paint_static_elided_text = lambda *_args: (_ for _ in ()).throw(AssertionError("circular done fallback should be icon-only"))
        image = QImage(90, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 42.0, 42.0),
            "TIME",
            "0:00",
            "Countdown 0:00",
            QColor(255, 184, 0),
            "done",
            "Timer done",
            "Countdown 0:00",
            1.0,
        )
        painter.end()

        self.assertEqual(symbols[0][1], "countdown")
        self.assertLess(symbols[0][2].red(), 70)
        self.assertLess(symbols[0][2].green(), 70)
        self.assertLess(symbols[0][2].blue(), 70)

    def test_time_rectangular_side_alert_does_not_paint_ring_animation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        visual._paint_plugin_progress_ring = lambda *_args: (_ for _ in ()).throw(AssertionError("rectangular side alert should not paint progress ring"))
        image = QImage(240, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 156.0, 42.0),
            "TIME",
            "10:11:08",
            "Countdown 4:47",
            QColor(255, 184, 0),
            "running",
            "Timer running",
            "Countdown 4:47",
            0.4,
        )
        painter.end()

    def test_generic_rectangular_side_alert_does_not_paint_ring_animation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enabled_plugin_count = 1
        visual.scroll_phase = 0.0
        visual.plugin_samples = [
            ("WEATHER", "雷雨", "北京", QColor(80, 210, 255), "☁", "weather", "severe", "严重天气提醒", "北京 · 雷雨", 2, 1.0),
        ]
        visual._paint_plugin_progress_ring = lambda *_args: (_ for _ in ()).throw(AssertionError("rectangular side alert should not paint progress ring"))
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin_alert(painter, QRectF(12.0, 12.0, 180.0, 42.0), "plugin-alert:weather")
        painter.end()

    def test_narrow_non_circular_side_alert_does_not_paint_ring_animation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enabled_plugin_count = 1
        visual.scroll_phase = 0.0
        visual.plugin_samples = [
            ("WEATHER", "雷雨", "北京", QColor(80, 210, 255), "☁", "weather", "severe", "严重天气提醒", "北京 · 雷雨", 2, 1.0),
        ]
        visual._paint_plugin_progress_ring = lambda *_args: (_ for _ in ()).throw(AssertionError("only circular fallback should paint progress ring"))
        image = QImage(120, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin_alert(painter, QRectF(12.0, 12.0, 48.0, 42.0), "plugin-alert:weather")
        painter.end()

    def test_circular_boundary_side_alert_keeps_ring_animation_for_all_plugins(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enabled_plugin_count = 2
        visual.scroll_phase = 0.0
        visual.plugin_samples = [
            ("TIME", "10:11:08", "Countdown 4:47", QColor(255, 184, 0), "", "time", "running", "Timer running", "Countdown 4:47", 10, 0.4),
            ("WEATHER", "雷雨", "北京", QColor(80, 210, 255), "☁", "weather", "severe", "严重天气提醒", "北京 · 雷雨", 2, 1.0),
        ]
        calls = []
        visual._paint_plugin_progress_ring = lambda *_args: calls.append(_args)
        image = QImage(140, 120, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin_alert(painter, QRectF(10.0, 10.0, 42.0, 42.0), "plugin-alert:time")
        visual._paint_plugin_alert(painter, QRectF(64.0, 10.0, 42.0, 42.0), "plugin-alert:weather")
        painter.end()

        self.assertGreaterEqual(len(calls), 2)

    def test_generic_plugin_alert_renderer_has_no_time_redundant_branch(self):
        source = inspect.getsource(IslandStackVisual._paint_plugin_alert)

        self.assertNotIn("is_time_done", source)
        self.assertNotIn("_time_reminder_icon_path", source)

    def test_generic_plugin_focus_cell_has_no_time_redundant_branch(self):
        source = inspect.getsource(IslandStackVisual._paint_plugin_focus_cell)

        self.assertNotIn("is_time_done", source)
        self.assertNotIn("_time_reminder_icon_path", source)

    def test_time_focus_cell_uses_summary_and_progress_for_full_countdown(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        calls = []
        visual._paint_time_compact_cell = lambda _painter, _rect, detail, summary, _color, _active, status, progress: calls.append(
            (str(detail), str(summary), str(status), float(progress))
        )
        image = QImage(300, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin_focus_cell(
            painter,
            QRectF(8.0, 8.0, 250.0, 36.0),
            "TIME",
            "10:11:08",
            QColor(255, 184, 0),
            "T",
            1.0,
            False,
            "running",
            summary="倒计时 1:02:03",
            progress=0.62,
        )
        painter.end()

        self.assertEqual(calls, [("10:11:08", "倒计时 1:02:03", "running", 0.62)])

    def test_time_focused_plugin_strip_keeps_full_countdown_when_seconds_hidden(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.show_seconds = False
        visual.enabled_plugin_count = 1
        visual.plugin_focus = 0
        visual.plugin_focus_from = 0
        visual.plugin_focus_target = 0
        visual.plugin_focus_changed_at = 0.0
        visual.hovered_key = "plugin"
        visual.plugin_panel_open = True
        visual.plugin_samples = [
            ("TIME", "10:11:08", "倒计时 1:02:03", QColor(255, 184, 0), "T", "time", "running", "计时进行中", "倒计时 1:02:03", 30, 0.62),
        ]
        visual._plugin_panel_visible = lambda: True
        visual._active_plugin_focus = lambda _now: 0
        visual._plugin_focus_weights = lambda *_args, **_kwargs: [1.0]
        visual._plugin_item_rects = lambda rect, _now, expand_active=True: [(0, QRectF(rect))]
        visual._paint_plugin_solid_shell = lambda *_args, **_kwargs: None
        calls = []
        visual._paint_plugin_focus_cell = lambda _painter, _rect, label, detail, _color, _icon, _focus, _icon_mode, status, summary="", progress=0.0: calls.append(
            (str(label), str(detail), str(summary), str(status), float(progress))
        )
        image = QImage(340, 80, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin(painter, QRectF(20.0, 20.0, 260.0, 48.0), False)
        painter.end()

        self.assertEqual(calls, [("TIME", "10:11:08", "倒计时 1:02:03", "running", 0.62)])

    def test_time_plugin_panel_focus_model_keeps_full_countdown_when_seconds_hidden(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.show_seconds = False
        visual.enabled_plugin_count = 1
        visual.plugin_samples = [
            ("TIME", "10:11:08", "倒计时 1:02:03", QColor(255, 184, 0), "T", "time", "running", "计时进行中", "倒计时 1:02:03", 30, 0.62),
        ]
        visual._active_plugin_focus = lambda _now: 0

        model = visual._plugin_panel_focus_model()

        self.assertEqual(model["summary"], "倒计时 1:02:03")
        self.assertEqual(model["progress"], 0.62)

    def test_time_compact_segments_align_seconds_and_sort_by_due_time(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        now = datetime(2026, 5, 28, 10, 11, 8).astimezone()
        visual.show_seconds = False
        visual.plugin_countdown_entries = [
            {
                "text": "4:59",
                "end_at": (now + timedelta(minutes=5)).isoformat(timespec="seconds"),
                "status": "running",
            }
        ]
        visual.plugin_alarm_rows = [
            {"time": "10:13", "label": "Note", "enabled": True, "frequency": "daily"},
        ]

        segments = visual._time_compact_segments("10:11:08", "Countdown 4:59", "running", now=now)

        self.assertEqual(segments[0]["kind"], "time")
        self.assertEqual(segments[0]["text"], "10:11")
        self.assertEqual([segment["kind"] for segment in segments[1:]], ["alarm", "countdown"])
        self.assertEqual(segments[1]["text"], "Note")

        visual.show_seconds = True
        self.assertEqual(visual._time_compact_segments("10:11:08", "", "idle", now=now)[0]["text"], "10:11:08")

    def test_time_plugin_compact_cell_uses_time_specific_segments(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        called = []
        visual._paint_time_compact_cell = lambda *args: called.append(args)
        image = QImage(240, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin_compact_cell(painter, QRectF(8.0, 8.0, 210.0, 36.0), "TIME", "10:11", "Countdown 4:59", QColor(255, 184, 0), True, "running", 0.4)
        painter.end()

        self.assertTrue(called)

    def test_plugin_action_click_refreshes_weather(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        calls = []
        visual.settings_setter = lambda key, value: calls.append((key, value))
        visual.refresh_plugin_data = lambda: calls.append(("refresh", "plugins"))

        visual._activate_plugin_action("weather-refresh")

        self.assertEqual(calls[-1], ("refresh", "plugins"))
        self.assertTrue(any(key == "v2_weather_refresh_nonce" for key, _value in calls if key != "refresh"))


if __name__ == "__main__":
    unittest.main()
