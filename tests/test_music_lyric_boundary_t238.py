"""Terminal lyric state and current artwork fence every resize endpoint."""
from __future__ import annotations

import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from _demos.narrow3.narrow_window import NarrowStudyWindow
from tests.test_music_surface_transition import _TransitionOwner


class LyricBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.owner.media_is_playing = False
        self.owner.media_duration_seconds = 120.
        self.owner.media_position_seconds = self.owner.lyric_last_sync_position = 12.
        self.owner.lyric_index = 2
        self.owner.lyric_changed_at = time.monotonic()
        self.owner.enable_dual_line_lyrics = False
        self.owner.album_art = QImage(128, 128, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor('#6094a6'))
        self.window = NarrowStudyWindow(self.owner, 'B', 'left', live=True)
        self.window.hints = False
        self.window.resize(960, 540)
        self.window.show()
        self.app.processEvents()
        for name in ('_timer', 'study_timer', '_audio_output_status_timer'):
            getattr(self.window, name).stop()

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.owner.close()
        self.app.processEvents()

    def publish(self, state):
        self.window._cancel_lyric_resize()
        self.owner.media_track_id = state
        if state == 'loaded':
            self.owner.timed_lyrics = [(i * 5., f'Readable verse {i}') for i in range(8)]
            self.owner.lyrics = [row for _, row in self.owner.timed_lyrics]
        else:
            self.owner.timed_lyrics = []
            self.owner.lyrics = ['加载歌词中' if state == 'loading' else '纯音乐，请欣赏']
        now = time.monotonic()
        self.window.set_content_layout('lyrics', animate=False)
        self.window._cover_layout_from = self.window._cover_layout_to = 0.
        self.window._update_lyric_loading_state(now - 2.)
        self.window._update_lyric_loading_state(now)
        self.window._sync_no_lyrics_content_layout(now)
        self.finish()
        self.render()
        if state == 'loading':
            # The first paint publishes the narrow mount's display payload.
            # Start the observation after the ordinary one-second delay.
            self.window._lyric_loading_started_at = time.monotonic() - 2.
            self.window._update_lyric_loading_state(time.monotonic())

    def finish(self):
        self.window._layout_animation.stop()
        self.window._finish_content_layout()
        for _ in range(5):
            if not self.window._width_transition or not self.window._width_transition.active:
                break
            self.window._width_chrome_animation.setCurrentTime(self.window._width_chrome_animation.duration())
        self.window._finish_width_layout()
        self.window._cancel_lyric_resize()

    def render(self):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.window.render(image)
        return image

    def test_three_states_wide_narrow_resize_drag_and_settle(self):
        for state in ('instrumental', 'loaded', 'loading'):
            self.publish(state)
            with patch.object(self.window, '_paint_lyric_loading', wraps=self.window._paint_lyric_loading) as loader, \
                 patch.object(self.window, '_paint_lyric_cover_shade', wraps=self.window._paint_lyric_cover_shade) as shade, \
                 patch.object(self.window, '_paint_live_lyric_column', wraps=self.window._paint_live_lyric_column) as ink:
                for width in (960, 883, 690, 610, 690, 883, 960):
                    with self.subTest(state=state, width=width):
                        with patch.object(self.window, '_available_geometry', return_value=QRect(0, 0, 2400, 1600)):
                            self.window._begin_width_resize()
                        self.window.resize(width, 540)
                        self.window.move(25 + width // 10, 40)
                        self.render()
                        self.window._end_width_resize()
                        if state == 'instrumental':
                            self.assertEqual(self.window._effective_content_layout(), 'cover')
                            self.assertFalse(self.window._lyric_resize_frozen)
                            self.assertTrue(self.window._lyric_clip_rect.isEmpty())
                self.finish()
                self.render()
                if state != 'loading':
                    loader.assert_not_called()
                shade.assert_not_called()
                if state == 'instrumental':
                    ink.assert_not_called()
                else:
                    ink.assert_called()
                if state == 'loading':
                    loader.assert_called()

    def test_live_and_frozen_lyric_layers_exclude_this_frames_cover_at_every_width(self):
        for state in ('loaded', 'loading'):
            self.publish(state)
            for width in (960, 883, 690, 610):
                self.window.resize(width, 540)
                self.finish()
                self.render()
                column = self.window._content_column_geometry()[2]
                # Simulate the cover crossing the former column during a
                # layout/width handoff, independently of its old geometry.
                cover = QRectF(column.left() + 10., 90., min(130., column.width() - 20.), 200.)
                for frozen in (False, True):
                    with self.subTest(state=state, width=width, frozen=frozen):
                        if frozen:
                            self.window._lyric_resize_frozen = True
                            self.window._lyric_resize_started_at = time.monotonic() - .3
                            self.window._lyric_resize_source_rect = QRectF(self.window.rect())
                            snapshot = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
                            snapshot.fill(Qt.GlobalColor.white)
                            self.window._lyric_resize_snapshot = snapshot
                        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
                        image.fill(Qt.GlobalColor.transparent)
                        with QPainter(image) as painter:
                            self.window._paint_lyric_column(painter, QRectF(self.window.rect()), painted_cover=cover)
                        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
                        alpha = np.frombuffer(rgba.constBits(), np.uint8).reshape(rgba.height(), rgba.width(), 4)[:, :, 3]
                        interior = cover.adjusted(2., 2., -2., -2.).toAlignedRect()
                        self.assertEqual(int(alpha[interior.top():interior.bottom(), interior.left():interior.right()].max()), 0)
                        self.assertGreater(int(alpha.max()), 0, 'The test must actually draw lyric ink outside artwork')
                        self.window._cancel_lyric_resize()

    def test_terminal_result_cancels_inflight_snapshot_before_next_resize_paint(self):
        self.publish('loaded')
        self.render()
        self.window.resize(883, 540)
        self.render()
        self.assertTrue(self.window._lyric_resize_frozen)
        self.owner.timed_lyrics = []
        self.owner.lyrics = ['暂无歌词']
        self.window._sync_no_lyrics_content_layout(time.monotonic())
        with patch.object(self.window, '_paint_lyric_loading') as loader, \
             patch.object(self.window, '_paint_live_lyric_column') as ink:
            for width in (690, 610, 883):
                self.window.resize(width, 540)
                self.render()
        self.assertEqual(self.window._effective_content_layout(), 'cover')
        self.assertTrue(self.window._lyric_resize_snapshot.isNull())
        self.assertFalse(self.window._lyric_resize_frozen)
        loader.assert_not_called()
        ink.assert_not_called()

    def test_inside_cover_lyrics_and_shade_are_suppressed_during_width_motion(self):
        self.publish('loaded')
        self.window.set_content_layout('cover_lyrics', animate=False)
        self.render()
        self.window.resize(883, 540)
        with patch.object(self.window, '_paint_live_lyric_column') as ink, \
             patch.object(self.window, '_paint_lyric_cover_shade') as shade:
            self.render()
            ink.assert_not_called()
            shade.assert_not_called()


if __name__ == '__main__':
    unittest.main()
