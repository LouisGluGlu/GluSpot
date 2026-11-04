"""Glass follows changed content in its sampling footprint and rests otherwise."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from local.music_control_glass import GlassControlRenderer
from tests import test_narrow_chrome as fixtures


class LiveGlassBackdropTests(unittest.TestCase):
    setUpClass = fixtures.NarrowChromeTests.__dict__['setUpClass']
    setUp = fixtures.NarrowChromeTests.setUp
    tearDown = fixtures.NarrowChromeTests.tearDown

    def scene(self, color):
        image = QImage(360, 640, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        return image

    def prepare(self):
        window = self.window
        window.resize(360, 640)
        window.narrow = True
        window._finish_width_chrome()
        window.visibility.opacity.start = window.visibility.opacity.target = 1.
        window.visibility.opacity.duration = 0.
        window.visibility.progress = 1.
        window._background_cache = self.scene('#1653a2')
        return window

    def test_dirty_composite_requests_present_without_progress_or_play_change(self):
        window = self.prepare()
        with patch.object(window, 'isVisible', return_value=True), \
                patch.object(window, '_paint_wide_lyrics_mounted'), \
                patch.object(window, 'paint_header'), \
                patch.object(window, '_track_cover_animating', return_value=False):
            window._refresh_chrome_backdrop(100.)
            before = window._surface_glass_backdrop()
            window._controls_key = ('unchanged playback and progress',)
            window._wide_lyrics_dirty = False
            window._background_cache = self.scene('#37a24a')
            window._refresh_chrome_backdrop(100.101)
            self.assertNotEqual(window._surface_glass_backdrop(), before)
            self.assertIsNone(window._controls_key)
            self.assertTrue(window._wide_lyrics_dirty)
            self.assertIs(window._clear_control_renderer.live_tint_scene, window._chrome_backdrop)

    def test_middle_content_damage_does_not_dirty_glass_sample_bands(self):
        window = self.prepare()
        ink = [0]
        def content(painter):
            painter.fillRect(0, 250, 360, 60, QColor('red' if ink[0] else 'blue'))
        with patch.object(window, 'isVisible', return_value=True), \
                patch.object(window, '_paint_wide_lyrics_mounted', side_effect=content), \
                patch.object(window, 'paint_header'), \
                patch.object(window, 'ink_position', side_effect=lambda: ink[0]), \
                patch.object(window, '_track_cover_animating', return_value=False):
            window._refresh_chrome_backdrop(100.)
            before = window._chrome_backdrop.cacheKey()
            ink[0] = 1
            window._controls_key = ('settled',)
            window._refresh_chrome_backdrop(100.101)
            self.assertEqual(window._chrome_backdrop.cacheKey(), before)
            self.assertEqual(window._controls_key, ('settled',))

    def test_static_scene_and_controls_stop_raster_work(self):
        window = self.prepare()
        def controls(painter, _layout, _opacity):
            painter.fillRect(0, 0, 360, 45, QColor('white'))
        with patch.object(window, 'isVisible', return_value=True), \
                patch.object(window, '_paint_wide_lyrics_mounted') as content, \
                patch.object(window, 'paint_header'), \
                patch.object(window, '_track_cover_animating', return_value=False), \
                patch.object(window, '_scene_control_ink', return_value=QColor('white')), \
                patch.object(window, 'paint_controls', side_effect=controls) as draw:
            for now in (100., 100.11, 100.5, 101., 102.):
                window._refresh_chrome_backdrop(now)
                window.refresh_controls(now)
            self.assertEqual(content.call_count, 1)
            self.assertEqual(draw.call_count, 1)

    def test_content_changes_are_throttled_to_existing_ten_hz_budget(self):
        window = self.prepare()
        with patch.object(window, 'isVisible', return_value=True), \
                patch.object(window, '_paint_wide_lyrics_mounted') as content, \
                patch.object(window, 'paint_header'), \
                patch.object(window, '_track_cover_animating', return_value=False):
            for index in range(61):
                window._background_cache = self.scene(QColor(index, 70, 100))
                window._refresh_chrome_backdrop(100. + index / 60.)
            self.assertLessEqual(content.call_count, 11)
            self.assertGreaterEqual(content.call_count, 9)

    def test_renderer_ignores_scene_damage_outside_padded_lens_region(self):
        scene = self.scene('#1653a2')
        output = QImage(scene.size(), QImage.Format.Format_ARGB32_Premultiplied)
        output.fill(Qt.GlobalColor.transparent)
        renderer = GlassControlRenderer()
        def draw(now):
            with QPainter(output) as painter:
                renderer.paint(painter, QRectF(25, 20, 35, 35), scene=scene,
                               scene_bounds=QRectF(0, 0, 360, 640), ink=QColor('white'),
                               now=now, role='top')
            return renderer.versions['top']
        self.assertEqual(draw(100.), 1)
        with QPainter(scene) as painter:
            painter.fillRect(150, 250, 30, 30, QColor('green'))
        self.assertEqual(draw(100.2), 1)
        with QPainter(scene) as painter:
            painter.fillRect(25, 20, 35, 35, QColor('green'))
        self.assertEqual(draw(100.4), 2)


if __name__ == '__main__':
    unittest.main()
