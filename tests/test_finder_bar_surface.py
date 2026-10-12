"""A real Finder bar owns a bounded, movable single-row V2 presentation."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QAbstractAnimation, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.providers import EmptyIslandDataProvider, empty_visual_data
from local.island_stack_visual import IslandStackVisual


class FinderBarSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.visual = IslandStackVisual(EmptyIslandDataProvider(empty_visual_data()))
        self.visual.timer.stop()
        self.visual._force_super_topmost = Mock()
        self.visual._notify_settings_changed = Mock()
        self.visual._begin_surface_transition = lambda reason: False
        self.visual._pulse_stack = Mock()
        self.visual._effective_lyric_offset_seconds = lambda: 0.
        self.visual.music_playing = True
        self.visual.music_auto_detect = False
        self.visual.media_is_playing = True
        self.visual.media_position_source = "local-playback"
        self.visual.media_track_id = "finder-fixture"
        self.visual.track_title = "Next song"
        self.visual.artist = "GluSpot singer"
        self.visual.lyrics = ["Do me like you miss me", "下一句歌词"]
        self.visual.timed_lyrics = [(0., self.visual.lyrics[0]), (10., self.visual.lyrics[1])]
        self.visual.timed_lyric_units = [(), ()]
        self.visual.timed_lyric_timing_kinds = ["line", "line"]
        self.visual.media_position_seconds = 2.
        self.visual.media_position_sync_at = 100.
        self.visual.media_duration_seconds = 20.
        self.visual.album_art = QImage(48, 48, QImage.Format.Format_RGB32)
        self.visual.album_art.fill(QColor("#b05663"))
        self.visual.location_mode = "free"
        self.visual.finder_mode = False
        self.visual.mydockfinder_running = True
        self.screen = SimpleNamespace(name=lambda: "DISPLAY-fixture", geometry=lambda: QRect(-1920, 0, 1920, 1080), devicePixelRatio=lambda: 1.)
        self.bar = {"hwnd": 1, "pid": 2, "enabled": True, "visible": True,
            "physical_rect": [-1920, 0, 0, 24], "physical_resting_rect": [-1920, 0, 0, 24],
            "physical_monitor_rect": [-1920, 0, 0, 1080], "monitor_name": "DISPLAY-fixture"}

    def tearDown(self):
        self.visual.close()
        self.visual.deleteLater()
        self.app.processEvents()

    def presence(self, bars=None):
        with patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen]), \
                patch("local.island_stack_visual.read_finder_material", return_value=self.visual.finder_material):
            self.visual.set_finder_bar_presence([self.bar] if bars is None else bars)

    def test_true_bar_auto_attaches_inside_its_actual_negative_origin(self):
        self.presence()
        self.assertEqual(self.visual.location_mode, "finder")
        self.assertEqual(self.visual.pos(), QPoint(-1920, 0))
        self.assertEqual(self.visual.finder_bar_rect, QRectF(-1920, 0, 1920, 24))
        main = self.visual._build_visuals()[0]
        self.assertEqual(main.rect.top(), 0.)
        self.assertEqual(main.rect.height(), 24.)
        self.assertEqual(self.visual.available_location_modes(), ["free", "finder"])

    def test_installed_or_dock_only_does_not_open_finder(self):
        self.presence([])
        self.assertEqual(self.visual.location_mode, "free")
        self.assertEqual(self.visual.available_location_modes(), ["free"])
        self.visual.set_location_mode("finder", notify=False)
        self.assertEqual(self.visual.location_mode, "free")

    def test_entering_finder_stops_old_taskbar_position_animations(self):
        animations = []
        for name in ("taskbar_dock_animation", "taskbar_follow_animation"):
            animation = QPropertyAnimation(self.visual, b"pos")
            animation.setDuration(80)
            animation.setStartValue(QPoint(50, 900))
            animation.setEndValue(QPoint(50, 1050))
            setattr(self.visual, name, animation)
            animation.start()
            animations.append(animation)
        self.presence()
        self.assertEqual(self.visual.pos(), QPoint(-1920, 0))
        self.assertTrue(all(a.state() == QAbstractAnimation.State.Stopped for a in animations))
        self.assertIsNone(self.visual.taskbar_dock_animation)
        self.assertIsNone(self.visual.taskbar_follow_animation)

    def test_user_free_choice_survives_ordinary_presence_refresh(self):
        self.presence()
        self.visual.set_location_mode("free", notify=False)
        self.presence()
        self.assertEqual(self.visual.location_mode, "free")
        self.presence([])
        self.presence()
        self.assertEqual(self.visual.location_mode, "finder")

    def test_auto_hide_does_not_detach_and_reveal_keeps_full_height(self):
        self.presence()
        self.bar["visible"] = False
        self.bar["physical_rect"] = [-1920, -23, 0, 1]
        self.presence()
        self.assertEqual(self.visual.location_mode, "finder")
        self.assertEqual(self.visual.finder_bar_rect.height(), 24.)
        self.assertEqual(self.visual._detect_scene_suppression(), (True, "finder-hidden"))
        self.bar["visible"] = True
        self.presence()
        self.assertEqual(self.visual._detect_scene_suppression(), (False, ""))
        self.assertTrue(self.visual.media_is_playing)

    def test_follow_uses_cached_hwnd_at_bounded_existing_tick_cadence(self):
        self.presence()
        fresh = dict(self.bar, visible=False)
        with patch("local.island_stack_visual.refresh_live_finder_bar", return_value=fresh) as probe, \
                patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen]):
            self.visual._refresh_finder_bar_follow_position(now=100.)
            self.visual._refresh_finder_bar_follow_position(now=100.01)
            self.visual._refresh_finder_bar_follow_position(now=100.11)
        self.assertEqual(probe.call_count, 2)
        self.assertFalse(self.visual.finder_bar_visible)

    def test_horizontal_drag_persists_finder_ratio_without_moving_bar(self):
        self.presence()
        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=self.screen):
            self.visual._apply_finder_drag_action(QPoint(-1440, 12), QPoint(30, 0))
        self.assertAlmostEqual(self.visual.finder_bar_center_ratio, .25)
        self.assertEqual(self.visual.pos(), QPoint(-1920, 0))
        self.assertAlmostEqual(self.visual.settings_snapshot()["v2_finder_bar_center_ratio"], .25)
        self.assertLess(self.visual._build_visuals()[0].rect.right(), 1920.)

    def test_attach_requires_actual_bar_hit_instead_of_any_screen_top(self):
        self.presence()
        self.visual.location_mode = "free"
        self.visual.finder_mode = False
        self.assertTrue(self.visual._should_attach_to_finder(QPoint(-1500, 10), QPoint(0, -20)))
        self.assertFalse(self.visual._should_attach_to_finder(QPoint(200, 10), QPoint(0, -20)))
        self.assertFalse(self.visual._should_attach_to_finder(QPoint(-1500, 500), QPoint(0, -20)))

    def test_dock_blocks_every_tasker_presence_and_stick_settle_path(self):
        self.presence()
        self.visual.set_location_mode("free", notify=False)
        self.visual._set_taskbar_presence(True)
        self.assertFalse(self.visual.taskbar_preview_visible)
        self.assertFalse(self.visual._accept_taskbar_preview_drop())
        self.assertFalse(self.visual._settle_standalone_above_taskbar(QRectF(0, 0, 200, 40), self.screen, QRectF(0, 1040, 1920, 40)))
        self.visual.set_location_mode("tasker", notify=False)
        self.assertEqual(self.visual.location_mode, "free")

    def test_external_fullscreen_does_not_hide_finder_or_tasker(self):
        self.presence()
        for mode in ("finder", "tasker"):
            self.visual.location_mode = mode
            self.visual.finder_mode = mode == "finder"
            for reason in ("fullscreen", "maximized", "video"):
                self.visual.scene_checked_at = 0.
                with patch.object(self.visual, "_detect_scene_suppression", return_value=(True, reason)):
                    self.visual._update_scene_state(100.)
                self.assertFalse(self.visual.scene_suppressed, (mode, reason))
                self.assertTrue(self.visual.media_is_playing)

    def test_default_transparent_face_has_only_cover_and_timed_lyrics(self):
        self.presence()
        width, height = self.visual._finder_bar_music_size()
        image = QImage(round(width), round(height), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch("local.island_stack_visual.time.monotonic", return_value=100.), QPainter(image) as painter:
            self.visual._paint_music(painter, QRectF(0, 0, width, height), False)
        self.assertEqual(self.visual.finder_bar_style, "transparent")
        self.assertEqual(image.pixelColor(round(width/2), 0).alpha(), 0)
        self.assertGreater(image.pixelColor(9, 12).alpha(), 0)
        self.assertTrue(self.visual.music_cover_rect.height() <= height)
        self.assertEqual(self.visual.music_button_rects, {})

    def test_three_styles_and_cover_hit_are_bounded_to_real_bar_height(self):
        self.presence()
        for style in ("capsule", "fade", "transparent"):
            self.visual.set_finder_bar_style(style, notify=False)
            width, height = self.visual._finder_bar_music_size()
            region = QRectF(0, 0, width, height)
            image = QImage(round(width), round(height), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with patch("local.island_stack_visual.time.monotonic", return_value=100.), QPainter(image) as painter:
                self.visual._paint_finder_bar_music(painter, region)
            self.assertTrue(region.contains(self.visual.music_cover_rect))
            self.assertEqual(self.visual._hit_test(self.visual.music_cover_rect.center()), "music:open_surface")
            if style != "transparent":
                self.assertGreater(image.pixelColor(round(width/2), 0 if style == "fade" else 1).alpha(), 180)

    def test_unknown_screen_does_not_fallback_to_primary_finder(self):
        self.bar["monitor_name"] = "unmatched"
        self.bar["physical_monitor_rect"] = [0, 0, 2560, 1440]
        self.presence()
        self.assertFalse(self.visual._finder_bar_available())
        self.assertEqual(self.visual.location_mode, "free")

    def test_friendly_screen_name_uses_unique_bounds_for_mapping_and_material(self):
        self.screen.name = lambda: "Display product name"
        self.presence()
        self.assertTrue(self.visual._finder_bar_active())
        with patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen]):
            self.assertIs(self.visual._finder_bar_screen(), self.screen)

    def test_expanded_tasker_auto_hide_suppresses_face_and_input_without_pause(self):
        self.visual.location_mode = "tasker"
        self.visual.finder_mode = False
        self.visual.mydockfinder_running = False
        self.visual.expanded = True
        self.visual.expansion = 1.
        self.visual._set_expanded = Mock()
        self.visual.taskbar_follow_last_hidden = True
        self.assertEqual(self.visual._detect_scene_suppression(), (True, "taskbar-hidden"))
        self.visual.scene_checked_at = 0.
        self.visual._update_scene_state(100.)
        self.assertTrue(self.visual.scene_suppressed)
        self.assertTrue(self.visual.input_passthrough_enabled)
        self.assertTrue(self.visual.media_is_playing)
        self.assertEqual(self.visual.media_position_seconds, 2.)
        self.visual._set_expanded.assert_called_once_with(False)
        self.visual.taskbar_follow_last_hidden = False
        self.visual.scene_checked_at = 0.
        self.visual._update_scene_state(101.)
        self.assertFalse(self.visual.scene_suppressed)
        self.assertFalse(self.visual.input_passthrough_enabled)
        self.assertTrue(self.visual.media_is_playing)

    def test_missing_music_has_no_empty_overlay_in_finder_bar(self):
        self.presence()
        self.visual.music_playing = False
        self.assertEqual(self.visual._build_visuals(), [])

    def test_transparent_finder_ink_and_dj_rings_use_actual_bar_tone(self):
        self.presence()
        for level in (20, 128, 240):
            self.visual.finder_material.mid_color = QColor(level, level, level)
            base = self.visual._music_lyric_base_text_color()
            live = self.visual._music_lyric_text_color()
            self.assertEqual(base.getRgb()[:3], live.getRgb()[:3])
            ink = self.visual._personal_dj_island_color()
            from local.music_surface_palette import relative_luminance
            a, b = relative_luminance(ink), relative_luminance(QColor(level, level, level))
            self.assertGreaterEqual((max(a, b) + .05) / (min(a, b) + .05), 2.30)


if __name__ == "__main__":
    unittest.main()
