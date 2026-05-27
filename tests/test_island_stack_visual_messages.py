from __future__ import annotations

import unittest

from PySide6.QtGui import QColor
from PySide6.QtCore import QRectF

from local.island_stack_visual import IslandKind, IslandStackVisual, Message


class IslandStackVisualMessageTests(unittest.TestCase):
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

        snapshot = visual.settings_snapshot()

        self.assertEqual(snapshot["v2_dual_line_secondary_weight"], 88)
        self.assertEqual(snapshot["v2_dual_line_secondary_spacing"], 124)

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

    def test_plugin_alerts_can_queue_due_and_soon_plugins(self):
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

    def test_running_timer_creates_plugin_alert(self):
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

    def test_plugin_tuple_reads_progress_field_compatibly(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        plugin = visual._plugin_tuple(("TIME", "12:05", "倒计时 4:52", QColor(67, 162, 255), "●", "time", "running", "", "", 30, 0.52))

        self.assertAlmostEqual(plugin[10], 0.52)

    def test_compact_plugin_value_is_value_only(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        value = visual._plugin_compact_value("TIME", "12:05", "倒计时 4:52", "running")

        self.assertEqual(value, "4:52")
        self.assertNotIn("倒计时", value)

    def test_expanded_plugin_main_surface_is_function_panel_height(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True

        _width, height = visual._main_size_for_kind(IslandKind.PLUGIN, 420.0)

        self.assertGreater(height, 160.0 * IslandStackVisual.VISUAL_SCALE)

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

    def test_plugin_alert_display_value_is_value_only(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        value = visual._plugin_alert_display_value("TIME", "12:05:49", "Countdown 0:24", "running", "Timer running", "Countdown 0:24")

        self.assertEqual(value, "0:24")
        self.assertNotIn("Countdown", value)

    def test_plugin_alert_width_is_small_for_value_only_alerts(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        width = visual._plugin_alert_width_for("TIME", "12:05:49", "0:24")

        self.assertLessEqual(width, 174.0 * IslandStackVisual.VISUAL_SCALE)

    def test_expanded_plugin_panel_is_not_overlong(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True

        _width, height = visual._main_size_for_kind(IslandKind.PLUGIN, 420.0)

        self.assertLessEqual(height, 190.0 * IslandStackVisual.VISUAL_SCALE)

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

    def test_expanded_plugin_keeps_alert_inside_plugin_panel(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = True
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

        self.assertEqual([item.key for item in visuals], ["plugin"])
        self.assertGreater(visual._plugin_panel_alert_progress(0), 0.0)

    def test_plugin_ring_style_is_thick_for_animation_first_language(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        style = visual._plugin_ring_style("running")

        self.assertGreaterEqual(style["width"], 3.4 * IslandStackVisual.VISUAL_SCALE)
        self.assertGreater(style["pulse"], 0.0)

    def test_plugin_function_lines_include_alert_context(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        _title, lines = visual._plugin_function_lines("TIME", "00:24", "Countdown 0:24", "running", "Timer running", "Countdown 0:24")

        self.assertIn("Countdown 0:24", lines)


if __name__ == "__main__":
    unittest.main()
