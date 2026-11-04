"""Finder runtime exits cannot replay captured shells or a queued Finder pose."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication

from api.providers import EmptyIslandDataProvider, empty_visual_data
from local.island_stack_visual import IslandStackVisual


class FinderExitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        IslandStackVisual.load_embedded_lyric_fonts()

    def make_island(self, dpr):
        w = IslandStackVisual(EmptyIslandDataProvider(empty_visual_data()))
        self.addCleanup(w.close)
        screen = self.app.primaryScreen()
        geometry = screen.geometry()
        w.resize(geometry.width(), 420)
        w.music_playing, w.music_auto_detect = True, False
        w.track_title, w.artist, w.media_track_id = 'Finder exit', 'GluSpot', 'finder-exit'
        w.media_is_playing = True
        w.media_duration_seconds = 30.
        w.media_position_source = 'local-seek'
        w._lyric_presentation_store = None
        w._island_lyric_miss_text = lambda: None
        w._is_instrumental_track = lambda: False
        w._empty_music_text_active = lambda: False
        w._detect_external_scene_suppression = lambda: (False, '')
        w._paint_taskbar_drop_hint = lambda painter: None
        w._force_super_topmost = lambda: None
        w.album_art = QImage(80, 80, QImage.Format.Format_ARGB32_Premultiplied)
        w.album_art.fill(QColor('#b05663'))
        w.lyrics = ['Short line', 'A much longer line testing the growing Finder shell']
        w.timed_lyrics = [(0., w.lyrics[0]), (.2, w.lyrics[1])]
        w.timed_lyric_units = [(), ()]
        w.timed_lyric_timing_kinds = ['line', 'line']
        rect = [geometry.left(), geometry.top(), geometry.left() + geometry.width(), geometry.top() + 32]
        monitor = [geometry.left(), geometry.top(), geometry.left() + geometry.width(), geometry.top() + geometry.height()]
        bar = dict(hwnd=1, pid=2, enabled=True, visible=True, window_visible=True,
                   physical_rect=rect, physical_resting_rect=rect,
                   physical_monitor_rect=monitor, monitor_name=screen.name() or 'BOX-fixture')
        native = {'region': QRegion()}
        # Offscreen cannot install an HWND region. Record the actual region
        # requests while letting QWidget.grab() use Qt's real paint path.
        w.setMask = lambda region: native.update(region=QRegion(region))
        w.clearMask = lambda: native.update(region=QRegion())
        with patch('local.island_stack_visual.read_finder_material', return_value=w.finder_material), \
                patch('local.island_stack_visual.time.monotonic', return_value=100.):
            w.set_mydockfinder_running(True, notify=False)
            w.set_finder_bar_presence([bar], notify=False)
            w._settle_island_return_motion(100.)
            w.show()
            self.app.processEvents()
        w.timer.stop()
        self.assertTrue(w.isVisible())
        self.assertEqual(w.location_mode, 'finder')
        buffer = QImage(w.width() * dpr, w.height() * dpr, QImage.Format.Format_ARGB32_Premultiplied)
        buffer.setDevicePixelRatio(dpr)
        buffer.fill(Qt.GlobalColor.transparent)
        return w, bar, native, buffer

    def frame(self, w, native, buffer, now, *, clean=True):
        shells = []
        original = w._paint_music

        def music(painter, rect, expanded):
            shells.append(QRectF(rect))
            original(painter, rect, expanded)

        class PersistentPainter(QPainter):
            def __init__(self, device):
                super().__init__(buffer if device is w else device)

        with patch('local.island_stack_visual.time.monotonic', return_value=now), \
                patch('local.island_stack_visual.sys.platform', 'win32'):
            w.media_position_seconds = w.media_position_observed_seconds = now - 100.
            w.media_position_sampled_at = w.media_position_sync_at = now
            w._sync_timed_lyrics(now)
            w._reserve_interaction_mask_bounds()
            queued_mask = QRectF(w.last_mask_rect)
            with patch.object(w, '_paint_music', music), \
                    patch('local.island_stack_visual.QPainter', PersistentPainter):
                w.paintEvent(None)
        self.assertEqual(len(shells), 1)
        shell = shells[0]
        margin = max(1., 2. * w._u(1.))
        painted_bounds = shell.adjusted(-margin, -margin, margin, margin)
        if w.finder_mode:
            self.assertEqual(queued_mask, painted_bounds)
            self.assertEqual(w.last_mask_rect, queued_mask)
            self.assertEqual(native['region'].boundingRect(), painted_bounds.toAlignedRect())
        else:
            self.assertTrue(native['region'].isEmpty())
            self.assertFalse(w.interaction_mask_active)
            self.assertTrue(w.last_mask_rect.isEmpty())
        if clean:
            outside = QImage(buffer)
            with QPainter(outside) as painter:
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                painter.fillRect(painted_bounds, Qt.GlobalColor.transparent)
            self.assertFalse(any(bytes(outside.constBits())[3::4]), (now, shell))
            self.assertTrue(any(bytes(buffer.constBits())[3::4]), now)
        return shell

    def exit_runtime(self, w, now):
        with patch('local.island_stack_visual.time.monotonic', return_value=now):
            # Match LocalIslandApp._poll_mydockfinder_presence ordering.
            w.set_mydockfinder_running(False, notify=False)
            w.set_finder_bar_presence([], notify=False)
        self.assertEqual(w.location_mode, 'free')
        self.assertFalse(w.finder_mode)

    def assert_exit_settled(self, w):
        self.assertTrue(w.surface_transition_pixmap.isNull())
        self.assertIsNone(getattr(w, '_queued_island_frame', None))
        self.assertIsNone(w.group_frame_motion)
        self.assertIsNone(w.group_frame_mask_path)
        self.assertFalse(getattr(w, '_bar_scene_transition', False))
        self.assertEqual(getattr(w, '_bar_visibility_value', 1.), 1.)
        self.assertEqual(getattr(w, '_bar_visibility_target', 1.), 1.)
        self.assertEqual(getattr(w, '_bar_visibility_velocity', 0.), 0.)
        self.assertEqual(getattr(w, '_finder_material_transition_until', 0.), 0.)
        self.assertEqual(getattr(w, '_finder_dj_reveal_from', 0.), 0.)
        self.assertEqual(getattr(w, '_finder_dj_reveal_target', 0.), 0.)
        self.assertEqual(w.hover_bounce_until, 0.)
        self.assertEqual(w.stack_bounce_until, 0.)
        self.assertEqual(w.scene_transition_mode, '')
        visuals = w._build_visuals()
        self.assertEqual(set(w.motions), {v.key for v in visuals})
        for visual in visuals:
            motion = w.motions[visual.key]
            self.assertEqual(motion.rect, visual.rect)
            self.assertEqual((motion.vx, motion.vy, motion.vw, motion.vh), (0., 0., 0., 0.))

    def test_exit_mid_expansion_discards_queued_finder_pose_and_old_ink(self):
        for dpr in (1, 2):
            with self.subTest(dpr=dpr):
                w, bar, native, buffer = self.make_island(dpr)
                self.frame(w, native, buffer, 100.1)
                with patch('local.island_stack_visual.time.monotonic', return_value=100.12), \
                        patch('local.island_stack_visual.sys.platform', 'win32'):
                    w._set_expanded(True)
                    w._reserve_interaction_mask_bounds()
                self.assertIsNotNone(w._queued_island_frame)
                self.exit_runtime(w, 100.13)
                self.assert_exit_settled(w)
                for frame in range(24):
                    self.frame(w, native, buffer, 100.13 + frame / 60.)
                self.assertTrue(w.expanded)

    def test_rapid_enter_exit_never_recaptures_previous_transition(self):
        for dpr in (1, 2):
            with self.subTest(dpr=dpr):
                w, bar, native, buffer = self.make_island(dpr)
                self.frame(w, native, buffer, 100.1)
                for cycle in range(8):
                    now = 100.15 + cycle * .08
                    self.exit_runtime(w, now)
                    self.assert_exit_settled(w)
                    self.frame(w, native, buffer, now)
                    with patch('local.island_stack_visual.read_finder_material', return_value=w.finder_material), \
                            patch('local.island_stack_visual.time.monotonic', return_value=now + .03):
                        w.set_mydockfinder_running(True, notify=False)
                        w.set_finder_bar_presence([bar], notify=False)
                    self.assertTrue(w.surface_transition_pixmap.isNull())
                    self.frame(w, native, buffer, now + .04)
                self.exit_runtime(w, 100.9)
                self.assert_exit_settled(w)
                for frame in range(24):
                    self.frame(w, native, buffer, 100.9 + frame / 60.)

    def test_exit_expanded_keeps_one_current_expanded_shell(self):
        for dpr in (1, 2):
            with self.subTest(dpr=dpr):
                w, bar, native, buffer = self.make_island(dpr)
                with patch('local.island_stack_visual.time.monotonic', return_value=100.1):
                    w._set_expanded(True)
                for frame in range(40):
                    self.frame(w, native, buffer, 100.1 + frame / 60.)
                self.exit_runtime(w, 100.8)
                self.assert_exit_settled(w)
                self.assertTrue(w.expanded)
                for frame in range(24):
                    self.frame(w, native, buffer, 100.8 + frame / 60.)

    def test_exit_during_owner_hide_cancels_visibility_and_material_springs(self):
        w, bar, native, buffer = self.make_island(1)
        self.frame(w, native, buffer, 100.1)
        w.scene_suppressed = True
        w.scene_reason = 'finder-hidden'
        w.scene_transition_mode = 'hide'
        w._retarget_bar_visibility(False, 100.11)
        w._bar_visibility_opacity(100.14)
        w._finder_dj_reveal_from, w._finder_dj_reveal_target = 1., .6
        w._finder_material_transition_started = 100.1
        w._finder_material_transition_until = 100.55
        self.assertLess(w._bar_visibility_value, 1.)
        self.exit_runtime(w, 100.15)
        self.assert_exit_settled(w)
        self.assertFalse(w.scene_suppressed)
        for frame in range(24):
            self.frame(w, native, buffer, 100.15 + frame / 60.)

    def test_hidden_exit_discards_prepared_compact_width_frame(self):
        w, bar, native, buffer = self.make_island(1)
        self.frame(w, native, buffer, 100.1)
        with patch('local.island_stack_visual.time.monotonic', return_value=100.21), \
                patch('local.island_stack_visual.sys.platform', 'win32'):
            w.media_position_seconds = w.media_position_observed_seconds = .21
            w.media_position_sampled_at = w.media_position_sync_at = 100.21
            w._sync_timed_lyrics(100.21)
            w._reserve_interaction_mask_bounds()
        self.assertIsNotNone(w._queued_island_frame)
        # Hidden owners cannot grab a snapshot. This isolates queued geometry
        # invalidation from the visible snapshot regression above.
        w.hide()
        self.exit_runtime(w, 100.22)
        self.assertTrue(w.surface_transition_pixmap.isNull())
        self.assert_exit_settled(w)
        self.frame(w, native, buffer, 100.22)
