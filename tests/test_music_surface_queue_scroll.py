from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtWidgets import QApplication

from api.online_music import OnlineSong
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner
from tests.test_owned_playback_surface import _Provider


class MusicSurfaceQueueScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.owner.data_provider = _Provider()
        self.state = {"token": 47, "current": 0,
            "songs": tuple(OnlineSong("netease", str(i), f"Song {i}", artist="Artist") for i in range(100))}
        self.owner.data_provider.get_owned_playback_queue = lambda: (
            self.state["token"], self.state["current"], self.state["songs"])
        self.owner.data_provider.select_owned_queue_index = Mock(return_value=True)
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.window.set_content_layout("playlist", animate=False)
        self.render()

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def render(self):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.window.render(image)
        return image

    def wheel(self, *, pixels=0, angle=0):
        point = self.window._queue_view_rect.center()
        event = QWheelEvent(point, QPointF(self.window.mapToGlobal(point.toPoint())),
            QPoint(0, pixels), QPoint(0, angle), Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        event.setAccepted(False)
        self.window.wheelEvent(event)
        self.assertTrue(event.isAccepted())

    def pointer(self, point):
        return SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton, position=lambda: point,
            globalPosition=lambda: QPointF(self.window.mapToGlobal(point.toPoint())), accept=lambda: None)

    def test_mouse_wheel_and_fractional_notches_move_the_visible_queue(self):
        scale = self.window._surface_scale()
        self.wheel(angle=-120)
        self.assertAlmostEqual(self.window._queue_scroll, 64. * scale)
        self.assertEqual(self.window._queue_hit_rects, {})
        self.render()
        self.assertNotIn(0, self.window._queue_hit_rects)
        self.assertIn(1, self.window._queue_hit_rects)
        self.wheel(angle=-30)
        self.assertAlmostEqual(self.window._queue_scroll, 80. * scale)

    def test_precision_trackpad_uses_logical_pixel_delta_before_angle_delta(self):
        self.wheel(pixels=-17, angle=120)
        self.assertEqual(self.window._queue_scroll, 17.)
        self.wheel(pixels=5)
        self.assertEqual(self.window._queue_scroll, 12.)

    def test_scroll_limits_apply_before_repaint_and_indicator_reaches_both_ends(self):
        first_thumb = self.window._queue_scrollbar_rect
        self.assertFalse(first_thumb.isEmpty())
        self.wheel(pixels=-1000000)
        self.assertEqual(self.window._queue_scroll, self.window._queue_scroll_limit)
        self.render()
        self.assertIn(99, self.window._queue_hit_rects)
        self.assertAlmostEqual(self.window._queue_scrollbar_rect.bottom(), self.window._queue_view_rect.bottom())
        self.wheel(pixels=1000000)
        self.assertEqual(self.window._queue_scroll, 0.)
        self.render()
        self.assertEqual(self.window._queue_scrollbar_rect, first_thumb)

    def test_scroll_cancels_held_row_before_its_hit_target_moves(self):
        event = self.pointer(self.window._queue_hit_rects[1].center())
        self.window.mousePressEvent(event)
        self.assertEqual(self.window._queue_pressed, 1)
        self.assertEqual(self.window._queue_pressed_token, 47)
        self.wheel(angle=-120)
        self.assertIsNone(self.window._queue_pressed)
        self.assertIsNone(self.window._queue_pressed_token)
        self.render()
        self.window.mouseReleaseEvent(event)
        self.owner.data_provider.select_owned_queue_index.assert_not_called()
        current = self.pointer(self.window._queue_hit_rects[2].center())
        self.window.mousePressEvent(current)
        self.window.mouseReleaseEvent(current)
        self.owner.data_provider.select_owned_queue_index.assert_called_once_with(2, 47)

    def test_repaints_keep_manual_position_and_new_current_song_is_revealed(self):
        self.wheel(angle=-480)
        position = self.window._queue_scroll
        self.render(); self.render()
        self.assertEqual(self.window._queue_scroll, position)
        self.state["current"] = 90
        self.render()
        self.assertGreater(self.window._queue_scroll, position)
        self.assertIn(90, self.window._queue_hit_rects)

    def test_each_new_current_song_centers_even_when_its_row_was_already_visible(self):
        self.state["current"] = 50
        self.render()
        self.assertIn(51, self.window._queue_hit_rects)
        previous = self.window._queue_scroll
        self.state["current"] = 51
        self.render()
        self.assertGreater(self.window._queue_scroll, previous)
        row_height = 64. * self.window._surface_scale()
        viewport_top = self.window._queue_view_rect.top() + 46. * self.window._surface_scale()
        viewport_center = (viewport_top + self.window._queue_view_rect.bottom()) * .5
        row_center = viewport_top + (51.5 * row_height) - self.window._queue_scroll
        self.assertAlmostEqual(row_center, viewport_center)

    def test_first_and_last_current_songs_center_with_end_clamping(self):
        for index, expected in ((50, None), (0, 0.), (99, "last")):
            with self.subTest(index=index):
                self.state["current"] = index
                self.render()
                if expected == "last":
                    self.assertEqual(self.window._queue_scroll, self.window._queue_scroll_limit)
                elif expected is not None:
                    self.assertEqual(self.window._queue_scroll, expected)
                self.assertIn(index, self.window._queue_hit_rects)

    def test_short_empty_and_unavailable_queues_have_no_stale_scroll_or_indicator(self):
        self.wheel(pixels=-1000000)
        for songs, token, current in ((self.state["songs"][:1], 48, 0), ((), 49, -1), ((), None, -1)):
            with self.subTest(token=token):
                self.state.update(songs=songs, token=token, current=current)
                self.render()
                self.assertEqual(self.window._queue_scroll, 0.)
                self.assertEqual(self.window._queue_scroll_limit, 0.)
                self.assertTrue(self.window._queue_scrollbar_rect.isEmpty())
                self.wheel(pixels=-100)
                self.assertEqual(self.window._queue_scroll, 0.)

    def test_frozen_layout_and_presentation_frames_do_not_scroll(self):
        for field, frozen in (("_layout_transition_fraction", .5), ("_pending_fullscreen", False),
                ("_island_entering", True), ("_island_returning", True)):
            with self.subTest(field=field):
                original = getattr(self.window, field)
                setattr(self.window, field, frozen)
                self.wheel(pixels=-50)
                self.assertEqual(self.window._queue_scroll, 0.)
                setattr(self.window, field, original)
