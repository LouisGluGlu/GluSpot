"""The seven visual complaints: lyrics motion, glass, cover, theme, library, magnet, corners."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.music_lyric_motion import WrappedLineSpringChain
from local.music_surface_layout import resting_cover_side
from local.settings_store import (
    CONTROL_OPACITY_DEFAULT, DEFAULT_SETTINGS, SettingsManager, normalize_control_opacity,
)
from local.spring_motion import magnet_tile_radius, magnet_tile_rect, scaled_cover_corner_radius
from local.theme_tokens import control_plate_rgba, library_surface_rgb
from local.ui_theme import library_is_dark
from tests.apple_lyric_fixture import make_surface, render
from tests.test_music_surface_transition import _TransitionOwner
from local.music_surface_window import MusicSurfaceWindow


class ComplaintRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_wrap_spring_is_critically_damped_and_moves_on_a_new_line(self):
        ratio = WrappedLineSpringChain.DAMPING / (2.0 * math.sqrt(WrappedLineSpringChain.SPRING))
        self.assertAlmostEqual(ratio, 1.0, places=6)
        chain = WrappedLineSpringChain()
        chain.frame("row", (4, 4), 0.1, 0.0)
        moved = chain.frame("row", (4, 4), 0.8, 0.05)
        self.assertEqual(moved.active_line, 1)
        self.assertGreater(abs(moved.offsets[0]) + abs(moved.velocities[0]), 0.0)

    def test_apple_active_line_uses_existing_float_and_glow(self):
        _owner, surface = make_surface(True, 1.2)
        try:
            with patch.object(surface, "_paint_wrapped_lifted_lyric", wraps=surface._paint_wrapped_lifted_lyric) as lift:
                render(surface)
            self.assertTrue(lift.called)
            self.assertTrue(callable(surface._paint_lyric_scatter_glow))
            self.assertGreater(surface._reading_lift_height(), 1.0)
        finally:
            surface.shutdown()
            _owner.close()

    def test_wide_cover_uses_the_previous_side_and_narrow_corners_match(self):
        side = resting_cover_side(960, 540)
        unit = min(960 / 100.0, 540 / 75.8)
        self.assertAlmostEqual(side, unit * 37.8, delta=0.2)
        self.assertGreater(side, min(unit * 36.5, 960 * 0.40, 540 * 0.44) + 10)
        owner = _TransitionOwner()
        window = MusicSurfaceWindow(owner)
        try:
            window.resize(360, 640)
            window._fullscreen = False
            window._sync_surface_shape()
            radius = window._window_corner_radius()
            # A tall, narrow window gets a clearly rounded corner (the cover
            # ratio gave about 8 px and read as a sharp box).
            self.assertGreater(radius, window._album_art_radius(resting_cover_side(360, 640)) * 2)
            self.assertAlmostEqual(radius, min(window.NARROW_WINDOW_CORNER, 360 * 0.08))
            self.assertFalse(window.mask().isEmpty())
            self.assertFalse(window.mask().contains(window.rect().topLeft()))
            wide = window._window_corner_radius(window.size().__class__(960, 540))
            # Wide windows keep the cover's corner ratio.
            self.assertAlmostEqual(wide, window._album_art_radius(resting_cover_side(960, 540)), places=4)
        finally:
            window.shutdown()
            owner.close()

    def test_narrow_thumbnail_uses_the_big_cover_radius_ratio(self):
        owner = _TransitionOwner()
        window = MusicSurfaceWindow(owner)
        try:
            window.resize(360, 640)
            window._content_layout_fit_narrow = True
            window._width_layout_fraction = 1.0
            big = scaled_cover_corner_radius(240)
            small = window._album_art_radius(48)
            self.assertAlmostEqual(big / 240, small / 48)
            path = window._cover_clip_path(QRectF(0, 0, 48, 48), small)
            self.assertFalse(path.contains(QRectF(0, 0, 1, 1).center()))
            self.assertTrue(path.contains(QRectF(24, 24, 1, 1).center()))
        finally:
            window.shutdown()
            owner.close()

    def test_control_opacity_is_stored_per_mode(self):
        self.assertEqual(normalize_control_opacity(True), CONTROL_OPACITY_DEFAULT)
        self.assertEqual(normalize_control_opacity(140), 100)
        self.assertEqual(control_plate_rgba(False, 0.5), (255, 255, 255, 128))
        self.assertEqual(control_plate_rgba(True, 1), (0, 0, 0, 255))
        self.assertIn("v2_control_opacity_light", DEFAULT_SETTINGS)
        self.assertIn("v2_control_opacity_dark", DEFAULT_SETTINGS)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            settings = SettingsManager(settings_path=path)
            settings.set("v2_control_opacity_light", 20)
            settings.set("v2_control_opacity_dark", 80)
            settings.save_now()
            restored = SettingsManager(settings_path=path)
            self.assertEqual(restored.get("v2_control_opacity_light"), 20)
            self.assertEqual(restored.get("v2_control_opacity_dark"), 80)
            restored._save_timer.stop()
            settings._save_timer.stop()

    def test_theme_ink_follows_the_plate_not_the_album(self):
        owner = _TransitionOwner()
        owner.settings_getter = lambda key, default=None: {
            "v2_theme_mode": "light",
            "v2_music_ambience_style": "pure_white",
        }.get(key, default)
        window = MusicSurfaceWindow(owner)
        try:
            window._background_cache = QImage(32, 32, QImage.Format.Format_RGB32)
            window._background_cache.fill(QColor("black"))
            self.assertEqual(window._surface_glass_ink(QRectF(0, 0, 20, 20)), QColor("black"))
            owner.settings_getter = lambda key, default=None: "dark" if key == "v2_theme_mode" else default
            window._control_color_transitions.clear()
            window._background_cache.fill(QColor("white"))
            self.assertEqual(window._surface_glass_ink(QRectF(0, 0, 20, 20)), QColor("white"))
        finally:
            window.shutdown()
            owner.close()

    def test_sea_level_is_dark_and_library_planes_differ(self):
        def getter(key, default=None):
            if key == "v2_theme_mode":
                return "light"
            if key == "v2_music_ambience_style":
                return "sea_level"
            return default
        self.assertTrue(library_is_dark(getter))
        light = library_surface_rgb(False)
        dark = library_surface_rgb(True)
        self.assertEqual(light["page"], (255, 255, 255))
        self.assertEqual(light["ink"], (0, 0, 0))
        self.assertEqual(dark["page"], (0, 0, 0))
        self.assertEqual(dark["ink"], (255, 255, 255))
        self.assertNotEqual(light["sidebar"], light["content"])
        self.assertNotEqual(light["header"], light["content"])
        self.assertNotEqual(dark["sidebar"], dark["content"])
        self.assertNotEqual(dark["header"], dark["content"])

    def test_magnet_only_exists_for_finder_and_tasker(self):
        screen = QRectF(0, 0, 1920, 1080)
        finder_bar = QRectF(80, 8, 1760, 46)
        taskbar = QRectF(0, 1040, 1920, 40)
        finder = magnet_tile_rect(screen, mode="finder", bar=finder_bar)
        task = magnet_tile_rect(screen, mode="taskbar", bar=taskbar)
        self.assertTrue(finder_bar.contains(finder.center()))
        self.assertAlmostEqual(finder.height(), finder_bar.height())
        self.assertAlmostEqual(magnet_tile_radius(finder, mode="finder"), finder.height() / 2.0)
        self.assertAlmostEqual(task.height(), taskbar.height())
        self.assertTrue(magnet_tile_rect(screen, mode="free", bar=finder_bar).isNull())
        owner = _TransitionOwner()
        from local.island_stack_visual import IslandStackVisual
        visual = IslandStackVisual()
        try:
            visual.location_mode = "free"
            visual.dragging = True
            visual._observe_magnet(1.0)
            self.assertTrue(getattr(visual, "taskbar_drop_overlay", None) is None or
                            visual.taskbar_drop_overlay.magnet_local.isNull())
            self.assertEqual(visual._decide_magnet_release(), "pass")
            visual.location_mode = "finder"
            self.assertTrue(visual._magnet_drag_enabled())
            visual.location_mode = "tasker"
            self.assertTrue(visual._magnet_drag_enabled())
            visual.location_mode = "free"
            self.assertFalse(visual._magnet_drag_enabled())
        finally:
            visual.close()
            owner.close()

    def test_flat_plate_has_no_specular_stop(self):
        from local.liquid_glass import paint_flat_liquid_plate
        image = QImage(80, 40, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            paint_flat_liquid_plate(painter, QRectF(4, 4, 72, 32), 16, dark=True, opacity=0.6)
        center = image.pixelColor(40, 20)
        edge = image.pixelColor(8, 8)
        self.assertGreater(center.alpha(), 100)
        self.assertLess(center.red() + center.green() + center.blue(), 40)
        self.assertGreater(edge.alpha(), 0)

    def test_resting_cover_left_inset_matches_the_top_inset(self):
        from local.music_surface_layout import layout_g_targets
        measures = dict(
            title_font_height=28, title_font_pixel_size=24,
            artist_font_height=18, artist_font_pixel_size=16, time_font_height=14,
        )
        for size in ((960, 540), (1920, 1080), (995, 690)):
            target = layout_g_targets(*size, **measures)
            self.assertAlmostEqual(target.cover.x, target.cover.y, delta=0.75, msg=size)
            self.assertGreater(target.cover.x, 18)

    def test_island_shell_matches_9cff078(self):
        from local.island_stack_visual import IslandStackVisual
        from local.taskbar_positioning import MainSizeFacts, main_size_for_kind
        standard = MainSizeFacts(stack_width=400, expanded=True, unit=0.75)
        dual = MainSizeFacts(
            stack_width=400, expanded=True, unit=0.75, music_expanded_layout_style="dual_panel",
        )
        self.assertAlmostEqual(main_size_for_kind("music", standard)[1], 176.0 * 0.75)
        self.assertAlmostEqual(main_size_for_kind("music", dual)[1], 212.0 * 0.75)
        compact = MainSizeFacts(stack_width=400, expanded=False, unit=0.75, compact_music_content_height=46.0)
        self.assertAlmostEqual(main_size_for_kind("music", compact)[1], 46.0)
        visual = IslandStackVisual()
        try:
            self.assertEqual(visual.VISUAL_SCALE, 0.75)
            self.assertEqual(visual.lyric_font_size, 18)
            self.assertEqual(visual._compact_primary_lyric_font_size(), 16)
            visual.lyrics = None
            self.assertEqual(visual._compact_music_content_height_raw(), 46.0)
            cover, _radius = visual._broadcast_indicator_geometry(QRectF(0, 0, 400, 132), expanded=True)
            self.assertAlmostEqual(cover.width(), visual._u(58))
            self.assertAlmostEqual(cover.height(), visual._u(58))
        finally:
            visual.close()

    def test_magnet_uses_real_location_tokens_and_a_high_dpi_finder_bar(self):
        from local.island_stack_visual import IslandStackVisual
        from local.mydockfinder import finder_bar_logical_rect
        from local.settings_store import DEFAULT_SETTINGS, PLACEMENT_MODE_LABELS
        from local.spring_motion import magnet_tile_rect
        self.assertEqual(set(PLACEMENT_MODE_LABELS), {"finder", "tasker", "top", "free"})
        self.assertEqual(DEFAULT_SETTINGS["v2_location_mode"], "top")
        snapshot = {
            "enabled": True,
            "monitor_name": "DISPLAY1",
            "physical_monitor_rect": [0, 0, 3840, 2160],
            "physical_resting_rect": [0, 0, 3840, 96],
            "physical_rect": [0, 0, 3840, 96],
        }
        mapped = finder_bar_logical_rect(
            snapshot, screen_name="DISPLAY1",
            screen_rect=(0, 0, 1920, 1080), device_pixel_ratio=2,
        )
        self.assertEqual(mapped, (0.0, 0.0, 1920.0, 48.0))
        tile = magnet_tile_rect(QRectF(0, 0, 1920, 1080), mode="finder", bar=QRectF(*mapped))
        self.assertAlmostEqual(tile.height(), 48.0)
        self.assertAlmostEqual(tile.top(), 0.0)
        visual = IslandStackVisual()
        try:
            visual.finder_bar_rect = QRectF(*mapped)
            for mode, enabled in (("finder", True), ("tasker", True), ("free", False), ("top", False)):
                visual._magnet_drag_origin = ""
                visual.dragging = False
                visual._magnet_spring_active = False
                visual.finder_mode = mode == "finder"
                visual.location_mode = mode
                self.assertEqual(visual._magnet_drag_enabled(), enabled)
            visual.location_mode = "finder"
            visual.finder_mode = True
            visual.dragging = True
            visual._remember_magnet_drag_origin()
            visual.location_mode = "free"
            visual.finder_mode = False
            self.assertTrue(visual._magnet_drag_enabled())
            self.assertEqual(visual._magnet_mode(), "finder")
            with patch.object(visual, "_observe_magnet") as observe:
                visual._apply_finder_drag_action(QPoint(80, 12), QPoint(10, 4))
            self.assertTrue(observe.called)
        finally:
            visual.close()

    def test_hidden_lottie_still_paints_the_broadcast_cover(self):
        from PySide6.QtGui import QPainter
        owner = _TransitionOwner()
        owner.settings_getter = lambda key, default=None: "face" if key == "v2_broadcast_style" else default
        window = MusicSurfaceWindow(owner)
        try:
            window.resize(640, 360)
            window._dj_activity_from = 1.0
            window._dj_activity_to = 1.0
            window._owned_dj_speaking = True

            class _View:
                def __init__(self, visible):
                    self._visible = visible

                def isVisible(self):
                    return self._visible

            class _Player:
                def __init__(self, visible):
                    self.motion = type("Motion", (), {"idle": False})()
                    self._view = _View(visible)

                def advance(self, *_args, **_kwargs):
                    return 0.8

            image = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
            # The Lottie face is opt-in; this checks that path when it is on.
            self.enterContext(patch("local.broadcast_face_player.lottie_face_enabled", return_value=True))
            window._broadcast_face_player = _Player(False)
            with QPainter(image) as painter, patch(
                "local.music_surface_window.paint_broadcast_indicator",
            ) as paint:
                window._paint_cover_with_broadcast(
                    painter, QRectF(40, 40, 180, 180), 100.0, cache_side=180,
                )
            self.assertTrue(paint.called)
            window._broadcast_face_player = _Player(True)
            with QPainter(image) as painter, patch(
                "local.music_surface_window.paint_broadcast_indicator",
            ) as paint:
                window._paint_cover_with_broadcast(
                    painter, QRectF(40, 40, 180, 180), 100.0, cache_side=180,
                )
            self.assertFalse(paint.called)
        finally:
            window.shutdown()
            owner.close()

    def test_saved_location_mode_roundtrips_and_finder_height_is_the_bar(self):
        from local.island_stack_visual import IslandStackVisual
        from local.island_visual_stack import IslandKind
        from local.settings_store import SettingsManager, normalize_location_mode
        self.assertEqual(normalize_location_mode("taskbar"), "tasker")
        self.assertEqual(normalize_location_mode("nope"), "top")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            settings = SettingsManager(settings_path=path)
            for mode in ("finder", "tasker", "top", "free"):
                settings.set("v2_location_mode", mode)
                settings.save_now()
                restored = SettingsManager(settings_path=path)
                self.assertEqual(restored.get("v2_location_mode"), mode)
                restored._save_timer.stop()
            settings._save_timer.stop()
        visual = IslandStackVisual()
        try:
            visual._mydockfinder_available = lambda: True
            visual.finder_bar_enabled = True
            visual.finder_bar_rect = QRectF(0, 0, 1600, 42)
            visual.finder_mode = True
            visual.location_mode = "finder"
            visual.expanded = False
            visual.lyrics = None
            _width, finder_height = visual._base_main_size_for_kind(IslandKind.MUSIC, 800)
            self.assertAlmostEqual(finder_height, 42.0)
            visual.finder_mode = False
            visual.location_mode = "top"
            _width, top_height = visual._base_main_size_for_kind(IslandKind.MUSIC, 800)
            self.assertAlmostEqual(top_height, visual._u(40.0))
            self.assertNotAlmostEqual(top_height, finder_height)
        finally:
            visual.close()
