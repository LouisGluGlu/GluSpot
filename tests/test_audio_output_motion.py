from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from local.audio_output_motion import OutputConnectionMotion, paint_connection_pose
from local.audio_output_status import ConnectedOutputStatus, OutputMember
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_lyric_visual_layers import _LayerOwner


OLD = OutputMember("buds", "AirPods Pro", "airpods-pro")
MINI = OutputMember("mini", "HomePod Mini", "homepod-mini")
POD = OutputMember("pod", "HomePod", "homepod")


class OutputConnectionMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if not QFontDatabase.families():
            QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")

    def setUp(self):
        self.engine = OutputConnectionMotion()
        self.before = ConnectedOutputStatus((OLD,))
        self.after = ConnectedOutputStatus((OLD, MINI))
        self.engine.settle(self.before, 0)
        self.engine.announce(MINI, self.before, self.after, 0)
        self.now = 0.0

    def _advance_to(self, seconds, hz=120):
        while self.now < seconds:
            self.now = min(seconds, self.now + 1 / hz)
            self.engine.advance(self.now)

    def test_growth_color_bounce_name_and_return_follow_reference_order(self):
        self._advance_to(.08)
        self.assertGreater(self.engine.pose.old_scale, .5)
        self.assertEqual(self.engine.pose.blue, 0)
        self._advance_to(.30)
        self.assertEqual(self.engine.pose.old_opacity, 0)
        self.assertGreater(self.engine.pose.new_scale, .06)
        self.assertLess(self.engine.pose.blue, 1)
        self._advance_to(.66)
        self.assertGreater(self.engine.pose.new_scale, 1)
        self.assertLessEqual(self.engine.pose.new_scale * .72, .82)
        self._advance_to(.95)
        self.assertAlmostEqual(self.engine.pose.new_scale, 1)
        self._advance_to(1.60)
        self.assertEqual(self.engine.pose.capsule, 1)
        self._advance_to(3.12)
        self.assertEqual(self.engine.pose.capsule, 0)
        self.assertGreater(self.engine.pose.final_opacity, 0)
        self._advance_to(3.50)
        self.assertFalse(self.engine.active)
        self.assertEqual(self.engine.status, self.after)

    def test_delayed_and_backward_ticks_never_skip_the_sequence(self):
        self._advance_to(.2)
        elapsed = self.engine._elapsed
        self.engine.advance(30)
        self.assertLess(self.engine._elapsed - elapsed, .04)
        self.assertTrue(self.engine.active)
        elapsed = self.engine._elapsed
        self.engine.advance(10)
        self.assertEqual(self.engine._elapsed, elapsed)

    def test_new_confirmations_queue_without_restarting_current_pose(self):
        self._advance_to(.6)
        before = self.engine.pose
        final = ConnectedOutputStatus((OLD, MINI, POD))
        self.engine.announce(POD, self.after, final, self.now)
        self.assertEqual(self.engine.pose, before)
        self.assertFalse(self.engine.announce(POD, self.after, final, self.now))
        self._advance_to(3.5)
        self.assertEqual(self.engine.pose.device, POD)
        self.assertEqual(self.engine.pose.previous, self.after)
        self.assertEqual(self.engine.pose.target, final)
        self._advance_to(7)
        self.assertFalse(self.engine.active)
        self.assertEqual(self.engine.status.count, 3)

    def test_removal_reconciles_from_current_pose_then_settles(self):
        self._advance_to(1.7)
        previous = self.engine.pose
        self.engine.reconcile(self.before, self.now, removed=(MINI.id,))
        self.assertEqual(self.engine.pose, previous)
        self._advance_to(1.85)
        self.assertLess(self.engine.pose.capsule, previous.capsule)
        self.assertGreater(self.engine.pose.final_opacity, previous.final_opacity)
        self._advance_to(2.2)
        self.assertFalse(self.engine.active)
        self.assertEqual(self.engine.status, self.before)

    def test_paint_does_not_advance_clock_and_stays_inside_capsule(self):
        for instant in (.08, .3, .66, 1.6, 2.7, 3.12):
            self._advance_to(instant)
            pose = self.engine.pose
            image = QImage(400, 90, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                paint_connection_pose(painter, QRectF(320, 15, 60, 60), pose, QFont("Segoe UI", 10))
            self.assertEqual(self.engine.pose, pose)
            self.assertTrue(all(image.pixelColor(x, y).alpha() == 0 for y in (0, 89) for x in range(400)))
            self.assertTrue(all(image.pixelColor(x, y).alpha() == 0 for x in (0, 399) for y in range(90)))

    def test_paint_multiplies_caller_opacity_without_mutating_it(self):
        self._advance_to(.7)
        image = QImage(80, 80, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            painter.setOpacity(.2)
            paint_connection_pose(painter, QRectF(10, 10, 60, 60), self.engine.pose, QFont())
            self.assertEqual(painter.opacity(), .2)
        # Face and glyph are separate source-over layers; neither may paint
        # at full opacity when the enclosing control strip is fading.
        self.assertLessEqual(max(image.pixelColor(x, y).alpha() for y in range(80) for x in range(80)), 100)

    def test_removed_destination_keeps_old_glyph_target_during_fade(self):
        self._advance_to(3.1)
        self.engine.reconcile(self.before, self.now, removed=(MINI.id,))
        self._advance_to(3.12)
        self.assertEqual(self.engine.pose.target, self.before)
        self.assertEqual(self.engine.pose.glyph_target, self.after)


class MusicSurfaceConnectionMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _LayerOwner()
        self.devices = (AudioRouteDevice("buds", "AirPods Pro", "airpods-pro", is_bluetooth=True),
                        AudioRouteDevice("mini", "HomePod Mini", "homepod-mini", "airplay"),
                        AudioRouteDevice("pod", "HomePod", "homepod", "airplay"))
        self.snapshot = AudioRouteSnapshot(self.devices, active_id="buds", state="ready")
        self.owner.data_provider = SimpleNamespace(get_audio_route_snapshot=lambda: self.snapshot)
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(1190, 666)
        self.window.show()
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def test_confirmed_addition_animates_once_pending_and_volume_never_restart(self):
        self.snapshot = replace(self.snapshot, state="connecting", pending_ids=("buds", "mini"))
        self.window._sync_audio_output_status()
        self.assertFalse(self.window._audio_output_motion.active)
        self.assertEqual(self.window._audio_output_kinds, ("airpods-pro",))
        self.snapshot = replace(self.snapshot, state="casting", pending_ids=(), member_ids=("buds", "mini"))
        self.window._sync_audio_output_status()
        pose = self.window._audio_output_motion.pose
        self.assertEqual(pose.device.id, "mini")
        self.snapshot = replace(self.snapshot, revision=10, devices=tuple(replace(d, volume=.3) for d in self.devices))
        self.window._sync_audio_output_status()
        self.assertEqual(self.window._audio_output_motion.pose, pose)

    def test_paint_uses_current_pose_without_provider_reads_and_keeps_neighbors_fixed(self):
        self.snapshot = replace(self.snapshot, state="casting", member_ids=("buds", "mini"))
        self.window._sync_audio_output_status()
        began = self.window._audio_output_motion._clock.last_at
        for step in range(100):
            self.window._advance_audio_output_motion(began + (step + 1) / 60)
        self.window._reset_control_idle(100)
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch.object(self.owner.data_provider, "get_audio_route_snapshot", side_effect=AssertionError("paint I/O")), \
                QPainter(image) as painter, patch("local.music_surface_window.time.monotonic", return_value=100):
            self.window._paint_window_controls(painter, QRectF(self.window.rect()))
        mode = self.window._window_control_base_rect(QRectF(self.window.rect()))
        audio = self.window._audio_output_button_rect(mode, self.window._surface_scale())
        self.assertEqual(self.window._button_rects["return"], mode)
        self.assertEqual(self.window._button_rects["audio_output"].right(), audio.right())
        self.assertGreater(self.window._button_rects["audio_output"].width(), audio.width())
        count = len(self.window._control_layer_cache)
        self.window._advance_audio_output_motion(began + 1.7)
        with QPainter(image) as painter, patch("local.music_surface_window.time.monotonic", return_value=100):
            self.window._paint_window_controls(painter, QRectF(self.window.rect()))
        self.assertEqual(len(self.window._control_layer_cache), count)

    def test_available_arrival_uses_animation_without_claiming_playback_count(self):
        arrival = replace(self.snapshot, event_serial=1, event_device_id="mini", event_name="HomePod Mini")
        self.window.on_audio_device_event(arrival, self.devices[1])
        self.assertEqual(self.window._audio_output_motion.pose.device.id, "mini")
        self.assertEqual(self.window._audio_output_motion.pose.target.count, 1)
        self.assertEqual(self.window._audio_output_kinds, ("airpods-pro",))
        self.window.on_audio_device_event(arrival, self.devices[1])
        self.assertEqual(len(self.window._audio_output_motion._queue), 0)

    def test_hide_settles_motion_and_does_not_replay_on_show(self):
        self.snapshot = replace(self.snapshot, state="casting", member_ids=("buds", "mini"))
        self.window._sync_audio_output_status()
        self.assertTrue(self.window._audio_output_motion.active)
        self.window.hide()
        self.assertFalse(self.window._audio_output_motion.active)
        self.window.show()
        self.assertFalse(self.window._audio_output_motion.active)
        self.assertEqual(self.window._audio_output_kinds, ("airpods-pro", "homepod-mini"))

    def test_hidden_route_change_is_baselined_on_direct_show(self):
        self.window.hide()
        self.snapshot = replace(self.snapshot, state="casting", member_ids=("mini", "pod"))
        self.window.show()
        self.assertEqual(self.window._audio_output_kinds, ("homepod-mini", "homepod"))
        self.assertFalse(self.window._audio_output_motion.active)

    def test_bitmap_transition_pauses_instead_of_consuming_motion(self):
        self.snapshot = replace(self.snapshot, state="casting", member_ids=("buds", "mini"))
        self.window._sync_audio_output_status()
        pose = self.window._audio_output_motion.pose
        began = self.window._audio_output_motion._clock.last_at
        self.window._geometry_transition_snapshot = QImage(10, 10, QImage.Format.Format_RGB32)
        self.window._advance_audio_output_motion(began + 4)
        self.assertEqual(self.window._audio_output_motion.pose, pose)
        self.window._geometry_transition_snapshot = QImage()
        self.window._advance_audio_output_motion(began + 4.01)
        self.assertTrue(self.window._audio_output_motion.active)
        self.assertLess(self.window._audio_output_motion._elapsed, .04)

    def test_confirmed_reconnection_within_four_seconds_is_not_discarded(self):
        self.snapshot = replace(self.snapshot, state="casting", active_id="mini")
        self.window._sync_audio_output_status()
        self.snapshot = replace(self.snapshot, state="ready", active_id="buds")
        self.window._sync_audio_output_status()
        self.snapshot = replace(self.snapshot, state="casting", active_id="mini")
        self.window._sync_audio_output_status()
        identities = [self.window._audio_output_motion.pose.device.id] + [
            item[0].id for item in self.window._audio_output_motion._queue]
        self.assertIn("mini", identities)

    def test_headset_arrival_uses_same_normalized_kind_as_status(self):
        device = replace(self.devices[0], kind="headset")
        self.window.on_audio_device_event(replace(self.snapshot, event_serial=1), device)
        self.assertEqual(self.window._audio_output_motion.pose.device.kind, "headphones")


if __name__ == "__main__":
    unittest.main()
