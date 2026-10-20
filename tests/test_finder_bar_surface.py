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
        self.bar = {"hwnd": 1, "pid": 2, "enabled": True, "visible": True, "window_visible": True,
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
        self.assertEqual(self.visual.available_location_modes(), ["top", "free"])
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
        self.assertEqual(probe.call_count, 3)
        self.assertFalse(self.visual.finder_bar_visible)

    def test_horizontal_drag_persists_finder_ratio_without_moving_bar(self):
        self.presence()
        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=self.screen), \
                patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen]), \
                patch("local.island_stack_visual.refresh_live_finder_bar", side_effect=lambda snapshot: dict(snapshot)):
            self.visual._apply_finder_drag_action(QPoint(-1440, 12), QPoint(30, 0))
        self.assertAlmostEqual(self.visual.finder_bar_center_ratio, .25)
        self.assertEqual(self.visual.pos(), QPoint(-1920, 0))
        self.assertAlmostEqual(self.visual.settings_snapshot()["v2_finder_bar_center_ratio"], .25)
        self.assertLess(self.visual._build_visuals()[0].rect.right(), 1920.)

    def test_attach_requires_actual_bar_hit_instead_of_any_screen_top(self):
        self.presence()
        self.visual.location_mode = "free"
        self.visual.finder_mode = False
        with patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen]), \
                patch("local.island_stack_visual.refresh_live_finder_bar", side_effect=lambda snapshot: dict(snapshot)):
            self.assertTrue(self.visual._should_attach_to_finder(QPoint(-1500, 10), QPoint(0, -20)))
            self.assertFalse(self.visual._should_attach_to_finder(QPoint(200, 10), QPoint(0, -20)))
            self.assertFalse(self.visual._should_attach_to_finder(QPoint(-1500, 500), QPoint(0, -20)))

    def test_late_presence_during_drag_preserves_geometry_grab_and_detach_snapshot(self):
        visual = self.visual
        visual.drag_candidate, visual.dragging = True, True
        visual.drag_press_global = QPoint(-1000, 300)
        visual.drag_press_window_pos = QPoint(-1400, 200)
        visual.drag_press_main_offset = QPointF(40., 12.)
        visual.drag_detach_global_rects = {"music": QRectF(-1100., 290., 300., 40.)}
        before = visual.pos(), visual.size(), dict(visual.drag_detach_global_rects)
        self.presence()
        self.presence()
        with patch("local.island_stack_visual.refresh_live_finder_bar") as probe:
            self.assertFalse(visual._refresh_finder_bar_follow_position(now=100.))
        probe.assert_not_called()
        self.assertEqual(visual.location_mode, "free")
        self.assertEqual((visual.pos(), visual.size(), visual.drag_detach_global_rects), before)
        self.assertEqual(visual.drag_press_global, QPoint(-1000, 300))
        self.assertEqual(visual.drag_press_window_pos, QPoint(-1400, 200))
        self.assertEqual(visual.drag_press_main_offset, QPointF(40., 12.))
        self.assertTrue(visual._finder_presence_reconcile_pending)
        self.assertEqual(len(visual.finder_bar_snapshots), 1)

    def _release_drag_at(self, global_pos):
        event = SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton,
            globalPosition=lambda: QPointF(global_pos), position=lambda: QPointF(40., 12.))
        self.visual._accept_taskbar_preview_drop = lambda: False
        self.visual._save_placement_state = Mock()
        self.visual._current_main_target_global_rect = lambda: QRectF(-1100., 290., 300., 40.)
        with patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen]), \
                patch("local.island_stack_visual.refresh_live_finder_bar", side_effect=lambda snapshot: dict(snapshot)), \
                patch("local.island_stack_visual.read_finder_material", return_value=self.visual.finder_material):
            self.visual.mouseReleaseEvent(event)

    def test_late_presence_drag_release_attaches_only_when_release_hits_real_bar(self):
        self.visual.drag_candidate, self.visual.dragging = True, True
        self.presence()
        self._release_drag_at(QPoint(-1500, 10))
        self.assertEqual(self.visual.location_mode, "finder")
        self.assertEqual(self.visual.pos(), QPoint(-1920, 0))
        self.assertFalse(self.visual.dragging)
        self.assertFalse(self.visual.drag_candidate)

    def test_late_presence_drag_release_away_from_bar_requests_free_settlement(self):
        visual = self.visual
        visual.drag_candidate, visual.dragging = True, True
        before = visual.pos(), visual.size()
        self.presence()
        visual._settle_standalone_drag_release = Mock(return_value=True)
        self._release_drag_at(QPoint(-1000, 300))
        visual._settle_standalone_drag_release.assert_called_once_with(
            QRectF(-1100., 290., 300., 40.), QPoint(-1000, 300))
        self.assertEqual(visual.location_mode, "free")
        self.assertEqual((visual.pos(), visual.size()), before)
        self.assertFalse(visual.finder_mode)

    def test_late_presence_during_held_press_does_not_attach_on_unrelated_click_release(self):
        visual = self.visual
        visual.drag_candidate, visual.dragging = True, False
        before = visual.pos(), visual.size()
        self.presence()
        visual._hit_test = lambda _point: ""
        self._release_drag_at(QPoint(-1000, 300))
        self.assertEqual(visual.location_mode, "free")
        self.assertEqual((visual.pos(), visual.size()), before)
        self.assertFalse(visual._finder_presence_reconcile_pending)

    def test_held_click_on_live_bar_reconciles_data_without_attaching_or_resetting(self):
        visual = self.visual
        visual.drag_candidate, visual.dragging = True, False
        before = visual.pos(), visual.size()
        self.presence()
        visual._hit_test = lambda _point: ""
        visual._reset_location_runtime_state = Mock(side_effect=AssertionError("A click cannot reset the surface"))
        self._release_drag_at(QPoint(-1500, 10))
        self.assertEqual(visual.location_mode, "free")
        self.assertEqual((visual.pos(), visual.size()), before)
        self.assertEqual(visual._finder_bar_hwnd, 1)
        self.assertFalse(visual._finder_presence_reconcile_pending)

    def test_held_navigation_click_does_not_normalize_or_reset_active_page(self):
        visual = self.visual
        for page in ("music_fullscreen_overlay", "_music_library_windows"):
            with self.subTest(page=page):
                visual.music_fullscreen_overlay = None
                visual._music_library_windows = []
                visual.location_mode = "tasker"
                visual.drag_candidate, visual.dragging = True, False
                self.presence()
                visual.drag_press_key = "music:open_surface"
                active_page = SimpleNamespace(isVisible=lambda: True, isMinimized=lambda: False)
                visual._activate_key = lambda _key: setattr(visual, page,
                    [active_page] if page == "_music_library_windows" else active_page)
                visual._start_island_button_ripple = Mock()
                visual._reset_location_runtime_state = Mock(side_effect=AssertionError("Navigation owns the surface"))
                self._release_drag_at(QPoint(-1500, 10))
                self.assertTrue(visual._navigation_surface_blocks_island())
                self.assertEqual(visual.location_mode, "tasker")
                self.assertEqual(visual._finder_bar_hwnd, 1)
                self.assertFalse(visual._finder_presence_reconcile_pending)
        visual.music_fullscreen_overlay = None
        visual._music_library_windows = []

    def test_live_target_rejects_hidden_missing_cloaked_minimized_and_offscreen_owner(self):
        self.presence()
        visual = self.visual
        visual.location_mode, visual.finder_mode = "free", False
        visual._reset_location_runtime_state = Mock(side_effect=AssertionError("Hidden owners cannot attach"))
        hidden_states = {
            "missing": None,
            "SW_HIDE": dict(self.bar, window_visible=False),
            "cloaked": dict(self.bar, cloaked=True),
            "minimized": dict(self.bar, minimized=True),
            "slide_complete": dict(self.bar, visible=False, physical_rect=[-1920, -23, 0, 1]),
        }
        for state, snapshot in hidden_states.items():
            with self.subTest(state=state), \
                    patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen]), \
                    patch("local.island_stack_visual.refresh_live_finder_bar", return_value=snapshot) as probe:
                self.assertIsNone(visual._finder_bar_drag_target(QPoint(-1500, 10)))
                self.assertFalse(visual._should_attach_to_finder(QPoint(-1500, 10), QPoint(0, -20)))
                self.assertFalse(visual._attach_to_finder_for_drag(QPoint(-1500, 10)))
                self.assertEqual(visual.location_mode, "free")
                self.assertTrue(probe.called)

    def test_partial_live_target_accepts_only_current_visible_monitor_intersection(self):
        self.presence()
        partial = dict(self.bar, visible=False, physical_rect=[-1920, -12, 0, 12])
        with patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen]), \
                patch("local.island_stack_visual.refresh_live_finder_bar", return_value=partial):
            target = self.visual._finder_bar_drag_target(QPoint(-1500, 6))
            self.assertEqual(target[0], partial)
            self.assertEqual(target[1], QRectF(-1920, 0, 1920, 24))
            self.assertIsNone(self.visual._finder_bar_drag_target(QPoint(-1500, 16)))
            self.assertIsNone(self.visual._finder_bar_drag_target(QPoint(-1500, -6)))

    def test_selected_live_snapshot_overrides_old_poll_inventory_before_probe(self):
        self.presence()
        latest = dict(self.bar, visible=False, physical_rect=[-1920, -12, 0, 12], marker="latest")
        self.visual._finder_bar_snapshot = latest
        with patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen]), \
                patch("local.island_stack_visual.refresh_live_finder_bar", side_effect=lambda snapshot: dict(snapshot)) as probe:
            self.assertIsNone(self.visual._finder_bar_drag_target(QPoint(-1500, 16)))
        probe.assert_called_once_with(latest)

    def test_partial_live_target_preserves_negative_monitor_origin_and_mixed_dpi(self):
        screen = SimpleNamespace(name=lambda: "upper-left", geometry=lambda: QRect(-1280, -720, 1280, 720), devicePixelRatio=lambda: 1.5)
        bar = dict(self.bar, monitor_name="upper-left", physical_rect=[-1280, -720, 640, -684],
            physical_resting_rect=[-1280, -720, 640, -684], physical_monitor_rect=[-1280, -720, 640, 360])
        partial = dict(bar, visible=False, physical_rect=[-1280, -738, 640, -702])
        with patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen, screen]), \
                patch("local.island_stack_visual.read_finder_material", return_value=self.visual.finder_material), \
                patch("local.island_stack_visual.refresh_live_finder_bar", return_value=partial):
            self.visual.set_finder_bar_presence([bar])
            target = self.visual._finder_bar_drag_target(QPoint(-640, -714))
            self.assertEqual(target[1], QRectF(-1280, -720, 1280, 24))
            self.assertIs(target[2], screen)
            self.assertIsNone(self.visual._finder_bar_drag_target(QPoint(-640, -704)))
            self.assertIsNone(self.visual._finder_bar_drag_target(QPoint(-640, -726)))

    def test_nonselected_stale_candidate_requires_live_refresh_before_hit(self):
        right_screen = SimpleNamespace(name=lambda: "right", geometry=lambda: QRect(0, 0, 1280, 720), devicePixelRatio=lambda: 1.5)
        right_bar = dict(self.bar, hwnd=3, pid=4, monitor_name="right",
            physical_rect=[0, 0, 1920, 36], physical_resting_rect=[0, 0, 1920, 36], physical_monitor_rect=[0, 0, 1920, 1080])
        hidden_right = dict(right_bar, visible=False, physical_rect=[0, -35, 1920, 1])
        with patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen, right_screen]), \
                patch("local.island_stack_visual.read_finder_material", return_value=self.visual.finder_material):
            self.visual.set_finder_bar_presence([self.bar, right_bar])
            with patch("local.island_stack_visual.refresh_live_finder_bar",
                    side_effect=lambda snapshot: hidden_right if snapshot["hwnd"] == 3 else dict(snapshot)) as probe:
                self.assertIsNone(self.visual._finder_bar_drag_target(QPoint(640, 10)))
            self.assertEqual([call.args[0]["hwnd"] for call in probe.call_args_list], [1, 3])

    def test_lost_finder_during_drag_normalizes_after_release_and_preserves_no_stale_owner(self):
        visual = self.visual
        self.presence()
        visual.drag_candidate, visual.dragging = True, True
        before = visual.pos(), visual.size()
        self.presence([])
        self.assertEqual((visual.pos(), visual.size()), before)
        visual._settle_standalone_drag_release = Mock(return_value=True)
        self._release_drag_at(QPoint(-1000, 300))
        self.assertEqual(visual.location_mode, "free")
        self.assertFalse(visual._finder_bar_available())
        self.assertEqual(visual._finder_bar_hwnd, 0)

    def test_dock_starting_during_drag_does_not_reset_grab_state(self):
        visual = self.visual
        visual.mydockfinder_running = False
        visual.drag_candidate, visual.dragging = True, True
        visual.location_mode = "tasker"
        visual._reset_location_runtime_state = Mock(side_effect=AssertionError("No runtime reset during drag"))
        visual.set_mydockfinder_running(True, notify=False)
        self.presence()
        self.assertEqual(visual.location_mode, "tasker")
        self.assertTrue(visual._finder_presence_reconcile_pending)

    def test_drag_uses_target_bar_on_another_monitor_and_selected_runtime_material(self):
        right_screen = SimpleNamespace(name=lambda: "right", geometry=lambda: QRect(0, 0, 1280, 720), devicePixelRatio=lambda: 1.5)
        right_bar = dict(self.bar, hwnd=3, pid=4, monitor_name="right",
            physical_rect=[0, 0, 1920, 36], physical_resting_rect=[0, 0, 1920, 36], physical_monitor_rect=[0, 0, 1920, 1080])
        visual = self.visual
        with patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen, right_screen]), \
                patch("local.island_stack_visual.refresh_live_finder_bar", side_effect=lambda snapshot: dict(snapshot)), \
                patch("local.island_stack_visual.read_finder_material", return_value=visual.finder_material):
            visual.set_finder_bar_presence([self.bar, right_bar])
            self.assertEqual(visual._finder_bar_hwnd, 1)
            self.assertTrue(visual._should_attach_to_finder(QPoint(640, 10), QPoint(0, -20)))
            visual._attach_to_finder_for_drag(QPoint(640, 10))
            self.assertEqual(visual._finder_bar_hwnd, 3)
            self.assertEqual(visual.finder_bar_rect, QRectF(0., 0., 1280., 24.))
            self.assertEqual(visual.pos(), QPoint(0, 0))
            self.assertAlmostEqual(visual.finder_bar_center_ratio, .5)
            self.assertIs(visual._finder_bar_screen(), right_screen)
            visual.set_finder_bar_presence([self.bar, right_bar])
            self.assertEqual(visual._finder_bar_hwnd, 3)

    def test_attached_horizontal_drag_switches_real_owner_without_primary_fallback(self):
        right_screen = SimpleNamespace(name=lambda: "right", geometry=lambda: QRect(0, 0, 1920, 1080), devicePixelRatio=lambda: 1.)
        right_bar = dict(self.bar, hwnd=3, pid=4, monitor_name="right", physical_rect=[0, 0, 1920, 24],
            physical_resting_rect=[0, 0, 1920, 24], physical_monitor_rect=[0, 0, 1920, 1080])
        with patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen, right_screen]), \
                patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=right_screen), \
                patch("local.island_stack_visual.refresh_live_finder_bar", side_effect=lambda snapshot: dict(snapshot)), \
                patch("local.island_stack_visual.read_finder_material", return_value=self.visual.finder_material):
            self.visual.set_finder_bar_presence([self.bar, right_bar])
            self.visual._apply_finder_drag_action(QPoint(960, 12), QPoint(1200, 0))
            self.assertEqual(self.visual._finder_bar_hwnd, 3)
            self.assertEqual(self.visual.pos(), QPoint(0, 0))
            self.assertAlmostEqual(self.visual.finder_bar_center_ratio, .5)

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
            self.visual.mydockfinder_running = mode == "finder"
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
