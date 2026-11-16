"""Render invariants across narrow line changes, independent of native capture."""
from unittest.mock import patch
import unittest

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.music_lyric_motion import MusicLyricFrame
from _demos.narrow3 import narrow_window as narrow
from _demos.narrow3.layout import NarrowLayout
from _demos.narrow3.veil import VeilOverlay
from tests import test_narrow_v4 as fixtures


class NarrowFlickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp = fixtures.NarrowCommitTests.setUp
    tearDown = fixtures.NarrowCommitTests.tearDown
    apply = fixtures.NarrowCommitTests.apply

    def test_prepare_and_present_share_static_source_without_evicting_ink(self):
        window = self.window
        layout = NarrowLayout(window.width(), window.height())
        static = window.content_image(layout, 1, include_current=False)
        window.content_image(layout, 1, include_current=True)
        builds = window._diagnostics['lyric_layer_builds']
        with patch.object(window, 'paint_live_lyrics', side_effect=AssertionError('rerasterized')):
            self.assertIs(window.content_image(layout, 1, veil_source=True,
                                               include_current=False), static)
        self.assertEqual(window._diagnostics['lyric_layer_builds'], builds)
        with self.assertRaisesRegex(AssertionError, 'live lyric ink'):
            window.content_image(layout, veil_source=True, include_current=True)

    def test_scroll_reuses_one_stanza_without_filtering_or_dimming_chrome_again(self):
        window = self.window
        layout = NarrowLayout(window.width(), window.height())
        target = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        with patch.object(window, '_lyric_frame', return_value=MusicLyricFrame(30, 29.1, 0, 0)) as frame:
            window.refresh_veil()
            content = window.content_image(layout, 30, include_current=False, overscan=window._scroll_guard)
            with QPainter(target) as painter:
                window.paint_scroll_surface(painter, layout, content, 1)
            surface = window.scroll_surface
            builds = window.veil.builds
            with patch.object(Image.Image, 'filter', side_effect=AssertionError('blur during scroll')), \
                 patch.object(window, 'paint_live_lyrics', side_effect=AssertionError('text during scroll')):
                for visual in (29.2, 29.5, 29.8, 30.0):
                    frame.return_value = MusicLyricFrame(30, visual, 0, 0)
                    window.refresh_veil()
                    self.assertIs(window.content_image(layout, 30, include_current=False,
                                                       overscan=window._scroll_guard), content)
                    target.fill(Qt.GlobalColor.transparent)
                    with QPainter(target) as painter:
                        window.paint_scroll_surface(painter, layout, content, 1)
                    self.assertIs(window.scroll_surface, surface)
                    # No row ink occupies this x, so it isolates the fixed dim.
                    self.assertEqual(target.pixelColor(0, 40).alpha(), 46)
            self.assertEqual(window.veil.builds, builds)

    def test_dots_are_one_opaque_raster_across_backdrop_changes(self):
        window = self.window
        font = window._surface_font(32, QFont.Weight.Bold)
        target = QImage(100, 40, QImage.Format.Format_ARGB32_Premultiplied)
        with patch.object(narrow, 'paint_intro_countdown', wraps=narrow.paint_intro_countdown) as dots:
            for backdrop in ('purple', 'black', 'gray', 'purple'):
                target.fill(QColor(backdrop))
                with QPainter(target) as painter:
                    window.paint_intro_dots(painter, QPointF(50, 20), font)
                self.assertEqual(target.pixelColor(50, 20), QColor('white'))
            self.assertEqual(dots.call_count, 1)
            self.assertEqual(dots.call_args.kwargs['opacity'], 1.)

    def test_reveal_preserves_identical_middle_ink_at_every_opacity(self):
        window = self.window
        layout = NarrowLayout(window.width(), window.height())
        with patch.object(window, '_lyric_frame', return_value=MusicLyricFrame(1, 1., 0, 0)):
            window.refresh_veil()
            source = QImage(window.width(), window.height() + 2 * window._scroll_guard,
                            QImage.Format.Format_ARGB32_Premultiplied)
            source.fill(Qt.GlobalColor.transparent)
            with QPainter(source) as painter:
                painter.fillRect(QRectF(100, window._scroll_guard + 300, 10, 10), Qt.GlobalColor.white)
            target = QImage(window.size(), source.format())
            for alpha in (0, .25, .5, .75, 1):
                target.fill(Qt.GlobalColor.transparent)
                with QPainter(target) as painter:
                    window.paint_scroll_surface(painter, layout, source, alpha)
                pixel = target.pixelColor(105, 305)
                self.assertEqual(pixel.alpha(), 255, (alpha, pixel))
                self.assertEqual(pixel.red(), 255)

    def test_bottom_band_clips_after_last_visible_lyric_row(self):
        layout = NarrowLayout(360, 640)
        source = QImage(360, 640, QImage.Format.Format_ARGB32_Premultiplied)
        source.fill(Qt.GlobalColor.transparent)
        # The final row stays inside the responsive lyric viewport. The old
        # fixed y=600 now belongs to the transport safe area.
        row_top = int(layout.viewport.bottom()) - 20
        with QPainter(source) as painter:
            painter.fillRect(QRectF(100, row_top, 3, 20), Qt.GlobalColor.white)
        veil = VeilOverlay()
        veil.prepare('bottom-row', source)
        target = QImage(source.size(), source.format())
        target.fill(Qt.GlobalColor.transparent)
        with QPainter(target) as painter:
            veil.paint(painter, source, 'bottom-row', 1,
                       bottom_start=layout.viewport.bottom() + 6)
        self.assertEqual(target.copy(0, row_top - 10, 360, 30), source.copy(0, row_top - 10, 360, 30))


if __name__ == '__main__':
    unittest.main()
