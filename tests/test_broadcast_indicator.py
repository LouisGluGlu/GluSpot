"""Cover-slot broadcast indicator: state map, geometry, and the status light."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from local.broadcast_indicator import (
    broadcast_expression, broadcast_indicator_rect, broadcast_motion,
    broadcast_slot_visible, broadcast_visual_state, face_eye_heights,
    normalize_broadcast_style, paint_broadcast_indicator,
)
from local.music_surface_window import MusicSurfaceWindow
from local.personal_dj_icon import (
    personal_dj_status_badge, personal_dj_status_badge_inside,
    personal_dj_status_dot_diameter, personal_dj_legacy_status_dot_diameter,
)
from local.settings_store import SettingsManager, normalize_broadcast_style as normalize_setting
from tests import test_island_lyric_follow_geometry as island_fixtures
from tests.test_owned_playback_surface import _Provider


class BroadcastStateTests(unittest.TestCase):
    def test_states_map_to_expressions_and_only_speaking_uses_the_rings_energy(self):
        cases = {
            "preparing": ("attentive", True, False),
            "speaking": ("talking", True, True),
            "paused": ("squint", False, False),
            "ended": ("happy", True, False),
            "idle": ("idle", False, False),
        }
        for state, (expression, animate, bands) in cases.items():
            motion = broadcast_motion(state)
            self.assertEqual(motion.expression, expression, state)
            self.assertEqual(motion.animate, animate, state)
            self.assertEqual(motion.use_bands, bands, state)
        self.assertEqual(broadcast_expression("ended")["shape"], "arc")
        self.assertEqual(broadcast_expression("paused")["shape"], "capsule")
        # Speech over ducked music is still speech: the indicator keeps moving.
        self.assertEqual(broadcast_visual_state(speaking=True, cover_paused=True), "speaking")
        self.assertEqual(broadcast_visual_state(speaking=True), "speaking")
        self.assertEqual(broadcast_visual_state(presentation_state="loading"), "preparing")
        self.assertEqual(broadcast_visual_state(introduction_pending=True), "preparing")
        self.assertEqual(broadcast_visual_state(speaking=False, slot=0.4), "ended")
        self.assertEqual(broadcast_visual_state(presentation_state="failed"), "ended")
        self.assertEqual(broadcast_visual_state(), "idle")
        self.assertFalse(broadcast_slot_visible(0.0))
        self.assertTrue(broadcast_slot_visible(0.2))

    def test_face_expressions_change_height_and_direction(self):
        talking = face_eye_heights("speaking", 2.0)
        squint = face_eye_heights("paused", 2.0)
        happy = broadcast_expression("ended")
        attentive = broadcast_expression("preparing")
        self.assertGreater(min(talking), max(squint))
        self.assertEqual(happy["shape"], "arc")
        self.assertLess(attentive["gaze"][0], 0.0)
        self.assertGreater(broadcast_expression("speaking")["gaze"][0], 0.0)
        self.assertLess(broadcast_expression("paused")["gaze"][1], 0.0)
        self.assertGreater(happy["gaze"][1], 0.0)

    def test_unknown_style_stays_on_the_scrolling_rings(self):
        self.assertEqual(normalize_broadcast_style(None), "rings")
        self.assertEqual(normalize_broadcast_style("nope"), "rings")
        self.assertEqual(normalize_broadcast_style("样式二"), "face")
        self.assertEqual(normalize_setting("face"), "face")

    def test_hidden_slot_does_not_paint(self):
        image = QImage(40, 40, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        called = []
        with QPainter(image) as painter:
            paint_broadcast_indicator(
                painter, QRectF(4, 4, 32, 32), 2.0, style="rings", state="speaking",
                elapsed=1.0, color=QColor("#ffffff"),
                ring_painter=lambda *_args, **_kwargs: called.append(True), slot=0.0,
            )
        self.assertEqual(called, [])
        self.assertEqual(image.pixelColor(20, 20).alpha(), 0)


class BroadcastSlotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.provider = _Provider()
        self.provider.fm = True
        self.provider.get_owned_personal_dj_enabled = lambda: True
        self.provider.is_owned_personal_dj_speaking = lambda: False
        self.provider.get_personal_dj_presentation = lambda: {}
        self.owner.data_provider = self.provider
        self.owner.album_color = QColor("#9a426b")
        self.owner.secondary_color = QColor("#38233a")
        self.owner.album_art = QImage()
        self.window = MusicSurfaceWindow(self.owner)
        self.window._timer.stop()

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def test_indicator_rect_matches_the_cover_in_wide_and_narrow(self):
        saw_narrow = saw_wide = False
        for size in ((360, 640), (420, 720), (960, 540), (1280, 720)):
            self.window.resize(*size)
            now = 100.0
            with patch("local.music_surface_window.time.monotonic", return_value=now):
                cover = self.window._scaled_cover_rect(
                    self.window._current_cover_rect(now), self.window._cover_scale_at(now))
                indicator = self.window._broadcast_indicator_rect(now)
            self.assertEqual(indicator, broadcast_indicator_rect(cover))
            self.assertEqual(indicator, cover)
            self.assertAlmostEqual(
                self.window._broadcast_indicator_radius(cover),
                self.window._album_art_radius(cover.width()))
            self.assertGreater(cover.width(), 1.0)
            if self.window._content_layout_is_narrow():
                saw_narrow = True
            else:
                saw_wide = True
        self.assertTrue(saw_narrow)
        self.assertTrue(saw_wide)

    def test_island_indicator_matches_the_cover_slot(self):
        visual = island_fixtures.IslandLyricFollowGeometryTests._visual()
        for height in (48.0, 72.0, 112.0):
            shell = QRectF(0.0, 0.0, 440.0, height)
            cover, radius = visual._island_cover_geometry(shell)
            indicator, indicator_radius = visual._broadcast_indicator_geometry(shell, expanded=False)
            self.assertEqual(indicator, QRectF(cover))
            self.assertEqual(indicator_radius, radius)
        shell = QRectF(0.0, 0.0, 580.0, 240.0)
        indicator, indicator_radius = visual._broadcast_indicator_geometry(shell, expanded=True)
        cover = QRectF(shell.left() + visual._u(16), shell.top() + visual._u(14), visual._u(58), visual._u(58))
        self.assertEqual(indicator, cover)
        self.assertEqual(indicator_radius, visual._u(17))


class StatusLightGeometryTests(unittest.TestCase):
    def test_light_bounding_rect_stays_inside_every_button_size(self):
        for side in (8.0, 12.0, 16.5, 24.0, 32.0, 35.0, 48.0, 64.0, 96.0, 128.0):
            button = QRectF(10.0, 20.0, side, side)
            badge = personal_dj_status_badge(button)
            self.assertTrue(personal_dj_status_badge_inside(button, badge), side)
            radius = side / 2.0
            center = button.center()
            farthest = 0.0
            for corner in (badge.topLeft(), badge.topRight(), badge.bottomLeft(), badge.bottomRight()):
                farthest = max(farthest, math.hypot(corner.x() - center.x(), corner.y() - center.y()))
            self.assertLessEqual(farthest, radius + 1e-6, side)
            self.assertGreater(badge.center().x(), center.x(), side)
            self.assertGreater(badge.center().y(), center.y(), side)
            requested = personal_dj_status_dot_diameter(side)
            self.assertLessEqual(badge.width(), requested + 1e-6, side)
            self.assertAlmostEqual(requested, personal_dj_legacy_status_dot_diameter(side) * 0.60)
            if side >= 32.0:
                self.assertAlmostEqual(badge.width(), requested)


class BroadcastSettingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_setting_defaults_to_rings_and_rejects_unknown_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with patch("local.settings_store.SETTINGS_FILE", str(path)), \
                    patch("local.settings_store.APP_DATA_DIR", directory), \
                    patch("local.settings_store.legacy_app_data_dirs", return_value=()):
                manager = SettingsManager()
                self.assertEqual(manager.get("v2_broadcast_style"), "rings")
                manager.set("v2_broadcast_style", "face")
                self.assertEqual(manager.get("v2_broadcast_style"), "face")
                manager.set("v2_broadcast_style", "样式三")
                self.assertEqual(manager.get("v2_broadcast_style"), "rings")
                manager.save_now()
                path.write_text('{"v2_broadcast_style": "face"}', encoding="utf-8")
                restored = SettingsManager()
                self.assertEqual(restored.get("v2_broadcast_style"), "face")


if __name__ == "__main__":
    unittest.main()
