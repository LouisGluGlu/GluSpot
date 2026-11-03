"""t165: width skeletons preserve t160 layout and prepared-veil gates."""
from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from api.models import TimedLyricUnit
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner
from tests import test_narrow_v4 as narrow_fixtures


class LyricResizeFreezeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.owner.media_is_playing = False
        self.owner.media_source_app = 'fixture'
        self.owner.timed_lyrics = [
            (float(index * 10), f'Fixture {index}: sing along to this long sentence as the window changes width')
            for index in range(8)
        ]
        self.owner.lyrics = [text for _, text in self.owner.timed_lyrics]
        self.window = MusicSurfaceWindow(self.owner)
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()
        self.window.resize(1100, 640)
        self.window.show()
        self.app.processEvents()
        self.finish_width_animations()
        self.wait_settle()
        self.render()

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.owner.close()
        self.app.processEvents()

    def render(self):
        ratio = self.window.devicePixelRatioF()
        image = QImage(round(self.window.width() * ratio), round(self.window.height() * ratio),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        self.window.render(image)
        return image

    def finish_width_animations(self):
        for _ in range(5):
            transition = self.window._width_transition
            if transition is None or not transition.active:
                break
            animation = getattr(self.window, '_width_chrome_animation', self.window._width_layout_animation)
            animation.setCurrentTime(animation.duration())
        self.app.processEvents()

    def wait_settle(self, maximum_ms=400):
        started = time.monotonic()
        while self.window._lyric_resize_frozen and (time.monotonic() - started) * 1000 < maximum_ms:
            QTest.qWait(10)
        self.assertFalse(self.window._lyric_resize_frozen, 'lyric geometry did not settle within 400 ms')
        return (time.monotonic() - started) * 1000

    def test_a1_programmatic_width_stream_never_calls_layout_or_mutates_lru(self):
        window = self.window
        before = window.lyric_relayout_count
        cache = dict(window._lyric_layout_cache)
        glow, reading = window._pending_glow_prewarm, window._pending_reading_prewarm
        blur_jobs, blur_token = tuple(window._lyric_blur_prewarm_queue), window._lyric_blur_prewarm_token
        with patch.object(window, '_song_lyric_layouts', side_effect=AssertionError('song layout during drag')), \
                patch.object(window, '_wrapped_lyric_layout', side_effect=AssertionError('row layout during drag')):
            for width in range(1096, 959, -4):
                window.resize(width, 640)
                self.render()
                self.assertTrue(window._lyric_resize_frozen)
                self.assertEqual(window.lyric_relayout_count, before)
                self.assertEqual(window._lyric_layout_cache, cache)
                self.assertIs(window._pending_glow_prewarm, glow)
                self.assertIs(window._pending_reading_prewarm, reading)
                self.assertEqual(tuple(window._lyric_blur_prewarm_queue), blur_jobs)
                self.assertEqual(window._lyric_blur_prewarm_token, blur_token)
        with self.assertRaises(AttributeError):
            window.lyric_relayout_count = 0

    def test_a2_final_width_settles_with_at_most_one_real_relayout(self):
        window = self.window
        before = window.lyric_relayout_count
        for width in range(1096, 879, -4):
            window.resize(width, 640)
            self.render()
        self.assertEqual(window.lyric_relayout_count, before)
        self.finish_width_animations()
        self.assertLessEqual(self.wait_settle(), 400)
        self.render()
        self.assertLessEqual(window.lyric_relayout_count - before, 1)
        self.assertEqual(window.width(), 880)
        self.assertEqual(window._song_layout_key[2], max(1, round(window._content_column_geometry()[2].width())))
        # A subsequent ordinary paint must retain the settled song layout.
        settled = window.lyric_relayout_count
        self.render()
        self.assertEqual(window.lyric_relayout_count, settled)

    def test_a3_equal_wrap_only_repositions_existing_text_layouts(self):
        self.owner.timed_lyrics = [(float(index * 10), f'Line {index}') for index in range(8)]
        self.owner.lyrics = [text for _, text in self.owner.timed_lyrics]
        self.render()
        before = self.window.lyric_relayout_count
        layouts = tuple(layout for layout, _height in self.window._song_layouts)
        self.window.resize(1099, 640)
        self.render()
        self.wait_settle()
        self.render()
        self.assertEqual(self.window.lyric_relayout_count, before)
        self.assertEqual(tuple(layout for layout, _height in self.window._song_layouts), layouts)
        self.assertEqual(self.window.width(), 1099)
        self.assertEqual(self.window._song_layout_key[2], round(self.window._content_column_geometry()[2].width()))

    def test_a4_skeleton_follows_current_width_without_song_shaping(self):
        window = self.window
        window.resize(1096, 640)
        self.render()
        started = window._lyric_resize_started_at
        for width in (1080, 1040, 1000, 1080):
            window.resize(width, 640)
            with patch('local.music_lyric_resize.time.monotonic', return_value=started + .2), \
                    patch.object(window, '_paint_live_lyric_column', side_effect=AssertionError('live ink during motion')), \
                    patch.object(window, '_paint_lyric_loading', wraps=window._paint_lyric_loading) as loading, \
                    patch.object(window, '_paint_lyric_resize_skeleton', wraps=window._paint_lyric_resize_skeleton) as skeleton:
                self.render()
                skeleton.assert_called()
                loading.assert_called()
                target = skeleton.call_args.args[1]
                load_args = loading.call_args
                self.assertEqual(load_args.kwargs.get('force'), True)
                self.assertAlmostEqual(load_args.args[1].width(), target.width(), delta=1)
                self.assertTrue(window._lyric_resize_snapshot.isNull())
                self.assertEqual(window._lyric_resize_ink_weights(), (0., 1., 0.))

    def test_skeleton_raster_reuses_lyric_loading_rows_and_animates(self):
        """Resize handoff must paint MusicSurfaceWindow._paint_lyric_loading."""
        target = QRectF(10, 10, 180, 220)
        images = []
        window = self.window
        # Pin the loader clock so the 3s row-sweep actually advances between frames.
        window._lyric_loading_started_at = 90.0
        window._lyric_loading_cycle_end_at = 0.0
        window._lyric_loading_release_at = 0.0
        window._lyric_loading_waiting = False
        window._lyric_loading_has_shown = True
        window._lyric_loading_hold_centered = False
        for now in (100.0, 102.9):
            image = QImage(200, 240, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with patch('local.music_lyric_resize.time.monotonic', return_value=now), \
                    patch.object(window, '_paint_lyric_loading', wraps=window._paint_lyric_loading) as loading:
                with QPainter(image) as painter:
                    window._paint_lyric_resize_skeleton(painter, target)
                loading.assert_called_once()
                self.assertTrue(loading.call_args.kwargs.get('force'))
            opaque = sum(1 for y in range(image.height()) for x in range(image.width())
                         if image.pixelColor(x, y).alpha() > 20)
            self.assertGreater(opaque, 80)
            self.assertEqual(image.pixelColor(2, 2).alpha(), 0)
            images.append(image.copy())
        self.assertNotEqual(images[0], images[1])

    def test_a3_font_change_and_return_reuse_wrap_equivalent_shapes(self):
        self.owner.timed_lyrics = [(float(index * 10), f'Line {index}') for index in range(8)]
        self.owner.lyrics = [text for _, text in self.owner.timed_lyrics]
        self.window.resize(600, 1000)
        self.render()
        self.finish_width_animations()
        self.wait_settle()
        self.render()
        before = self.window.lyric_relayout_count
        layouts = tuple(layout for layout, _height in self.window._song_layouts)
        source_font_size = self.window._surface_font(
            self.window.LYRIC_PIXEL_SIZE * self.window._surface_scale()).pixelSize()
        self.window.resize(580, 1000)
        self.render()
        self.finish_width_animations()
        self.wait_settle()
        self.render()
        target_font_size = self.window._surface_font(
            self.window.LYRIC_PIXEL_SIZE * self.window._surface_scale()).pixelSize()
        self.assertNotEqual(source_font_size, target_font_size)
        self.assertEqual(self.window.lyric_relayout_count, before)
        self.assertEqual(tuple(layout for layout, _height in self.window._song_layouts), layouts)
        self.render()
        self.assertEqual(self.window.lyric_relayout_count, before)
        painted_column = QRectF(self.window._lyric_resize_last_geometry['column'])
        self.window.resize(600, 1000)
        self.render()
        self.assertAlmostEqual(self.window._lyric_resize_source_rect.width(), painted_column.width(), delta=1)
        self.finish_width_animations()
        self.wait_settle()
        self.render()
        self.assertEqual(self.window.lyric_relayout_count, before)
        self.assertEqual(tuple(layout for layout, _height in self.window._song_layouts), layouts)

    def test_a9_word_target_is_disabled_until_settle(self):
        self.owner.timed_lyrics = [(0., 'first line'), (20., 'next'), (40., 'later'), (60., 'last')]
        self.owner.lyrics = [text for _, text in self.owner.timed_lyrics]
        self.owner.timed_lyric_units = [
            (TimedLyricUnit(2., 4., 'first '), TimedLyricUnit(4., 6., 'line')), (), (), (),
        ]
        self.owner.timed_lyric_timing_kinds = ['unit', 'line', 'line', 'line']
        self.render()
        target = next(iter(self.window._lyric_word_hit_rects.values()))[0].center()
        self.assertIsNotNone(self.window._lyric_word_target_at(target))
        self.window.resize(1099, 640)
        self.render()
        self.assertIsNone(self.window._lyric_word_target_at(target))
        with patch.object(self.window, '_seek_to_lyric') as seek:
            QTest.mouseClick(self.window, Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.NoModifier, target.toPoint())
            seek.assert_not_called()
        self.wait_settle()
        self.render()
        fresh = next(iter(self.window._lyric_word_hit_rects.values()))[0].center()
        self.assertIsNotNone(self.window._lyric_word_target_at(fresh))

    def test_same_track_text_change_during_freeze_commits_new_text_once(self):
        self.owner.timed_lyrics = [(float(index * 10), f'Line {index}') for index in range(8)]
        self.owner.lyrics = [text for _, text in self.owner.timed_lyrics]
        self.render()
        before = self.window.lyric_relayout_count
        self.window.resize(1099, 640)
        self.render()
        # Same row count and wrap signature must not hide changed content.
        self.owner.timed_lyrics = [(started, text.replace('Line', 'Song'))
                                   for started, text in self.owner.timed_lyrics]
        self.owner.lyrics = [text for _, text in self.owner.timed_lyrics]
        self.render()
        self.assertEqual(self.window.lyric_relayout_count, before)
        self.wait_settle()
        self.render()
        self.assertEqual(self.window.lyric_relayout_count - before, 1)
        self.assertEqual(tuple(layout.text() for layout, _ in self.window._song_layouts),
                         tuple(text for _, text in self.owner.timed_lyrics))

    def test_cover_lyrics_wrap_equivalent_font_reuse_keeps_a_real_column(self):
        self.owner.timed_lyrics = [(float(index * 10), 'Hi') for index in range(8)]
        self.owner.lyrics = [text for _, text in self.owner.timed_lyrics]
        self.window.set_content_layout('cover_lyrics', animate=False)
        self.render()
        before = self.window.lyric_relayout_count
        layouts = tuple(layout for layout, _ in self.window._song_layouts)
        for width in (600, 400):
            self.window.resize(width, 640)
            self.render()
            self.finish_width_animations()
            self.wait_settle()
            self.render()
            self.assertEqual(self.window.lyric_relayout_count, before)
            self.assertEqual(tuple(layout for layout, _ in self.window._song_layouts), layouts)
            self.assertGreater(self.window._lyric_clip_rect.width(), 80)
            self.assertGreater(self.window._lyric_clip_rect.height(), 40)
            self.render()
            self.assertEqual(self.window.lyric_relayout_count, before)

    def test_duet_fade_keeps_cached_geometry_then_uses_only_skeleton(self):
        self.owner.timed_lyrics = [
            (0., '(男)Hello'), (.5, '(女)World'), (8., '(男)Sing'), (8.5, '(女)Along'),
            (16., '(合)Together'), (24., 'Next'), (32., 'Later'), (40., 'Last'),
        ]
        self.owner.lyrics = [text for _, text in self.owner.timed_lyrics]
        self.render()
        self.assertIn(0, self.window._song_layout_key[11])
        before = self.window.lyric_relayout_count
        normal = {}
        with patch.object(self.window, '_paint_lyric_role_tag', wraps=self.window._paint_lyric_role_tag) as tags:
            self.render()
            for call in tags.call_args_list:
                normal.setdefault(call.args[1], call.args[3].x())
        self.assertIn('男', normal)
        self.assertIn('女', normal)
        self.assertGreater(normal['女'] - normal['男'], 80)
        captured = {}
        with patch.object(self.window, '_paint_lyric_role_tag', wraps=self.window._paint_lyric_role_tag) as tags:
            self.window.resize(1099, 640)
            self.render()
            for call in tags.call_args_list:
                captured.setdefault(call.args[1], call.args[3].x())
        self.assertEqual(captured['男'], normal['男'])
        self.assertEqual(captured['女'], normal['女'])
        self.assertEqual(self.window.lyric_relayout_count, before)

    def test_hiding_during_debounce_cannot_leave_permanent_freeze(self):
        self.window.resize(1096, 640)
        self.render()
        self.assertTrue(self.window._lyric_resize_frozen)
        self.window.hide()
        self.app.processEvents()
        QTest.qWait(180)
        self.window.show()
        self.app.processEvents()
        self.finish_width_animations()
        self.wait_settle()
        self.render()
        self.assertFalse(self.window._lyric_resize_frozen)

    def test_crossfade_preserves_coincident_ink_and_leaves_outside_column_clear(self):
        ratio = self.window.devicePixelRatioF()
        column = QRectF(20, 20, 40, 40)
        output = QImage(round(100 * ratio), round(100 * ratio), QImage.Format.Format_ARGB32_Premultiplied)
        output.setDevicePixelRatio(ratio)
        output.fill(Qt.GlobalColor.transparent)
        def white(painter, _rect, **_kwargs):
            painter.fillRect(column, Qt.GlobalColor.white)
        with patch.object(self.window, '_lyric_resize_crossfade_at', 100.), \
                patch.object(self.window, '_lyric_resize_target_rect', return_value=column), \
                patch.object(self.window, '_paint_live_lyric_column', side_effect=white), \
                patch.object(self.window, '_paint_lyric_resize_skeleton', side_effect=white), \
                patch('local.music_lyric_resize.time.monotonic', return_value=100.09):
            with QPainter(output) as painter:
                self.window._paint_lyric_column(painter, QRectF(self.window.rect()))
        center = output.pixelColor(round(40 * ratio), round(40 * ratio))
        self.assertGreaterEqual(center.alpha(), 254)
        self.assertGreaterEqual(center.red(), 254)
        self.assertEqual(output.pixelColor(round(10 * ratio), round(10 * ratio)).alpha(), 0)

    def test_both_fades_are_bounded_180ms_and_equal_wrap_still_fades_in(self):
        window = self.window
        window.resize(1099, 640)
        self.render()
        started = window._lyric_resize_started_at
        for elapsed, expected in ((0., (1., 0., 0.)), (.09, (.5, .5, 0.)), (.18, (0., 1., 0.))):
            for actual, wanted in zip(window._lyric_resize_ink_weights(started + elapsed), expected):
                self.assertAlmostEqual(actual, wanted)
        self.wait_settle()
        started = window._lyric_resize_crossfade_at
        self.assertIsNotNone(started)
        for elapsed, expected in ((0., (0., 1., 0.)), (.09, (0., .5, .5)), (.18, (0., 0., 1.))):
            weights = window._lyric_resize_ink_weights(started + elapsed)
            self.assertAlmostEqual(sum(weights), 1.)
            for actual, wanted in zip(weights, expected):
                self.assertAlmostEqual(actual, wanted)
        with patch('local.music_lyric_resize.time.monotonic', return_value=started + .2):
            window._advance_lyric_resize_fade()
        self.assertIsNone(window._lyric_resize_crossfade_at)
        self.assertFalse(window._lyric_resize_fade_timer.isActive())

    def test_playlist_motion_uses_current_width_bars_and_skips_live_list(self):
        window = self.window
        window.set_content_layout('playlist', animate=False)
        self.render()
        window.resize(1090, 640)
        self.render()
        started = window._lyric_resize_started_at
        from local.music_surface_layout import MusicContentLayoutMixin
        with patch('local.music_lyric_resize.time.monotonic', return_value=started + .2), \
                patch.object(MusicContentLayoutMixin, '_paint_playback_queue', side_effect=AssertionError('list rebuilt during drag')), \
                patch.object(window, '_paint_lyric_loading', wraps=window._paint_lyric_loading) as loading, \
                patch.object(window, '_paint_lyric_resize_skeleton', wraps=window._paint_lyric_resize_skeleton) as skeleton:
            window.resize(1080, 640)
            self.render()
            skeleton.assert_called()
            loading.assert_called()
            self.assertFalse(window._queue_hit_rects)
            target = skeleton.call_args.args[1]
            self.assertTrue(window._queue_view_rect.contains(target.center()))
            self.assertTrue(loading.call_args.kwargs.get('force'))
        self.wait_settle()
        self.assertIsNotNone(window._lyric_resize_crossfade_at)

    def test_new_width_during_reveal_starts_from_visible_composite(self):
        window = self.window
        column = QRectF(window._lyric_resize_last_geometry['column'])
        ratio = window.devicePixelRatioF()
        before = window.lyric_relayout_count
        def live(painter, _rect, **_kwargs):
            painter.fillRect(column, QColor('white'))
        def skeleton(painter, _rect):
            painter.fillRect(column, QColor(80, 80, 80))
        for elapsed in (.045, .09, .135):
            window._cancel_lyric_resize()
            with patch.object(window, '_lyric_resize_crossfade_at', 100.), \
                    patch.object(window, '_lyric_resize_target_rect', return_value=column), \
                    patch.object(window, '_paint_live_lyric_column', side_effect=live), \
                    patch.object(window, '_paint_lyric_resize_skeleton', side_effect=skeleton), \
                    patch('local.music_lyric_resize.time.monotonic', return_value=100. + elapsed):
                expected = QImage(round(column.width() * ratio), round(column.height() * ratio),
                                  QImage.Format.Format_ARGB32_Premultiplied)
                expected.setDevicePixelRatio(ratio)
                expected.fill(Qt.GlobalColor.transparent)
                with QPainter(expected) as painter:
                    painter.translate(-column.left(), -column.top())
                    window._paint_lyric_column(painter, QRectF(window.rect()))
                window._enter_lyric_resize()
                self.assertTrue(window._lyric_resize_frozen)
                self.assertEqual(window._lyric_resize_snapshot, expected)
                self.assertEqual(window.lyric_relayout_count, before)

    def test_pointer_resize_marks_without_width_motion_take_no_snapshot(self):
        before = self.window.lyric_relayout_count
        with patch.object(self.window, '_available_geometry', return_value=QRect(0, 0, 2400, 1600)):
            self.window._begin_width_resize()
        self.window._sync_lyric_resize_motion()
        self.render()
        self.assertIsNotNone(self.window._width_resize_marks)
        self.assertFalse(self.window._lyric_resize_frozen)
        self.assertTrue(self.window._lyric_resize_snapshot.isNull())
        self.assertEqual(self.window.lyric_relayout_count, before)
        self.window._end_width_resize()

    def test_all_four_content_selections_survive_width_freeze_and_settle(self):
        for mode in ('cover', 'cover_lyrics', 'lyrics', 'playlist'):
            with self.subTest(mode=mode):
                self.window.set_content_layout(mode, animate=False)
                self.render()
                self.window.resize(1060 if self.window.width() == 1100 else 1100, 640)
                self.render()
                self.finish_width_animations()
                self.wait_settle()
                self.render()
                self.assertEqual(self.window._content_layout, mode)
                self.assertFalse(self.window._lyric_resize_frozen)


class NarrowLyricResizeFreezeTests(unittest.TestCase):
    """The narrow shell consumes the same shared gate, including queued motion."""
    setUp = narrow_fixtures.NarrowCommitTests.setUp
    tearDown = narrow_fixtures.NarrowCommitTests.tearDown
    apply = narrow_fixtures.NarrowCommitTests.apply
    setUpClass = LyricResizeFreezeTests.__dict__['setUpClass']
    render = LyricResizeFreezeTests.render
    finish_width_animations = LyricResizeFreezeTests.finish_width_animations
    wait_settle = LyricResizeFreezeTests.wait_settle

    def warm(self, width=480):
        self.window.resize(width, 640)
        self.window.show()
        self.app.processEvents()
        self.finish_width_animations()
        self.wait_settle()
        self.render()
        self.window.study_timer.stop()
        self.window._timer.stop()
        self.window.refresh_background_veil(time.monotonic() + 1)

    def test_a6_veil_prepare_stays_zero_during_motion_and_refreshes_once_at_settle(self):
        self.warm()
        window = self.window
        self.assertTrue(window.background_veil.ready)
        prepares = window.background_veil.prepare_count
        with patch.object(window, '_available_geometry', return_value=QRect(0, 0, 2400, 1600)):
            window._begin_width_resize()
        for index, width in enumerate((476, 472, 464, 456, 480)):
            window.resize(width, 640)
            window._background_cache.fill(QColor('blue' if index % 2 else 'orange'))
            window.refresh_background_veil(time.monotonic() + index + 2)
            self.render()
            self.assertEqual(window.background_veil.prepare_count, prepares)
        window._end_width_resize()
        self.wait_settle()
        self.assertEqual(window.background_veil.prepare_count, prepares + 1)
        self.render()
        self.assertEqual(window.background_veil.prepare_count, prepares + 1)

    def test_a7_queued_split_merge_holds_freeze_until_final_animation_plus150ms(self):
        self.warm(360)
        window = self.window
        before = window.lyric_relayout_count
        with patch.object(window, '_available_geometry', return_value=QRect(0, 0, 2400, 1600)):
            window._begin_width_resize()
        window.resize(1100, 640)
        self.render()
        animation = window._width_chrome_animation
        animation.setCurrentTime(80)
        animation.pause()
        window.resize(360, 640)
        self.render()
        window._end_width_resize()
        QTest.qWait(180)
        self.assertTrue(window._lyric_resize_frozen)
        self.assertEqual(window.lyric_relayout_count, before)
        animation.resume()
        animation.setCurrentTime(animation.duration())
        self.assertEqual(window._width_transition.state, 'MERGING')
        self.render()
        self.assertTrue(window._lyric_resize_frozen)
        self.assertEqual(window.lyric_relayout_count, before)
        animation.setCurrentTime(animation.duration())
        self.assertEqual(window._width_transition.state, 'NARROW')
        QTest.qWait(100)
        self.assertTrue(window._lyric_resize_frozen)
        self.assertEqual(window.lyric_relayout_count, before)
        self.wait_settle()
        self.assertLessEqual(window.lyric_relayout_count - before, 1)

    def test_a8_chrome_visibility_and_probe_share_the_frozen_scene(self):
        self.warm()
        window = self.window
        with patch.object(window, '_available_geometry', return_value=QRect(0, 0, 2400, 1600)):
            window._set_resize_chrome_hold(True)
        window.resize(476, 640)
        self.render()
        window._refresh_chrome_backdrop(time.monotonic())
        scene = window._clear_control_renderer.live_tint_scene
        self.assertIsNotNone(scene)
        self.assertFalse(scene.isNull())
        key = scene.cacheKey()
        center = scene.pixelColor(scene.width() // 2, scene.height() // 2)
        window._scene_control_ink()
        ink_target = QColor(window._scene_control_ink_cache)
        with patch.object(window, '_song_lyric_layouts', side_effect=AssertionError('chrome rebuilt lyrics')), \
                patch.object(window, '_wrapped_lyric_layout', side_effect=AssertionError('chrome shaped lyrics')):
            for index, width in enumerate((472, 464, 460, 468, 480)):
                window.resize(width, 640)
                window._background_cache.fill(QColor('white' if index % 2 else 'black'))
                window._refresh_chrome_backdrop(time.monotonic() + index + 1)
                current = window._clear_control_renderer.live_tint_scene
                self.assertEqual(current.cacheKey(), key)
                self.assertEqual(current.pixelColor(current.width() // 2, current.height() // 2), center)
                window._scene_control_ink()
                self.assertEqual(window._scene_control_ink_cache, ink_target)
                self.assertEqual(window.visibility.progress, 1.)
                self.assertEqual(window._control_reveal_at(time.monotonic()), 1.)
                self.render()

    def test_wide_settle_refreshes_visible_background_without_unused_lyric_veil(self):
        self.warm(900)
        window = self.window
        self.assertFalse(window.narrow)
        prepares = window.background_veil.prepare_count
        with patch.object(window.veil, 'prepare', side_effect=AssertionError('unused wide lyric veil prepared')):
            window.resize(896, 640)
            self.render()
            self.wait_settle()
            self.render()
        self.assertEqual(window.background_veil.prepare_count, prepares + 1)


if __name__ == '__main__':
    unittest.main()
