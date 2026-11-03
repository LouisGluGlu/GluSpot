"""t160: shared lyric pixels and prepared veil survive live width changes."""
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

    def test_a4_snapshot_is_column_only_dpr_aware_and_stable_across_widths(self):
        window = self.window
        window.resize(1096, 640)
        self.render()
        snapshot = window._lyric_resize_snapshot
        source = window._lyric_resize_source_rect
        self.assertFalse(snapshot.isNull())
        self.assertTrue(QRectF(window.rect()).contains(source))
        self.assertLess(source.width(), 1100)
        self.assertEqual(snapshot.devicePixelRatio(), window.devicePixelRatioF())
        self.assertAlmostEqual(snapshot.width() / snapshot.devicePixelRatio(), source.width(), delta=1)
        self.assertAlmostEqual(snapshot.height() / snapshot.devicePixelRatio(), source.height(), delta=1)
        key = snapshot.cacheKey()
        for width in (1080, 1040, 1000, 1080):
            window.resize(width, 640)
            self.render()
            self.assertEqual(window._lyric_resize_snapshot.cacheKey(), key)

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

    def test_duet_snapshot_keeps_the_cached_two_column_geometry(self):
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
        snapshot = QImage(round(40 * ratio), round(40 * ratio),
                          QImage.Format.Format_ARGB32_Premultiplied)
        snapshot.setDevicePixelRatio(ratio)
        snapshot.fill(Qt.GlobalColor.white)
        output = QImage(round(100 * ratio), round(100 * ratio), snapshot.format())
        output.setDevicePixelRatio(ratio)
        output.fill(Qt.GlobalColor.transparent)
        def live(painter, _rect, **_kwargs):
            painter.fillRect(column, Qt.GlobalColor.white)
        with patch.object(self.window, '_lyric_resize_snapshot', snapshot), \
                patch.object(self.window, '_lyric_resize_source_rect', column), \
                patch.object(self.window, '_lyric_resize_crossfade_at', 100.), \
                patch.object(self.window, '_lyric_resize_target_rect', return_value=column), \
                patch.object(self.window, '_paint_live_lyric_column', side_effect=live), \
                patch('local.music_lyric_resize.time.monotonic', return_value=100.07):
            with QPainter(output) as painter:
                self.window._paint_lyric_column(painter, QRectF(self.window.rect()))
        # Premultiplied endpoint addition allows one raster rounding unit;
        # sequential SourceOver at half opacity would drop alpha to about191.
        center = output.pixelColor(round(40 * ratio), round(40 * ratio))
        self.assertGreaterEqual(center.alpha(), 254)
        self.assertGreaterEqual(center.red(), 254)
        self.assertEqual(output.pixelColor(round(10 * ratio), round(10 * ratio)).alpha(), 0)

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
