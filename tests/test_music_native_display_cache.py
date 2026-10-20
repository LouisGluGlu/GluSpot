"""Direct surface drawing preserves pixels and bounded control prewarm."""
from __future__ import annotations

import math
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_fullscreen import _Owner


class MusicNativeDisplayCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _Owner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(640, 480)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def target(self, dpr=1., size=(640, 480)):
        image = QImage(math.ceil(size[0] * dpr), math.ceil(size[1] * dpr), QImage.Format.Format_RGB32)
        image.setDevicePixelRatio(dpr)
        image.fill(QColor('#202124'))
        return image

    def test_layout_fit_reuses_shaping_but_rechecks_size_font_and_device(self):
        original = self.window._surface_font
        with patch.object(self.window, '_surface_font', wraps=original) as shaped:
            for size, families, dpr in (((330, 400), ('Segoe UI',), 1.),
                                        ((960, 540), ('Segoe UI',), 1.),
                                        ((960, 540), ('Microsoft YaHei UI',), 1.),
                                        ((960, 540), ('Microsoft YaHei UI',), 2.),
                                        ((1920, 1080), ('Segoe UI',), 2.)):
                self.window.resize(*size)
                with patch.object(self.window, 'SURFACE_FONT_FAMILIES', families), \
                        patch.object(self.window, 'devicePixelRatioF', return_value=dpr):
                    scale = self.window._surface_scale()
                    column = self.window._content_column_geometry()[2]
                    glyphs = QFontMetricsF(original(self.window.LYRIC_PIXEL_SIZE * scale,
                                                   QFont.Weight.Bold)).horizontalAdvance('音乐正在播放')
                    expected = column.width() < max(180., glyphs, 190. * scale)
                    before = shaped.call_count
                    self.assertEqual(self.window._content_layout_is_narrow(), expected)
                    for _ in range(20):
                        self.assertEqual(self.window._content_layout_is_narrow(), expected)
                    self.assertEqual(shaped.call_count - before, 1)

    def test_direct_background_matches_field_at_dpr_one_and_two_and_marks_actual_rebuilds(self):
        for dpr in (1., 2.):
            with self.subTest(dpr=dpr):
                actual = self.target(dpr)
                rect = QRectF(0, 0, 640, 480)
                with QPainter(actual) as painter:
                    self.window._paint_background(painter, rect, QColor('#a83c71'), QColor('#237bac'), dpr)
                self.assertTrue(self.window._background_rebuilt_this_paint)
                reference = self.target(dpr)
                with QPainter(reference) as painter:
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                    painter.drawImage(rect, self.window._background_cache)
                self.assertEqual(actual, reference)
                cached = self.window._background_cache.cacheKey()
                with QPainter(actual) as painter:
                    self.window._paint_background(painter, rect, QColor('#a83c71'), QColor('#237bac'), dpr + .01)
                self.assertFalse(self.window._background_rebuilt_this_paint)
                self.assertEqual(self.window._background_cache.cacheKey(), cached)
                # The renderer itself may return its already cached QImage.
                # A render call is not evidence of a newly generated raster.
                same_source = QImage(self.window._background_cache)
                with patch.object(self.window._album_flow_background, 'render',
                                  return_value=same_source) as render:
                    with QPainter(actual) as painter:
                        self.window._paint_background(painter, rect, QColor('#a83c71'),
                                                      QColor('#237bac'), dpr + 101.)
                render.assert_called_once()
                self.assertFalse(self.window._background_rebuilt_this_paint)
                self.assertEqual(self.window._background_cache.cacheKey(), cached)
                # A late partial paint holds the complete displayed field;
                # elapsed time alone must not mark it as rebuilt.
                with QPainter(actual) as painter:
                    self.window._paint_background(painter, rect, QColor('red'), QColor('blue'),
                                                  dpr + 100., advance_frame=False)
                self.assertFalse(self.window._background_rebuilt_this_paint)
                self.assertEqual(self.window._background_cache.cacheKey(), cached)

    def test_ready_background_waits_for_full_damage_then_bypasses_old_request_interval(self):
        rect = QRectF(0, 0, 640, 480)
        colors = (QColor('#a83c71'), QColor('#237bac'))
        target = self.target()
        with QPainter(target) as painter:
            self.window._paint_background(painter, rect, *colors, 10.)
        old_key = self.window._background_cache.cacheKey()
        renderer = self.window._album_flow_background
        # A completed pixel job replaces the renderer's accepted frame before
        # the next ambient request. Partial damage must retain the old scene.
        ready = renderer._frame.copy()
        ready.fill(QColor('#3287a9'))
        renderer._frame = ready
        renderer._frame_at = 10.01
        with QPainter(target) as painter:
            self.window._paint_background(painter, rect, *colors, 10.01, advance_frame=False)
        self.assertEqual(self.window._background_cache.cacheKey(), old_key)
        with QPainter(target) as painter:
            self.window._paint_background(painter, rect, *colors, 10.01)
        self.assertEqual(self.window._background_cache.cacheKey(), ready.cacheKey())
        self.assertIsNotNone(renderer.contrast_backdrop(self.window._background_cache))

    def test_direct_background_honors_scaled_snapshot_and_initial_partial_build(self):
        actual = self.target(1., (1280, 960))
        with QPainter(actual) as painter:
            painter.scale(2., 2.)
            self.window._paint_background(painter, QRectF(0, 0, 640, 480),
                                          QColor('#a83c71'), QColor('#237bac'), 1., advance_frame=False)
        # Even a partial request must construct the first missing source.
        self.assertTrue(self.window._background_rebuilt_this_paint)
        reference = self.target(1., (1280, 960))
        with QPainter(reference) as painter:
            painter.scale(2., 2.)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(QRectF(0, 0, 640, 480), self.window._background_cache)
        self.assertEqual(actual, reference)

    def test_rebuilding_background_cannot_starve_control_prewarm_or_change_direct_pixels(self):
        self.window._reset_control_idle(now=50.)
        bounds = QRectF(30, 20, 80, 40)
        draws = []

        def draw(painter):
            draws.append(painter.deviceTransform().m11())
            painter.fillRect(bounds, QColor('#b34e65'))

        def paint():
            target = self.target(2.)
            with QPainter(target) as painter:
                self.window._paint_control_layer(
                    painter, bounds, 1., QPointF(), ('fixture',), draw,
                    prewarm_default=(('fixture-default',), draw),
                )
            return target

        with patch('local.music_surface_window.time.monotonic', return_value=50.), \
                patch.object(self.window, '_maybe_prewarm_control_blur') as prewarm:
            self.window._background_rebuilt_this_paint = True
            direct = paint()
            self.assertEqual(prewarm.call_count, 1)
            self.assertEqual(draws, [2.])
            self.assertEqual(len(self.window._control_layer_cache), 0)
            self.assertEqual(self.window._control_layer_cache_bytes, 0)
            self.window._background_rebuilt_this_paint = False
            ready = paint()
            self.assertEqual(prewarm.call_count, 2)
            self.assertEqual(draws, [2., 2.])
            self.assertEqual(direct, ready)
            paint()
            self.window._background_rebuilt_this_paint = True
            paint()
            self.assertEqual(prewarm.call_count, 4)
            self.assertEqual(draws, [2., 2., 2., 2.])
            self.assertEqual(len(self.window._control_layer_cache), 0)
            self.assertEqual(self.window._control_layer_cache_bytes, 0)

    def test_fully_visible_layer_draws_directly_at_target_dpr_and_shift(self):
        self.window._reset_control_idle(now=50.)
        bounds = QRectF(30, 20, 80, 40)
        shift = QPointF(9., 5.)
        draws = []

        def draw(painter):
            draws.append(painter.deviceTransform().m11())
            painter.fillRect(bounds, QColor('#b34e65'))

        with patch('local.music_surface_window.time.monotonic', return_value=50.):
            for dpr in (1., 2.):
                with self.subTest(dpr=dpr):
                    actual, reference = self.target(dpr), self.target(dpr)
                    with QPainter(actual) as painter:
                        self.window._paint_control_layer(painter, bounds, 1., shift, ('fixture',), draw)
                    with QPainter(reference) as painter:
                        painter.translate(shift)
                        painter.fillRect(bounds, QColor('#b34e65'))
                    self.assertEqual(actual, reference)
        self.assertEqual(draws, [1., 2.])
        self.assertEqual(len(self.window._control_layer_cache), 0)
        self.assertEqual(self.window._control_layer_cache_bytes, 0)

    def test_direct_layer_follows_current_geometry(self):
        self.window._reset_control_idle(now=50.)
        target = self.target()
        bounds = QRectF(20, 20, 50, 30)

        def draw(painter):
            painter.fillRect(bounds, QColor('red') if bounds.x() < 100 else QColor('blue'))

        with patch('local.music_surface_window.time.monotonic', return_value=50.):
            with QPainter(target) as painter:
                self.window._paint_control_layer(painter, bounds, 1., QPointF(), ('fixture',), draw)
            bounds.moveLeft(160)
            with QPainter(target) as painter:
                self.window._paint_control_layer(painter, bounds, 1., QPointF(), ('fixture',), draw)
        self.assertEqual(target.pixelColor(45, 35), QColor('red'))
        self.assertEqual(target.pixelColor(185, 35), QColor('blue'))

    def test_native_symbols_preserve_antialiasing_and_fractional_device_phase(self):
        self.window._reset_control_idle(now=50.)
        bounds = QRectF(30.25, 20.5, 40.1, 40.3)
        shift = QPointF(.13, .41)
        path = QPainterPath()
        path.addRoundedRect(bounds.adjusted(6.3, 5.1, -7.7, -8.5), 3.2, 4.1)
        for dpr in (1., 1.25, 2.):
            for antialias in (False, True):
                with self.subTest(dpr=dpr, antialias=antialias):
                    images = []
                    for through_helper in (False, True):
                        image = QImage(math.ceil(160*dpr), math.ceil(100*dpr),
                                       QImage.Format.Format_ARGB32_Premultiplied)
                        image.setDevicePixelRatio(dpr)
                        image.fill(Qt.GlobalColor.transparent)
                        with QPainter(image) as painter, \
                                patch('local.music_surface_window.time.monotonic', return_value=50.):
                            painter.translate(.21, .35)
                            painter.setRenderHint(QPainter.RenderHint.Antialiasing, antialias)
                            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                            draw = lambda layer: layer.fillPath(path, QColor('white'))
                            if through_helper:
                                self.window._paint_control_layer(painter, bounds, 1., shift, ('phase',), draw)
                            else:
                                painter.translate(shift)
                                draw(painter)
                        images.append(image)
                    self.assertEqual(images[0], images[1])


if __name__ == '__main__':
    unittest.main()
