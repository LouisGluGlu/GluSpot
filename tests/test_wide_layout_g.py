"""Wide layout G: centered column, glass transport card, and the 220/320/420 ms handoff."""
from __future__ import annotations

import tests.isolate_appdata  # noqa: F401  # before product imports capture %APPDATA%

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QImage
from PySide6.QtWidgets import QApplication

from _demos.narrow3.narrow_window import NarrowStudyWindow
from local.music_surface_layout import layout_g_targets, wide_narrow_easing
from local.music_width_transition import handoff_seconds
from tests.test_music_surface_transition import _TransitionOwner
from tests.test_owned_playback_surface import _Provider



def assert_hidden_group_centred(case, hidden, height, delta=0.75):
    """Controls hidden: cover + title + artist sit centred in the window.

    The left inset keeps the shown rule and is not raised to the new top.
    """
    group = hidden.artist.y + hidden.artist.h - hidden.cover.y
    margin = max(18.0, hidden.unit * 5.0)
    expected = max(margin, min((height - group) / 2.0, height - group - margin))
    case.assertAlmostEqual(hidden.cover.y, expected, delta=delta)
    case.assertLessEqual(hidden.cover.x, hidden.cover.y + delta)

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
                from local.music_surface_layout import resting_cover_side
                self.assertAlmostEqual(metrics.cover.width(), resting_cover_side(*size), delta=0.6)
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
                targets = metrics.targets
                plain = layout_g_targets(size[0], size[1], **self.window._layout_g_font_measures(surface))
                for name in ("cover", "title", "artist", "card", "track", "time_row", "buttons"):
                    box = getattr(targets, name)
                    other = getattr(plain, name)
                    painted = getattr(metrics, name)
                    self.assertEqual((box.x, box.y, box.w, box.h), (other.x, other.y, other.w, other.h))
                    self.assertAlmostEqual(painted.x(), box.x, delta=0.01)
                    self.assertAlmostEqual(painted.y(), box.y, delta=0.01)
                    self.assertAlmostEqual(painted.width(), box.w, delta=0.01)
                    self.assertAlmostEqual(painted.height(), box.h, delta=0.01)
                self.assertTrue(targets.card.y <= targets.track.y <= targets.buttons.y)
                hidden = layout_g_targets(
                    size[0], size[1], control_reveal=0.0,
                    **self.window._layout_g_font_measures(surface),
                )
                # Hidden controls grow the sleeve only while its left inset
                # can still match the top; a tight window keeps the size.
                self.assertGreaterEqual(hidden.cover.w, plain.cover.w - 0.01)
                assert_hidden_group_centred(self, hidden, size[1])
                self.assertGreater(hidden.title_size, plain.title_size)
                self.assertLessEqual(hidden.cover.x + hidden.cover.w, (6.8 + 37.8) * unit + 0.6)

    def test_hidden_cover_keeps_left_inset_equal_to_top_for_every_font_metric(self):
        size = (960, 540)
        unit = min(size[0] / 100.0, size[1] / 75.8)
        lane = (6.8 + 37.8) * unit
        for ratio in (1.0, 1.1, 1.2, 1.36, 1.5, 1.8, 2.2):
            measures = dict(
                title_font_height=28.0 * ratio,
                title_font_pixel_size=28.0,
                artist_font_height=18.0 * ratio,
                artist_font_pixel_size=18.0,
                time_font_height=16.0 * ratio / 1.36,
                title_emphasis=1.15,
            )
            shown = layout_g_targets(*size, control_reveal=1.0, **measures)
            hidden = layout_g_targets(*size, control_reveal=0.0, **measures)
            large = layout_g_targets(*size, control_reveal=0.0, cover_growth_mode="large", **measures)
            with self.subTest(ratio=ratio):
                # Shown, the left inset matches the top. Hidden, the group is
                # centred vertically and the left inset stays where it was.
                self.assertGreaterEqual(hidden.cover.w, shown.cover.w - 0.01)
                self.assertLessEqual(hidden.cover.w / shown.cover.w, 1.11 + 0.015)
                assert_hidden_group_centred(self, hidden, size[1])
                self.assertLessEqual(hidden.cover.x + hidden.cover.w, lane + 0.6)
                self.assertLess(abs(large.cover.w - hidden.cover.w), 2.0)
                if shown.cover.x + shown.cover.w < lane - 1.0:
                    self.assertAlmostEqual(shown.cover.x, shown.cover.y, delta=0.75)
                else:
                    self.assertAlmostEqual(shown.cover.x + shown.cover.w, lane, delta=1.0)

    def test_hidden_controls_keep_the_live_cover_inset_square(self):
        self.pose((960, 540))
        surface = QRectF(self.window.rect())
        shown_content, shown_cover, shown_lyric = self.window._wide_content_column_geometry()
        shown_title = self.window._metadata_layout(
            shown_cover, shown_content, self.window._surface_scale(), 1.0,
        )[1]
        shown_card = QRectF(self.window._wide_transport_rect(surface))
        self.window._control_reveal_from = self.window._control_reveal_target = 0.0
        self.window._control_transition_started_at = 0.0
        hidden_content, hidden_cover, hidden_lyric = self.window._wide_content_column_geometry()
        hidden_title = self.window._metadata_layout(
            hidden_cover, hidden_content, self.window._surface_scale(), 0.0,
        )[1]
        hidden_card = QRectF(self.window._wide_transport_rect(surface))
        self.assertGreaterEqual(hidden_cover.width(), shown_cover.width() - 0.01)
        self.assertLessEqual(hidden_cover.width() / shown_cover.width(), 1.11 + 0.015)
        hidden_targets = self.window._layout_g_targets(surface, control_reveal=0.0)
        self.assertAlmostEqual(hidden_cover.top(), hidden_targets.cover.y, delta=1.0)
        self.assertAlmostEqual(hidden_cover.left(), hidden_targets.cover.x, delta=1.0)
        assert_hidden_group_centred(self, hidden_targets, surface.height(), delta=1.0)
        # About 55 px lower than the shown cover at 960 x 540.
        self.assertGreater(hidden_cover.top() - shown_cover.top(), 40.0)
        self.assertAlmostEqual(hidden_cover.width(), hidden_cover.height(), delta=0.2)
        self.assertLessEqual(hidden_cover.right(), shown_lyric.left())
        self.assertAlmostEqual(hidden_lyric.left(), shown_lyric.left(), delta=0.6)
        self.assertAlmostEqual(hidden_lyric.top(), shown_lyric.top(), delta=0.6)
        self.assertAlmostEqual(hidden_title.width(), shown_title.width(), delta=0.6)
        self.assertGreaterEqual(hidden_title.top(), hidden_cover.bottom() - 0.6)
        self.assertAlmostEqual(hidden_card.left(), shown_card.left(), delta=0.6)
        self.assertAlmostEqual(hidden_card.top(), shown_card.top(), delta=0.6)
        self.assertAlmostEqual(hidden_card.width(), shown_card.width(), delta=0.6)

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
