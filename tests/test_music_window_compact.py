from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_fullscreen import _PointerEvent
from tests.test_music_surface_transition import _TransitionOwner
from tests.test_owned_playback_surface import _Provider


class MusicWindowCompactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.owner.data_provider = _Provider()
        self.previews = []
        self.commits = []
        self.commit_targets = []
        self.owner._preview_free_island_from_music_surface = self.preview
        self.owner._commit_free_island_from_music_surface = self.commit
        self.window = MusicSurfaceWindow(self.owner)
        self.window._available_geometry = lambda: QRect(0, 0, 3840, 2160)
        self.window.setGeometry(400, 300, 330, 400)
        self.window._fullscreen = False
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()
        self.capture = patch.object(self.window, "_capture_transition_snapshot", side_effect=self.snapshot).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def snapshot(self):
        dpr = self.window.devicePixelRatioF()
        image = QImage(round(self.window.width() * dpr), round(self.window.height() * dpr),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(QColor(30, 70, 130))
        return image

    def preview(self, center, dpr):
        self.previews.append((QPointF(center), dpr))
        target = QRect(0, 0, 160, 50)
        target.moveCenter(center.toPoint())
        image = QImage(round(160 * dpr), round(50 * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(QColor(7, 7, 7))
        return target, image

    def commit(self, center, target=None):
        # Deliberately do not hide/reset: one consumed completion cannot depend
        # on the real owner doing so, or on synchronous Qt hide delivery.
        self.commits.append(QPointF(center))
        self.commit_targets.append(QRect(target) if target is not None else QRect())

    def drag(self, point, delta):
        origin = QPointF(self.window.mapToGlobal(point.toPoint()))
        self.window.mousePressEvent(_PointerEvent(point, global_point=origin))
        self.move(point, origin, delta)
        return origin

    def move(self, point, origin, delta):
        self.window.mouseMoveEvent(_PointerEvent(
            point + delta, global_point=origin + delta, buttons=Qt.MouseButton.LeftButton,
        ))

    def finish(self):
        self.window._compact_animation.stop()
        self.window._advance_window_compact(self.window._compact_animation.endValue())
        self.window._finish_window_compact()

    def assert_clean(self):
        self.assertTrue(self.window._compact_snapshot.isNull())
        self.assertTrue(self.window._compact_island.isNull())
        self.assertFalse(self.window._compact_target.isValid())
        self.assertFalse(self.window._compact_base_geometry.isValid())
        self.assertFalse(self.window._compact_canvas_geometry.isValid())
        self.assertEqual(self.window._compact_fraction, 0.)
        self.assertFalse(self.window._compact_committing)
        self.assertEqual(self.window._compact_animation.state(), self.window._compact_animation.State.Stopped)

    def test_default_is_proportionate_and_restored_height_is_preserved_above_minimum(self):
        self.assertEqual(self.window._minimum_window_size(), QSize(330, 400))
        default = self.window._default_window_geometry()
        self.assertEqual(default.size(), QSize(960, 540))
        self.assertEqual(self.window._bounded_window_geometry(QRect(400, 300, 100, 900)),
                         QRect(400, 300, 330, 900))
        self.assertEqual(self.window._bounded_window_geometry(QRect(-20, 2300, 5000, 700)),
                         QRect(0, 1460, 3840, 700))
        self.assertEqual(self.window._bounded_window_geometry(QRect(400, 300, 500, 200)),
                         QRect(400, 300, 500, 400))

    def test_all_four_edges_and_corners_resize_both_axes_with_opposite_anchors_fixed(self):
        original = QRect(400, 300, 660, 400)
        cases = (
            (QPointF(0, 200), QPointF(-100, 80), QRect(300, 300, 760, 400)),
            (QPointF(659, 200), QPointF(100, -80), QRect(400, 300, 760, 400)),
            (QPointF(330, 0), QPointF(90, -80), QRect(400, 220, 660, 480)),
            (QPointF(330, 399), QPointF(-90, 80), QRect(400, 300, 660, 480)),
            (QPointF(0, 0), QPointF(-100, -80), QRect(300, 220, 760, 480)),
            (QPointF(659, 0), QPointF(100, -80), QRect(400, 220, 760, 480)),
            (QPointF(0, 399), QPointF(-100, 80), QRect(300, 300, 760, 480)),
            (QPointF(659, 399), QPointF(100, 80), QRect(400, 300, 760, 480)),
        )
        for point, delta, expected in cases:
            with self.subTest(point=point):
                self.window.setGeometry(original)
                self.drag(point, delta)
                self.assertEqual(self.window.geometry(), expected)
                self.assertFalse(self.window._compact_committing)
        self.assertEqual(self.previews, [])
        self.assertEqual(self.owner.data_provider.calls, [])

    def test_all_resize_cursors_remain_available_before_either_axis_reaches_minimum(self):
        self.window.setGeometry(400, 300, 660, 600)
        self.window._button_rects = {}
        self.window._media_button_rects = {}
        self.window._lyric_hit_rects = {}
        cases = (
            (QPointF(0, 300), Qt.CursorShape.SizeHorCursor),
            (QPointF(659, 300), Qt.CursorShape.SizeHorCursor),
            (QPointF(330, 0), Qt.CursorShape.SizeVerCursor),
            (QPointF(330, 599), Qt.CursorShape.SizeVerCursor),
            (QPointF(0, 0), Qt.CursorShape.SizeFDiagCursor),
            (QPointF(659, 599), Qt.CursorShape.SizeFDiagCursor),
            (QPointF(659, 0), Qt.CursorShape.SizeBDiagCursor),
            (QPointF(0, 599), Qt.CursorShape.SizeBDiagCursor),
        )
        for point, expected in cases:
            with self.subTest(point=point):
                self.window.mouseMoveEvent(QMouseEvent(
                    QEvent.Type.MouseMove, point, QPointF(self.window.mapToGlobal(point.toPoint())),
                    Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                ))
                self.assertEqual(self.window.cursor().shape(), expected)

    def test_real_width_stops_at_330_with_left_or_right_anchor_preserved(self):
        original = QRect(400, 300, 430, 400)
        for left in (False, True):
            with self.subTest(left=left):
                self.window.setGeometry(original)
                point = QPointF(0 if left else 429, 200)
                self.drag(point, QPointF(160 if left else -160, 0))
                self.assertEqual(self.window.size(), QSize(330, 400))
                self.assertEqual(self.window.y(), 300)
                self.assertEqual(self.window.geometry().right() if left else self.window.x(),
                                 original.right() if left else original.x())
                self.assertGreater(self.window._compact_fraction, 0.)
                self.assertFalse(self.window._compact_committing)

    def test_vertical_inward_drag_only_previews_at_minimum_width(self):
        for width in (331, 660):
            with self.subTest(width=width):
                self.window.setGeometry(400, 300, width, 400)
                self.drag(QPointF(width // 2, 0), QPointF(0, 250))
                self.assertEqual(self.window.size(), QSize(width, 400))
                self.assert_clean()
        self.assertEqual(self.previews, [])
        self.window.setGeometry(400, 300, 330, 400)
        self.drag(QPointF(165, 0), QPointF(0, 100))
        self.assertEqual(self.window._compact_fraction, .5)
        self.assertEqual(self.window.geometry(), QRect(400, 300, 330, 400))
        self.assertEqual(len(self.previews), 1)

    def test_horizontal_overscroll_does_not_preview_while_height_exceeds_minimum(self):
        for height in (401, 600):
            with self.subTest(height=height):
                self.window.setGeometry(400, 300, 330, height)
                self.drag(QPointF(329, height // 2), QPointF(-250, 0))
                self.assertEqual(self.window.size(), QSize(330, height))
                self.assert_clean()
        self.assertEqual(self.previews, [])
        self.assertEqual(self.commits, [])
        self.assertEqual(self.owner.data_provider.calls, [])

    def test_corner_reaches_both_minimums_before_continued_overscroll_previews(self):
        self.window.setGeometry(400, 300, 430, 500)
        point = QPointF(429, 499)
        origin = self.drag(point, QPointF(-200, 0))
        self.assertEqual(self.window.size(), QSize(330, 500))
        self.assert_clean()
        self.move(point, origin, QPointF(-200, -100))
        self.assertEqual(self.window.size(), QSize(330, 400))
        self.assertGreater(self.window._compact_fraction, 0.)
        self.assertFalse(self.window._compact_committing)
        self.move(point, origin, QPointF(-265, -100))
        self.assertTrue(self.window._compact_committing)
        self.assertEqual(self.window._normal_geometry, QRect(400, 300, 330, 400))
        self.finish()
        self.assertEqual(len(self.commits), 1)

    def test_top_left_corner_keeps_bottom_right_anchor_at_both_minimums(self):
        original = QRect(400, 300, 430, 500)
        self.window.setGeometry(original)
        self.drag(QPointF(0, 0), QPointF(100, 100))
        self.assertEqual(self.window.size(), QSize(330, 400))
        self.assertEqual(self.window.geometry().bottomRight(), original.bottomRight())
        self.assert_clean()

    def test_top_and_bottom_clamp_height_at_400_without_entering_gesture_while_wide(self):
        original = QRect(400, 300, 500, 540)
        for top in (False, True):
            with self.subTest(top=top):
                self.window.setGeometry(original)
                self.drag(QPointF(250, 0 if top else 539), QPointF(0, 300 if top else -300))
                self.assertEqual(self.window.size(), QSize(500, 400))
                self.assertEqual(self.window.geometry().bottom() if top else self.window.y(),
                                 original.bottom() if top else original.y())
                self.assert_clean()
        self.assertEqual(self.previews, [])
        self.assertEqual(self.commits, [])

    def test_growing_either_dimension_cancels_preview_and_resumes_normal_resize(self):
        cases = (
            (QPointF(329, 399), QPointF(-100, 0), QPointF(-100, 80), QSize(330, 480)),
            (QPointF(329, 399), QPointF(0, -100), QPointF(80, -100), QSize(410, 400)),
        )
        for point, inward, outward, expected in cases:
            with self.subTest(size=expected):
                self.window._reset_window_compact()
                self.window.setGeometry(400, 300, 330, 400)
                origin = self.drag(point, inward)
                self.assertGreater(self.window._compact_fraction, 0.)
                self.move(point, origin, outward)
                self.assert_clean()
                self.assertEqual(self.window.size(), expected)
        self.assertEqual(self.commits, [])
        self.assertEqual(self.owner.data_provider.calls, [])

    def test_exact_horizontal_half_size_threshold_commits_once_without_audio(self):
        self.window._update_window_compact(165.001, 400)
        self.assertLess(self.window._compact_fraction, 1.)
        self.assertFalse(self.window._compact_committing)
        self.assertEqual(self.commits, [])
        self.window._update_window_compact(165., 400)
        self.assertTrue(self.window._compact_committing)
        self.assertEqual(self.window._compact_fraction, 1.)
        self.assertEqual(self.commits, [])
        self.finish()
        self.window._finish_window_compact()
        self.assertEqual(self.commits, [QPointF(self.window.geometry().center())])
        self.assertEqual(self.owner.data_provider.calls, [])
        self.assertFalse(self.owner.restored)
        self.assert_clean()

    def test_vertical_half_height_threshold_is_exact_and_does_not_resize_page(self):
        self.window._update_window_compact(330, 200.001)
        self.assertFalse(self.window._compact_committing)
        self.assertLess(self.window._compact_fraction, 1.)
        self.window._update_window_compact(330, 200.)
        self.assertTrue(self.window._compact_committing)
        self.assertEqual(self.window.size(), QSize(330, 400))
        self.finish()
        self.assertEqual(len(self.commits), 1)

    def test_diagonal_drag_uses_strongest_axis_and_keeps_one_capture(self):
        self.window._update_window_compact(247.5, 300.)
        self.assertAlmostEqual(self.window._compact_fraction, .5)
        self.window._update_window_compact(247.5, 240.)
        self.assertAlmostEqual(self.window._compact_fraction, .8)
        self.assertEqual(self.capture.call_count, 1)
        self.assertEqual(len(self.previews), 1)
        self.assertEqual(self.commits, [])

    def test_reversing_pointer_reduces_preview_then_releases_images_at_boundary(self):
        point = QPointF(329, 200)
        origin = self.drag(point, QPointF(-100, 0))
        prior = self.window._compact_fraction
        self.move(point, origin, QPointF(-30, 0))
        self.assertGreater(prior, self.window._compact_fraction)
        self.move(point, origin, QPointF(0, 0))
        self.assertEqual(self.window.geometry(), QRect(400, 300, 330, 400))
        self.assert_clean()
        self.assertEqual(self.commits, [])

    def test_release_below_threshold_springs_back_without_owner_or_audio_command(self):
        point = QPointF(329, 200)
        self.drag(point, QPointF(-100, 0))
        self.window.mouseReleaseEvent(_PointerEvent(point))
        self.assertEqual(self.window._compact_animation.endValue(), 0.)
        self.assertGreater(self.window._compact_animation.startValue(), 0.)
        self.finish()
        self.assert_clean()
        self.assertEqual(self.window.size(), QSize(330, 400))
        self.assertEqual(self.commits, [])
        self.assertEqual(self.owner.data_provider.calls, [])

    def test_new_pointer_update_stops_running_rebound_at_current_requested_fraction(self):
        self.window._update_window_compact(230., 400.)
        self.window._release_window_compact()
        self.window._compact_animation.setCurrentTime(30)
        self.window._update_window_compact(297., 400.)
        self.assertAlmostEqual(self.window._compact_fraction, .2)
        self.assertEqual(self.window._compact_animation.state(), self.window._compact_animation.State.Stopped)
        self.assertEqual(self.capture.call_count, 1)
        self.assertEqual(self.commits, [])

    def test_escape_cancels_preview_without_returning_to_previous_island(self):
        self.window._update_window_compact(230., 400.)
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        with patch.object(self.window, "return_to_island") as restore:
            self.window.keyPressEvent(event)
        self.assertTrue(event.isAccepted())
        restore.assert_not_called()
        self.finish()
        self.assert_clean()
        self.assertEqual(self.commits, [])
        self.assertEqual(self.owner.data_provider.calls, [])

    def test_commit_inflight_ignores_further_moves_release_and_escape(self):
        self.window._update_window_compact(165., 400.)
        self.window._compact_animation.stop()
        with patch.object(self.window._compact_animation, "start") as restart:
            self.window._update_window_compact(50., 100.)
            self.assertTrue(self.window._release_window_compact())
            self.window.mouseMoveEvent(_PointerEvent(QPointF(0, 0), buttons=Qt.MouseButton.LeftButton))
            self.window.mouseReleaseEvent(_PointerEvent(QPointF(0, 0)))
            self.window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                               Qt.KeyboardModifier.NoModifier))
        restart.assert_not_called()
        self.finish()
        self.assertEqual(len(self.commits), 1)
        self.assertEqual(self.owner.data_provider.calls, [])

    def test_preview_dismisses_panels_once_and_cached_paints_skip_live_scene(self):
        with patch.object(self.window, "_dismiss_audio_output_panel") as audio, \
                patch.object(self.window, "_dismiss_song_playlist_panel") as collection, \
                patch.object(self.window, "_close_dj_voice_selector") as voice:
            self.window._update_window_compact(280., 400.)
            self.window._update_window_compact(230., 400.)
        audio.assert_called_once_with(immediate=True)
        collection.assert_called_once_with()
        voice.assert_called_once_with()
        key = self.window._compact_snapshot.cacheKey()
        with patch.object(self.window, "_paint_background") as background, \
                patch.object(self.window, "_lyric_frame") as lyrics:
            for fraction in (.3, .6, .9):
                self.window._compact_fraction = fraction
                frame = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
                frame.fill(Qt.GlobalColor.transparent)
                self.window.render(frame)
                self.assertEqual(self.window._compact_snapshot.cacheKey(), key)
                self.assertGreater(frame.pixelColor(frame.width() // 2, frame.height() // 2).alpha(), 0)
        background.assert_not_called()
        lyrics.assert_not_called()
        self.assertEqual(self.capture.call_count, 1)

    def test_high_dpr_preview_keeps_logical_target_and_native_raster_resolution(self):
        with patch.object(self.window, "devicePixelRatioF", return_value=2.):
            self.window._update_window_compact(230., 400.)
        self.assertEqual(self.previews, [(QPointF(self.window.geometry().center()), 2.)])
        self.assertEqual(self.window._compact_target.size(), QSize(160, 50))
        self.assertEqual(self.window._compact_snapshot.size(), QSize(660, 800))
        self.assertEqual(self.window._compact_snapshot.devicePixelRatio(), 2.)
        self.assertEqual(self.window._compact_island.size(), QSize(320, 100))
        self.assertEqual(self.window._compact_island.devicePixelRatio(), 2.)
        self.assertEqual(self.window._compact_target.center(), self.window.geometry().center())

    def test_wide_island_uses_asymmetric_transparent_canvas_without_clipping_at_screen_edge(self):
        base = QRect(0, 300, 330, 400)
        target = QRect(0, 475, 640, 50)
        expected_canvas = base.united(target.adjusted(-2, -2, 2, 2))
        ink = QColor(188, 23, 172)

        def wide_preview(center, dpr):
            self.assertEqual(center, QPointF(base.center()))
            image = QImage(round(target.width() * dpr), round(target.height() * dpr),
                           QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(dpr)
            image.fill(ink)
            return target, image

        for dpr in (1., 2.):
            with self.subTest(dpr=dpr):
                self.window._reset_window_compact()
                self.window.setGeometry(base)
                self.window._normal_geometry = QRect(base)
                with patch.object(self.window, "devicePixelRatioF", return_value=dpr), \
                        patch.object(self.owner, "_preview_free_island_from_music_surface", side_effect=wide_preview):
                    self.window._update_window_compact(230., 400.)
                self.assertEqual(self.window.geometry(), expected_canvas)
                self.assertEqual(self.window._compact_canvas_geometry, expected_canvas)
                self.assertEqual(self.window._compact_base_geometry, base)
                self.assertEqual(self.window._normal_geometry, base)
                self.assertNotEqual(self.window.geometry().center(), base.center())
                self.assertTrue(self.window.geometry().contains(target.adjusted(-2, -2, 2, 2)))
                self.assertEqual(self.window._compact_snapshot.size(), QSize(round(330 * dpr), round(400 * dpr)))
                self.window._compact_fraction = 1.
                frame = QImage(round(self.window.width() * dpr), round(self.window.height() * dpr),
                               QImage.Format.Format_ARGB32_Premultiplied)
                frame.setDevicePixelRatio(dpr)
                frame.fill(Qt.GlobalColor.transparent)
                self.window.render(frame)

                def pixel(global_x, global_y):
                    return frame.pixelColor(round((global_x - self.window.x()) * dpr),
                                            round((global_y - self.window.y()) * dpr))

                # Both lateral ends extend beyond the ordinary page; every
                # corner of the real 640px target must survive the canvas.
                for x in (target.left() + 2, target.right() - 2):
                    for y in (target.top() + 2, target.bottom() - 2):
                        self.assertEqual(pixel(x, y), ink)
                self.assertEqual(pixel(600, 320).alpha(), 0)
                # The page remains in its 330px logical footprint, rather
                # than being rescaled to fill the enlarged native canvas.
                self.assertGreater(pixel(100, 320).alpha(), 0)
                self.assertEqual(self.window._normal_geometry, base)

    def test_wide_canvas_commit_uses_base_center_and_preserves_next_normal_window(self):
        base = QRect(0, 300, 330, 400)
        target = QRect(0, 475, 680, 50)
        image = QImage(target.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(7, 7, 7))
        self.window.setGeometry(base)
        self.window._normal_geometry = QRect(base)
        with patch.object(self.owner, "_preview_free_island_from_music_surface", return_value=(target, image)):
            self.window._update_window_compact(230., 400.)
            self.assertNotEqual(self.window.geometry().center(), base.center())
            self.window._update_window_compact(165., 400.)
        self.assertEqual(self.window._normal_geometry, base)
        self.finish()
        self.window._finish_window_compact()
        self.assertEqual(self.commits, [QPointF(base.center())])
        self.assertEqual(self.commit_targets, [target])
        self.assertEqual(self.window.geometry(), base)
        self.assertEqual(self.window._normal_geometry, base)
        self.assertEqual(self.owner.data_provider.calls, [])
        self.assert_clean()

    def test_reversing_or_hiding_wide_preview_restores_base_without_geometry_pollution(self):
        base = QRect(0, 300, 330, 400)
        target = QRect(0, 475, 680, 50)
        image = QImage(target.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(7, 7, 7))
        self.window.setGeometry(base)
        self.window._normal_geometry = QRect(base)
        with patch.object(self.owner, "_preview_free_island_from_music_surface", return_value=(target, image)):
            point = QPointF(329, 200)
            origin = self.drag(point, QPointF(-100, 0))
            self.assertGreater(self.window.width(), 600)
            self.move(point, origin, QPointF(0, 0))
            self.assertEqual(self.window.geometry(), base)
            self.assertEqual(self.window._normal_geometry, base)
            self.assert_clean()
            self.window.show()
            self.window._timer.stop()
            self.window._audio_output_status_timer.stop()
            self.window._update_window_compact(230., 400.)
            self.assertGreater(self.window.width(), 600)
            self.window.hide()
        self.assertEqual(self.window.geometry(), base)
        self.assertEqual(self.window._normal_geometry, base)
        self.assert_clean()
        self.assertEqual(self.commits, [])

    def test_preview_exception_releases_partial_capture_and_allows_normal_paint_and_retry(self):
        base = QRect(self.window.geometry())
        with patch.object(self.owner, "_preview_free_island_from_music_surface",
                          side_effect=RuntimeError("preview fixture failed")):
            self.window._update_window_compact(230., 400.)
        self.assert_clean()
        self.assertEqual(self.window.geometry(), base)
        self.assertFalse(self.window._paint_window_compact())
        frame = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(Qt.GlobalColor.transparent)
        with patch.object(self.window, "_paint_background", wraps=self.window._paint_background) as background:
            self.window.render(frame)
        background.assert_called_once()
        self.window._update_window_compact(230., 400.)
        self.assertFalse(self.window._compact_snapshot.isNull())
        self.assertTrue(self.window._compact_target.isValid())
        self.assertGreater(self.window._compact_fraction, 0.)
        self.assertEqual(len(self.previews), 1)
        self.assertEqual(self.commits, [])
        self.assertEqual(self.owner.data_provider.calls, [])

    def test_null_source_or_island_cannot_leave_half_active_preview(self):
        for null_source in (False, True):
            with self.subTest(null_source=null_source):
                self.window._reset_window_compact()
                with patch.object(self.window, "_capture_transition_snapshot",
                                  return_value=QImage() if null_source else self.snapshot()), \
                        patch.object(self.owner, "_preview_free_island_from_music_surface",
                                     return_value=(QRect(400, 300, 160, 50), QImage())):
                    self.window._update_window_compact(230., 400.)
                self.assert_clean()
                self.assertEqual(self.commits, [])

    def test_hide_shutdown_and_presentation_cancel_discard_late_completion(self):
        self.window.show()
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()
        for action in (self.window.hide, self.window._cancel_mode_transition, self.window.shutdown):
            with self.subTest(action=action.__name__):
                self.window._update_window_compact(165., 400.)
                self.assertTrue(self.window._compact_committing)
                action()
                self.window._finish_window_compact()
                self.assert_clean()
        self.assertEqual(self.commits, [])
        self.assertEqual(self.owner.data_provider.calls, [])


if __name__ == "__main__":
    unittest.main()
