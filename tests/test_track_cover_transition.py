"""Shared 640 ms projected covers on the actual responsive release surface."""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QTransform
from PySide6.QtWidgets import QApplication

from api.models import IslandVisualData
from api.providers import EmptyIslandDataProvider
from _demos.narrow3.narrow_window import NarrowStudyWindow
from local.island_stack_visual import IslandStackVisual
from local.track_cover_transition import (
    ARTWORK_UPGRADE_SECONDS, TRACK_COVER_SECONDS, cover_transition_progress,
    paint_track_cover_flip,
)
from tests import test_island_track_transition as island_fixtures
from tests.test_music_surface_transition import _TransitionOwner
from tests.test_owned_playback_surface import _Provider


def image(color, side=128):
    art = QImage(side, side, QImage.Format.Format_RGB32)
    art.fill(QColor(color))
    with QPainter(art) as painter:
        painter.fillRect(QRectF(side * .1, side * .2, side * .15, side * .25), QColor('yellow'))
    return art


def alpha_width(frame):
    rgba = frame.convertToFormat(QImage.Format.Format_RGBA8888)
    alpha = np.frombuffer(rgba.constBits(), np.uint8).reshape(rgba.height(), rgba.width(), 4)[:, :, 3]
    return int(np.count_nonzero(np.any(alpha > 100, axis=0)))


class SharedCoverProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_exact_clock_and_faces_close_to_an_edge_without_mirroring(self):
        self.assertEqual(TRACK_COVER_SECONDS, .640)
        self.assertAlmostEqual(cover_transition_progress(10., 10.32), .5)
        self.assertAlmostEqual(cover_transition_progress(10., 10.64), 1.)
        widths = []
        for progress in (0., .35, .5, .65, 1.):
            frame = QImage(120, 120, QImage.Format.Format_ARGB32_Premultiplied)
            frame.fill(Qt.GlobalColor.transparent)
            with QPainter(frame) as painter:
                def face(outgoing):
                    painter.drawImage(QRectF(20, 20, 80, 80), image('#c02030' if outgoing else '#126ec0'))
                paint_track_cover_flip(painter, QRectF(20, 20, 80, 80), 8., progress, face)
            widths.append(alpha_width(frame))
            if progress == 1.:
                self.assertGreater(frame.pixelColor(32, 42).red(), 170)
                self.assertLess(frame.pixelColor(32, 42).blue(), 80)
        self.assertEqual(widths[2], 0)
        self.assertLess(widths[1], widths[0])
        self.assertLess(widths[3], widths[4])

    def test_shared_projection_preserves_parent_transform_clip_and_opacity(self):
        frame = QImage(160, 160, QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(Qt.GlobalColor.transparent)
        with QPainter(frame) as painter:
            painter.translate(7, 11)
            painter.setOpacity(.6)
            painter.setClipRect(QRectF(0, 0, 50, 120))
            transform = QTransform(painter.worldTransform())
            clip = QRectF(painter.clipBoundingRect())
            paint_track_cover_flip(painter, QRectF(20, 20, 80, 80), 8., .28,
                                   lambda _old: painter.fillRect(QRectF(20, 20, 80, 80), QColor('red')))
            self.assertEqual(painter.worldTransform(), transform)
            self.assertEqual(painter.clipBoundingRect(), clip)
            self.assertAlmostEqual(painter.opacity(), .6)
        self.assertEqual(frame.pixelColor(65, 55).alpha(), 0)


class ReleaseSurfaceCoverTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_surface(self, narrow=False, size=(960, 540), fullscreen=False, lyricless=False):
        owner = _TransitionOwner()
        owner.data_provider = _Provider()
        owner.media_is_playing = False
        owner._music_windowed_preference = not fullscreen
        owner.media_duration_seconds = 180.
        owner.media_position_seconds = owner.lyric_last_sync_position = 31.
        owner.lyric_index, owner.lyric_changed_at = 3, 99.
        owner.media_capture_epoch_id = 1
        owner.enable_dual_line_lyrics = False
        owner.media_track_id = 'old'
        owner.album_art = image('#c02030')
        owner.album_art_source = 'fixture-original'
        if lyricless:
            owner.lyrics, owner.timed_lyrics = [], []
        window = NarrowStudyWindow(owner, 'B', 'left', live=True)
        window._timer.stop()
        window.study_timer.stop()
        window._audio_output_status_timer.stop()
        self.addCleanup(owner.close)
        self.addCleanup(window.close)
        self.addCleanup(window.shutdown)
        window._fullscreen = fullscreen
        window._explicit_width_target = (*size, narrow)
        window.resize(*size)
        window._sync_narrow_mode()
        window._finish_width_chrome()
        window._cancel_lyric_resize()
        window._finish_width_layout()
        window.narrow, window._width_layout_fraction = narrow, float(narrow)
        window.set_content_layout('cover' if lyricless else 'lyrics', animate=False)
        window._finish_content_layout()
        window._cover_layout_from = window._cover_layout_to = float(window._no_lyrics_cover_target())
        window.visibility.progress = 1.
        window.visibility.opacity.start = window.visibility.opacity.target = 1.
        window.visibility.opacity.duration = 0.
        window._control_reveal_from = window._control_reveal_target = 1.
        window._control_last_motion_at = 1000.
        window._track_visual = window._previous_track_visual = None
        with patch('local.music_surface_window.time.monotonic', return_value=100.):
            window._sync_artwork_palette(100.)
            window.refresh_controls(100.)
        return owner, window

    @staticmethod
    def switch(owner, window, identity='new', color='#126ec0', now=101.):
        owner.media_track_id, owner.album_art = identity, image(color)
        with patch('local.music_surface_window.time.monotonic', return_value=now):
            window._sync_artwork_palette(now)

    @staticmethod
    def cover_frame(window, now):
        frame = QImage(120, 120, QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(Qt.GlobalColor.transparent)
        with QPainter(frame) as painter:
            window._paint_track_cover_image(painter, QRectF(20, 20, 80, 80), 8., now, light=False)
        return frame

    def test_all_release_poses_paint_the_same_640ms_flip_in_lyrics_and_lyricless_modes(self):
        for narrow, size, fullscreen in ((False, (960, 540), False), (True, (510, 650), False),
                                         (False, (1280, 720), True)):
            for lyricless in (False, True):
                with self.subTest(narrow=narrow, fullscreen=fullscreen, lyricless=lyricless):
                    owner, window = self.make_surface(narrow, size, fullscreen, lyricless)
                    self.switch(owner, window)
                    self.assertEqual(window._track_cover_started_at, 101.)
                    for elapsed in (0., .32, .64):
                        now = 101. + elapsed
                        frame = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
                        frame.fill(Qt.GlobalColor.transparent)
                        with patch('local.music_surface_window.time.monotonic', return_value=now), \
                                patch.object(window, '_paint_track_cover_image', wraps=window._paint_track_cover_image) as painted:
                            window.refresh_controls(now)
                            window.render(frame)
                        self.assertGreater(painted.call_count, 0, 'The actual release painter must use the shared transition')
                        self.assertAlmostEqual(window._track_cover_progress(now), elapsed / .64)
                        width = alpha_width(self.cover_frame(window, now))
                        self.assertEqual(width, 0 if elapsed == .32 else 80)
                    self.assertEqual(window.narrow, narrow)

    def test_same_track_artwork_upgrade_crossfades_without_restarting_flip(self):
        owner, window = self.make_surface()
        self.switch(owner, window)
        self.cover_frame(window, 101.64)
        started = window._track_cover_started_at
        before = self.cover_frame(window, 102.).pixelColor(84, 84)
        owner.album_art = image('#088f70', 512)
        with patch('local.music_surface_window.time.monotonic', return_value=102.):
            window._sync_artwork_palette(102.)
        colors = [self.cover_frame(window, 102. + elapsed).pixelColor(84, 84)
                  for elapsed in (0., ARTWORK_UPGRADE_SECONDS / 2, ARTWORK_UPGRADE_SECONDS)]
        self.assertEqual(colors[0], before)
        self.assertGreater(colors[1].green(), colors[0].green())
        self.assertLess(colors[1].green(), colors[2].green())
        self.assertEqual(window._track_cover_started_at, started)
        self.assertEqual(window._track_cover_progress(102.1), 1.)

    def test_rapid_switch_uses_bounded_visible_pose_and_paused_clock_keeps_committing(self):
        owner, window = self.make_surface(True, (510, 650))
        self.switch(owner, window)
        expected = window._track_cover_snapshot(101.2)
        self.switch(owner, window, 'third', '#088f70', 101.2)
        self.assertEqual(window._track_cover_previous, expected)
        self.assertLessEqual(window._track_cover_previous.width(), 192)
        self.assertFalse(owner.media_is_playing)
        before = window._diagnostics['commits']
        for now in (101.23, 101.30):
            with patch('local.music_surface_window.time.monotonic', return_value=now):
                window.tick()
        self.assertGreaterEqual(window._diagnostics['commits'], before + 2)


class IslandArtworkUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.visual = IslandStackVisual(EmptyIslandDataProvider())
        self.visual.timer.stop()
        self.visual._u = float
        self.visual._navigation_surface_blocks_island = lambda: False
        self.visual._sync_timed_lyrics = Mock()
        self.addCleanup(self.visual.close)

    def apply(self, color, now, *, identity='same', initial=False):
        track = island_fixtures.IslandTrackTransitionTests.track(identity, color)
        with patch('local.island_stack_visual.time.monotonic', return_value=now):
            self.visual._apply_visual_data(IslandVisualData(track, (), ()), initial=initial)

    def render(self, now):
        frame = QImage(120, 120, QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(Qt.GlobalColor.transparent)
        self.visual._island_glow_paint_now = now
        with QPainter(frame) as painter, patch('local.island_stack_visual.time.monotonic', return_value=now):
            self.visual._paint_interactive_music_cover(painter, QRectF(20, 20, 80, 80), 8.)
        return frame

    def test_actual_visible_island_cover_quietly_blends_same_track_upgrade_and_uses_existing_clock(self):
        self.apply('#bb1830', 10., initial=True)
        self.apply('#126ec0', 11.)
        self.assertEqual(self.visual._island_track_transition_progress(11.1), 1.)
        colors = [self.render(11. + elapsed).pixelColor(84, 84) for elapsed in (0., .12, .24)]
        self.assertGreater(colors[0].red(), colors[0].blue())
        self.assertGreater(colors[1].blue(), colors[0].blue())
        self.assertLess(colors[1].blue(), colors[2].blue())
        self.assertTrue(self.visual._active_visual_animation_plan(11.1, include_media=False).active)

    def test_track_change_during_upgrade_snapshots_the_visible_blend(self):
        self.apply('#bb1830', 10., initial=True)
        self.apply('#126ec0', 11.)
        self.visual._island_glow_paint_now = 11.12
        expected = self.visual._island_track_cover_snapshot(11.12)
        color = expected.pixelColor(expected.width() * 4 // 5, expected.height() * 4 // 5)
        self.assertGreater(color.red(), 30)
        self.assertGreater(color.blue(), 30)
        self.apply('#088f70', 11.12, identity='next')
        self.assertEqual(self.visual._island_track_previous_cover, expected)
