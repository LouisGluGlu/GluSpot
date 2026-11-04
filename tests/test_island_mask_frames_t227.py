"""Per-frame native mask and persistent backing-store regression for t227."""
from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.island_stack_visual import IslandStackVisual, SpringRect


class IslandMaskFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        IslandStackVisual.load_embedded_lyric_fonts()

    def make_island(self):
        w = IslandStackVisual()
        w.timer.stop()
        self.addCleanup(w.close)
        w.resize(960, 180)
        w.location_mode, w.finder_mode = 'finder', True
        w.finder_bar_rect = QRectF(0, 0, 960, 32)
        w.finder_bar_style = 'capsule'
        w._finder_bar_available = lambda: True
        w.music_playing, w.music_auto_detect = True, False
        w.expanded = False
        w.track_title, w.artist, w.media_track_id = 'Mask regression', 'GluSpot', 'frame-a'
        w.media_is_playing = True
        w.media_duration_seconds = 30.
        w.media_position_source = 'local-seek'
        w._scene_transition_params = lambda now: (1., 1., False, 0.)
        w._paint_taskbar_drop_hint = lambda painter: None
        w._lyric_presentation_store = None
        w._island_lyric_miss_text = lambda: None
        w._is_instrumental_track = lambda: False
        w._empty_music_text_active = lambda: False
        w.setMask = lambda region: None  # offscreen cannot apply a native HRGN
        w.clearMask = lambda: self.fail('An expand/collapse must never unmask the canvas')
        w.album_art = QImage(80, 80, QImage.Format.Format_ARGB32_Premultiplied)
        w.album_art.fill(Qt.GlobalColor.darkRed)
        w.lyrics = ['Short line', 'A much longer line testing complete shell growth', 'Short again']
        w.timed_lyrics = [(0., w.lyrics[0]), (3., w.lyrics[1]), (4., w.lyrics[2])]
        w.timed_lyric_units = [()] * 3
        w.timed_lyric_timing_kinds = ['line'] * 3
        return w

    def test_every_frame_uses_current_shell_and_clears_old_transparent_pixels(self):
        for dpr, available in ((1, True), (2, True), (1, False), (2, False)):
            with self.subTest(dpr=dpr, finder_available=available):
                w = self.make_island()
                w._finder_bar_available = lambda: available
                buffer = QImage(w.width()*dpr, w.height()*dpr, QImage.Format.Format_ARGB32_Premultiplied)
                buffer.setDevicePixelRatio(dpr)
                buffer.fill(Qt.GlobalColor.transparent)
                observed = {}
                original = w._paint_music

                def music(painter, rect, expanded):
                    observed['shell'] = QRectF(rect)
                    observed['mask'] = QRectF(w.last_mask_rect)
                    original(painter, rect, expanded)

                class PersistentPainter(QPainter):
                    def __init__(self, device):
                        super().__init__(buffer if device is w else device)

                w._paint_music = music
                for frame in range(330):
                    now = 100. + frame / 60.
                    position = frame / 60.
                    w.media_position_seconds = w.media_position_observed_seconds = position
                    w.media_position_sampled_at = w.media_position_sync_at = now
                    w.last_pointer_move_at, w.mask_deferred_until = now, now + 1.
                    if frame == 0:
                        w.last_frame_at = now - 1/60.
                        visual = w._build_visuals()[0]
                        w.motions[visual.key] = SpringRect(visual.rect)
                        w.motions[visual.key].opacity = 1.
                    with patch('local.island_stack_visual.sys.platform', 'win32'), \
                            patch('local.island_stack_visual.time.monotonic', return_value=now):
                        if frame in (30, 120):
                            w._set_expanded(frame == 30)
                        if frame == 280:
                            w.media_track_id = 'frame-b'
                            w.track_title = 'Track switch'
                        w._sync_timed_lyrics(now)
                        w._reserve_interaction_mask_bounds()
                        queued_mask = QRectF(w.last_mask_rect)
                        with patch('local.island_stack_visual.QPainter', PersistentPainter):
                            w.paintEvent(None)
                    shell, mask = observed['shell'], observed['mask']
                    self.assertTrue(queued_mask.contains(shell), (dpr, frame, shell, queued_mask))
                    self.assertEqual(mask, queued_mask, (dpr, frame))
                    margin = max(1., 2*w._u(1.))
                    self.assertEqual(mask, shell.adjusted(-margin, -margin, margin, margin))
                    self.assertIsNone(w.group_frame_mask_path, (dpr, frame))
                    # The same raster is reused across all frames; painting must
                    # erase old cover/title/buttons, even outside the new shell.
                    outside = QImage(buffer)
                    with QPainter(outside) as painter:
                        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                        painter.fillRect(mask, Qt.GlobalColor.transparent)
                    self.assertFalse(any(bytes(outside.constBits())[3::4]), (dpr, frame))
                    self.assertTrue(any(bytes(buffer.constBits())[3::4]), (dpr, frame))

    def test_debug_flag_reports_window_mask_and_painted_bounds(self):
        w = self.make_island()
        output = io.StringIO()
        with patch.dict(os.environ, {'GLUSPOT_MASK_DEBUG': '1'}), contextlib.redirect_stderr(output):
            w._island_lyric_frame_at = 100.
            w._log_mask_frame([QRectF(10, 20, 30, 40)])
        record = json.loads(output.getvalue().split('[GluSpot mask] ', 1)[1])
        self.assertEqual(record['painted'], [[10., 20., 30., 40.]])
        self.assertEqual(len(record['window']), 4)
        self.assertEqual(len(record['mask']), 4)
