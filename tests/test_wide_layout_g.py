"""Wide layout G: centered column, glass transport card, and the 220/320/420 ms handoff."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QImage
from PySide6.QtWidgets import QApplication

from _demos.narrow3.narrow_window import NarrowStudyWindow
from local.music_surface_layout import wide_narrow_easing
from local.music_width_transition import handoff_seconds
from tests.test_music_surface_transition import _TransitionOwner
from tests.test_owned_playback_surface import _Provider


class WideLayoutGTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.owner.data_provider = _Provider()
        self.owner._music_library_back_callback = Mock()
        self.owner.media_duration_seconds = 192.0
        self.owner.media_position_seconds = 73.0
        self.owner.track_title = "What Would U Do"
        self.owner.artist = "Haley Smalls"
        self.window = NarrowStudyWindow(self.owner, "B", "left", live=True)
        self.window._timer.stop()
        self.window.study_timer.stop()
        self.window._audio_output_status_timer.stop()
        self.window.set_content_layout("lyrics", animate=False)
        self.window._finish_content_layout()
        self.window._song_collection_state = lambda: SimpleNamespace(
            track_id="owned:netease:1", available=True, busy=False, liked=False, message="",
        )
        self.seeked = []
        self.owner.data_provider.seek_playback_position = lambda seconds, expected_track_id=None: self.seeked.append(seconds) or True

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.owner.close()

    def pose(self, size):
        self.window._explicit_width_target = (*size, False)
        self.window.resize(*size)
        self.window._sync_narrow_mode()
        self.window._finish_width_chrome()
        self.window._finish_width_layout()
        self.window.narrow = False
        self.window._width_layout_fraction = 0.0
        self.window._content_layout_fit_narrow = False
        self.window._control_reveal_from = self.window._control_reveal_target = 1.0

    def test_left_group_is_vertically_centered_and_lyrics_share_its_axis(self):
        for size in ((995, 690), (715, 540), (440, 480)):
            self.pose(size)
            surface = QRectF(self.window.rect())
            metrics = self.window._wide_layout_g(surface)
            unit = min(size[0] / 100.0, size[1] / 75.8)
            with self.subTest(size=size):
                self.assertAlmostEqual(metrics.unit, unit)
                self.assertAlmostEqual(metrics.cover.width(), min(unit * 36.5, size[0] * 0.40, size[1] * 0.44), delta=0.6)
                self.assertAlmostEqual(metrics.axis, (metrics.cover.top() + metrics.card.bottom()) / 2.0, delta=1.0)
                self.assertLess(metrics.track.bottom(), metrics.time_row.top() + 0.5)
                self.assertLess(metrics.time_row.bottom(), metrics.buttons.top() + 0.5)
                self.assertTrue(metrics.card.contains(metrics.track))
                self.assertTrue(metrics.card.contains(metrics.buttons))
                self.assertEqual(self.window._metadata_title_font(20).weight(), QFont.Weight.Bold)
                frame = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
                self.window.render(frame)
                self.assertAlmostEqual(self.window._lyric_anchor_fraction * size[1], metrics.axis, delta=2.0)
                self.assertAlmostEqual(self.window._lyric_clip_rect.top(), 0.0, delta=1.0)
                self.assertAlmostEqual(self.window._lyric_clip_rect.bottom(), size[1], delta=1.0)

    def test_glass_card_uses_the_study_fill_edge_and_blur(self):
        self.assertEqual(self.window.WIDE_GLASS_FILL.alpha(), 0x1C)
        self.assertEqual(self.window.WIDE_GLASS_EDGE.alpha(), 0x38)
        self.assertEqual(self.window.WIDE_GLASS_BLUR, 12.0)
        self.assertEqual(self.window.WIDE_TRANSPORT_ICON.alpha(), 0xD8)
        self.assertEqual(self.window.WIDE_TRANSPORT_ICON.red(), 255)

    def test_progress_uses_the_playback_clock_and_drag_seeks(self):
        self.pose((715, 540))
        frame = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        self.window.render(frame)
        self.assertAlmostEqual(self.window._displayed_progress_ratio(), 73.0 / 192.0, places=3)
        track = self.window._progress_track_rect
        self.assertGreater(track.width(), 20.0)
        point = QPointF(track.left() + track.width() * 0.25, track.center().y())
        self.window.mousePressEvent(self.event(point))
        self.assertTrue(self.window._progress_dragging)
        moved = QPointF(track.left() + track.width() * 0.5, track.center().y())
        self.window.mouseMoveEvent(self.drag_event(moved))
        self.window.mouseReleaseEvent(self.event(moved))
        self.assertFalse(self.window._progress_dragging)
        self.assertEqual(len(self.seeked), 1)
        self.assertAlmostEqual(self.seeked[0], 96.0, delta=1.0)

    def test_footer_plate_holds_collection_and_dj_without_moving_the_lamp_helper(self):
        self.pose((995, 690))
        self.window.dj_available = True
        self.window._owned_personal_dj_enabled = True
        surface = QRectF(self.window.rect())
        with patch("local.music_surface_window.time.monotonic", return_value=100.0), \
                patch("local.music_song_collection.time.monotonic", return_value=100.0):
            plate = self.window._footer_glass_rect(surface)
            face = self.window._song_collection_geometry(surface)
            icon, _caption = self.window._personal_dj_geometry(surface, 100.0, expansion=0.0)
        self.assertTrue(plate.isValid())
        self.assertTrue(plate.contains(face.center()))
        self.assertTrue(plate.contains(icon.center()))
        # The lamp stays a fraction of the waveform icon, at its bottom-right.
        dot = max(6.0, min(10.0, icon.width() * 0.22))
        badge = QRectF(icon.right() - dot * 0.82, icon.bottom() - dot * 0.82, dot, dot)
        self.assertLess(badge.center().x(), icon.right())
        self.assertGreater(badge.center().y(), icon.center().y())

    def test_handoff_durations_and_curve_do_not_rebuild_the_window(self):
        self.assertEqual(handoff_seconds(True, 0.0), 0.22)
        self.assertEqual(handoff_seconds(False, 60.0), 0.32)
        self.assertEqual(handoff_seconds(False, 0.0), 0.42)
        curve = wide_narrow_easing()
        self.assertEqual(curve.type(), QEasingCurve.Type.BezierSpline)
        self.assertAlmostEqual(curve.valueForProgress(0.0), 0.0, places=3)
        self.assertAlmostEqual(curve.valueForProgress(1.0), 1.0, places=3)
        self.assertAlmostEqual(curve.valueForProgress(0.5), 0.5, delta=0.02)
        self.pose((995, 690))
        with patch.object(self.window, "grab") as grab, patch.object(self.window, "_capture_content_layout") as capture:
            self.window._advance_width_chrome(0.4)
            self.window._advance_width_layout(0.4)
        grab.assert_not_called()
        capture.assert_not_called()

    def event(self, point):
        return SimpleNamespace(
            button=lambda: Qt.MouseButton.LeftButton,
            buttons=lambda: Qt.MouseButton.NoButton,
            position=lambda: point,
            globalPosition=lambda: QPointF(self.window.mapToGlobal(point.toPoint())),
            accept=lambda: None,
        )

    def drag_event(self, point):
        event = self.event(point)
        event.buttons = lambda: Qt.MouseButton.LeftButton
        return event


if __name__ == "__main__":
    unittest.main()
