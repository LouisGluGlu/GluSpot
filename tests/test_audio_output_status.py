from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from local.audio_output_status import OutputMembershipTracker, connected_output_kinds
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
    def _group(self, *identities, devices=DEVICES, **changes):
        snapshot = AudioRouteSnapshot(devices, active_id="source", state="casting",
                                      member_ids=identities, cast_source_id="source")
        return replace(snapshot, **changes) if changes else snapshot

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

    def test_tracker_baseline_does_not_replay_a_connection(self):
        tracker = OutputMembershipTracker()
        first = tracker.update(self._group("mini", "pod"))
        self.assertTrue(first.baseline)
        self.assertEqual(first.added, ())
        self.assertEqual(first.current.count, 2)
        self.assertEqual(first.current.representatives, ("homepod-mini", "homepod"))
        tracker.reset()
        again = tracker.update(self._group("buds"))
        self.assertTrue(again.baseline)
        self.assertEqual(again.added, ())

    def test_same_kind_before_second_kind_keeps_first_connection_slots(self):
        devices = DEVICES + (AudioRouteDevice("mini2", "Mini 2", kind="homepod-mini", transport="airplay"),)
        tracker = OutputMembershipTracker()
        tracker.update(self._group("mini", devices=devices))
        second = tracker.update(self._group("mini2", "mini", devices=devices))
        self.assertEqual(tuple(member.id for member in second.current.members), ("mini", "mini2"))
        self.assertEqual(second.current.representatives, ("homepod-mini",))
        self.assertEqual(tuple(member.id for member in second.added), ("mini2",))
        third = tracker.update(self._group("pod", "mini2", "mini", devices=devices))
        self.assertEqual(tuple(member.id for member in third.current.members), ("mini", "mini2", "pod"))
        self.assertEqual(third.current.representatives, ("homepod-mini", "homepod"))
        self.assertEqual(third.current.count, 3)
        self.assertEqual(tuple(member.id for member in third.added), ("pod",))

    def test_reverse_homepod_order_and_later_kinds_do_not_replace_first_pair(self):
        tracker = OutputMembershipTracker()
        tracker.update(self._group("pod"))
        second = tracker.update(self._group("mini", "pod"))
        self.assertEqual(second.current.representatives, ("homepod", "homepod-mini"))
        third = tracker.update(self._group("buds", "mini", "pod"))
        self.assertEqual(third.current.representatives, ("homepod", "homepod-mini"))
        self.assertEqual(third.current.count, 3)
        self.assertEqual(tuple(member.id for member in third.added), ("buds",))

    def test_earbuds_then_homepod_keeps_side_by_side_pair_after_mini_arrives(self):
        tracker = OutputMembershipTracker()
        tracker.update(self._group("buds"))
        tracker.update(self._group("pod", "buds"))
        third = tracker.update(self._group("mini", "pod", "buds"))
        self.assertEqual(third.current.representatives, ("airpods-pro", "homepod"))
        self.assertEqual(tuple(member.id for member in third.current.members), ("buds", "pod", "mini"))
        self.assertEqual(third.current.count, 3)

    def test_reorder_and_volume_updates_do_not_report_new_connections(self):
        tracker = OutputMembershipTracker()
        tracker.update(self._group("buds", "mini", "m6"))
        changed_volume = tuple(replace(device, volume=.35) for device in reversed(DEVICES))
        update = tracker.update(self._group("m6", "mini", "buds", devices=changed_volume, revision=8))
        self.assertFalse(update.changed)
        self.assertEqual(update.added, ())
        self.assertEqual(update.removed, ())
        self.assertEqual(update.current.representatives, ("airpods-pro", "homepod-mini"))

    def test_pending_sender_restart_holds_only_the_previous_confirmed_members(self):
        tracker = OutputMembershipTracker()
        tracker.update(self._group("mini", "m6"))
        for state in ("waiting_source", "connecting", "waiting_audio"):
            pending = AudioRouteSnapshot(DEVICES, active_id="source", state=state,
                pending_id="pod", pending_ids=("mini", "m6", "pod"), cast_source_id="source")
            update = tracker.update(pending)
            self.assertTrue(update.held_pending)
            self.assertFalse(update.changed)
            self.assertEqual(update.current.count, 2)
            self.assertEqual(update.added, ())
        confirmed = tracker.update(self._group("pod", "m6", "mini"))
        self.assertFalse(confirmed.held_pending)
        self.assertEqual(confirmed.current.representatives, ("homepod-mini", "speaker"))
        self.assertEqual(tuple(member.id for member in confirmed.added), ("pod",))
        self.assertEqual(confirmed.current.count, 3)

    def test_initial_pending_is_not_a_connected_device(self):
        tracker = OutputMembershipTracker()
        pending = tracker.update(AudioRouteSnapshot(DEVICES, active_id="source", pending_id="mini",
                                                    state="connecting", cast_source_id="source"))
        self.assertEqual(pending.current.count, 0)
        self.assertEqual(pending.added, ())
        confirmed = tracker.update(self._group("mini"))
        self.assertEqual(tuple(member.id for member in confirmed.added), ("mini",))

    def test_removals_rederive_earliest_remaining_distinct_kinds(self):
        tracker = OutputMembershipTracker()
        tracker.update(self._group("buds", "pod", "mini", "m6"))
        update = tracker.update(self._group("m6", "mini", "pod"))
        self.assertEqual(tuple(member.id for member in update.current.members), ("pod", "mini", "m6"))
        self.assertEqual(update.current.representatives, ("homepod", "homepod-mini"))
        self.assertEqual(tuple(member.id for member in update.removed), ("buds",))
        self.assertEqual(update.added, ())
        rejoined = tracker.update(self._group("buds", "m6", "mini", "pod"))
        self.assertEqual(tuple(member.id for member in rejoined.current.members), ("pod", "mini", "m6", "buds"))
        self.assertEqual(rejoined.current.representatives, ("homepod", "homepod-mini"))

    def test_failure_clears_held_members_and_recovered_route_is_new_confirmation(self):
        tracker = OutputMembershipTracker()
        tracker.update(self._group("mini", "pod"))
        failed = tracker.update(self._group("mini", "pod", state="error", pending_ids=("mini", "pod")))
        self.assertEqual(failed.current.count, 0)
        self.assertFalse(failed.held_pending)
        self.assertEqual(tuple(member.id for member in failed.removed), ("mini", "pod"))
        recovered = tracker.update(AudioRouteSnapshot(DEVICES, active_id="buds", state="ready"))
        self.assertEqual(recovered.current.representatives, ("airpods-pro",))
        self.assertEqual(tuple(member.id for member in recovered.added), ("buds",))
        self.assertEqual(tracker.update(None).current.count, 0)

    def test_discovery_absence_preserves_confirmed_metadata_without_name_guessing(self):
        tracker = OutputMembershipTracker()
        tracker.update(self._group("mini"))
        absent = tracker.update(self._group("mini", devices=()))
        self.assertEqual(absent.current.representatives, ("homepod-mini",))
        self.assertEqual(absent.current.members[0].name, "Living room")
        unknown = tracker.update(self._group("mini", "unknown", devices=()))
        self.assertEqual(unknown.added[0].name, "未识别设备")
        self.assertEqual(unknown.current.representatives, ("homepod-mini", "device"))
        misleading = DEVICES + (AudioRouteDevice("unknown", "My HomePod", kind="anything"),)
        self.assertEqual(tracker.update(self._group("mini", "unknown", devices=misleading)).current.representatives,
                         ("homepod-mini", "device"))

    def test_aliases_do_not_duplicate_a_representative(self):
        aliases = (AudioRouteDevice("a", "A", kind="headset"), AudioRouteDevice("b", "B", kind="headphones"),
                   AudioRouteDevice("c", "C", kind="homepodmini", transport="airplay"))
        update = OutputMembershipTracker().update(self._group("a", "b", "c", devices=aliases))
        self.assertEqual(update.current.representatives, ("headphones", "homepod-mini"))
        self.assertEqual(update.current.count, 3)


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
        self.assertLess(update.call_args.args[0].width(), self.window.width() // 2)
        self.assertLess(update.call_args.args[0].height(), self.window.height() // 4)
        self.getter.side_effect = AssertionError("provider read during paint")
        image = self._controls()
        circle = self.window._button_rects["audio_output"].toAlignedRect()
        face = image.pixelColor(circle.center().x(), circle.top() + 3)
        self.assertGreater(face.alpha(), 0)
        self.assertLess(face.alpha(), 245)
        # Connected glyph changes the compositor key, avoiding the old cached
        # link or single-device image when group membership changes.
        self.window._audio_output_kinds = ("homepod-mini", "homepod")
        grouped = self._controls()
        single_crop, grouped_crop = image.copy(circle), grouped.copy(circle)
        self.assertNotEqual(bytes(single_crop.constBits()), bytes(grouped_crop.constBits()))

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
        with patch.object(self.window, "render", side_effect=lambda _frame, *args, **kwargs: observed.append(
                self.window._audio_output_kinds)):
            self.window.show_from_island(fullscreen=False, origin_geometry=QRect(20, 20, 200, 100))
        self.assertEqual(observed, [("homepod-mini",)])


if __name__ == "__main__":
    unittest.main()
