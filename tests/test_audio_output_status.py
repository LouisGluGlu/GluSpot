from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from local.audio_output_status import connected_output_kinds
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_lyric_visual_layers import _LayerOwner


DEVICES = (
    AudioRouteDevice("source", "Capture", kind="speaker"),
    AudioRouteDevice("m6", "M6", kind="speaker", is_bluetooth=True),
    AudioRouteDevice("buds", "AirPods Pro", kind="airpods-pro", is_bluetooth=True),
    AudioRouteDevice("mini", "Living room", kind="homepod-mini", transport="airplay"),
    AudioRouteDevice("pod", "Bedroom", kind="homepod", transport="airplay"),
    AudioRouteDevice("express", "Express", kind="airplay", transport="airplay"),
)


class ConnectedOutputStatusTests(unittest.TestCase):
    def test_single_local_uses_explicit_device_kind(self):
        for identity, kind in (("buds", "airpods-pro"), ("m6", "speaker")):
            with self.subTest(identity=identity):
                snapshot = AudioRouteSnapshot(DEVICES, active_id=identity, state="ready")
                self.assertEqual(connected_output_kinds(snapshot), (kind,))

    def test_connection_targets_never_claim_connected_playback(self):
        for state in ("connecting", "switching", "waiting_source", "waiting_audio"):
            with self.subTest(state=state):
                snapshot = AudioRouteSnapshot(DEVICES, active_id="source", pending_id="mini",
                    pending_ids=("mini", "m6"), state=state, cast_source_id="source")
                self.assertEqual(connected_output_kinds(snapshot), ())

    def test_confirmed_group_counts_only_unique_playback_members(self):
        snapshot = AudioRouteSnapshot(DEVICES, active_id="mini", state="casting",
            member_ids=("pod", "mini", "source", "m6", "mini", ""), cast_source_id="source")
        self.assertEqual(connected_output_kinds(snapshot), ("homepod", "homepod-mini", "speaker"))
        self.assertEqual(connected_output_kinds(replace(snapshot, state="waiting_audio")),
                         ("homepod", "homepod-mini", "speaker"))

    def test_capture_default_does_not_become_an_extra_group_member(self):
        snapshot = AudioRouteSnapshot(DEVICES, active_id="source", state="casting",
            member_ids=("mini", "express"), cast_source_id="source")
        self.assertEqual(connected_output_kinds(snapshot), ("homepod-mini", "airplay"))

    def test_explicit_network_identity_survives_discovery_absence(self):
        devices = tuple(replace(device, available=False) if device.id == "mini" else device
                        for device in DEVICES)
        snapshot = AudioRouteSnapshot(devices, active_id="mini", state="casting")
        self.assertEqual(connected_output_kinds(snapshot), ("homepod-mini",))
        self.assertEqual(connected_output_kinds(replace(snapshot, member_ids=("missing", "mini"))),
                         ("device", "homepod-mini"))

    def test_errors_and_stopped_network_routes_do_not_retain_old_badges(self):
        snapshot = AudioRouteSnapshot(DEVICES, active_id="mini", state="error", member_ids=("mini", "pod"))
        self.assertEqual(connected_output_kinds(snapshot), ())
        self.assertEqual(connected_output_kinds(replace(snapshot, state="ready")), ())
        self.assertEqual(connected_output_kinds(None), ())


class MusicSurfaceOutputStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _LayerOwner()
        self.getter = Mock(return_value=AudioRouteSnapshot(DEVICES, active_id="mini", state="casting"))
        self.owner.data_provider = SimpleNamespace(get_audio_route_snapshot=self.getter)
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(1190, 666)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _controls(self):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.window._reset_control_idle(100.0)
        with QPainter(image) as painter, patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window._paint_window_controls(painter, QRectF(self.window.rect()))
        return image

    def test_sync_changes_only_control_region_and_paint_reuses_snapshot(self):
        with patch.object(self.window, "update") as update:
            self.window._sync_audio_output_status()
        self.assertEqual(self.window._audio_output_kinds, ("homepod-mini",))
        self.assertEqual(update.call_count, 1)
        self.assertLess(update.call_args.args[0].width(), self.window.width() // 4)
        self.getter.side_effect = AssertionError("provider read during paint")
        image = self._controls()
        circle = self.window._button_rects["audio_output"].toAlignedRect()
        self.assertGreater(image.pixelColor(circle.center().x(), circle.top() + 3).red(), 200)
        # Connected glyph changes the compositor key, avoiding the old cached
        # link or single-device image when group membership changes.
        self.window._audio_output_kinds = ("homepod-mini", "homepod")
        grouped = self._controls()
        self.assertNotEqual(bytes(image.copy(circle).constBits()), bytes(grouped.copy(circle).constBits()))

    def test_unchanged_kind_or_volume_does_not_repaint(self):
        self.window._sync_audio_output_status()
        self.getter.return_value = AudioRouteSnapshot(
            tuple(replace(device, volume=.7) for device in DEVICES), active_id="mini", state="casting", revision=5)
        with patch.object(self.window, "update") as update:
            self.window._sync_audio_output_status()
        update.assert_not_called()

    def test_provider_failure_clears_connected_status(self):
        self.window._sync_audio_output_status()
        self.getter.side_effect = RuntimeError("closed provider")
        self.window._sync_audio_output_status()
        self.assertEqual(self.window._audio_output_kinds, ())

    def test_status_timer_follows_surface_visibility(self):
        self.assertFalse(self.window._audio_output_status_timer.isActive())
        self.window.show()
        self.assertTrue(self.window._audio_output_status_timer.isActive())
        self.assertEqual(self.window._audio_output_kinds, ("homepod-mini",))
        self.window.hide()
        self.assertFalse(self.window._audio_output_status_timer.isActive())
        self.getter.return_value = AudioRouteSnapshot(DEVICES, active_id="buds", state="ready")
        self.window.show()
        self.assertEqual(self.window._audio_output_kinds, ("airpods-pro",))
        self.window.shutdown()
        self.assertFalse(self.window._audio_output_status_timer.isActive())

    def test_explicit_reopen_reconnects_status_polling(self):
        self.window.show_from_island(fullscreen=False)
        self.window.shutdown()
        self.window.show_from_island(fullscreen=False)
        self.getter.return_value = AudioRouteSnapshot(DEVICES, active_id="buds", state="ready")
        self.window._audio_output_status_timer.timeout.emit()
        self.assertEqual(self.window._audio_output_kinds, ("airpods-pro",))

    def test_entry_bitmap_uses_current_hidden_route(self):
        self.window._audio_output_kinds = ("speaker",)
        observed = []
        with patch.object(self.window, "render", side_effect=lambda _frame: observed.append(
                self.window._audio_output_kinds)):
            self.window.show_from_island(fullscreen=False, origin_geometry=QRect(20, 20, 200, 100))
        self.assertEqual(observed, [("homepod-mini",)])


if __name__ == "__main__":
    unittest.main()
