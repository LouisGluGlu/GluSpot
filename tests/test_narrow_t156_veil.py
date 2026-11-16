"""t156: continuous normalized veil, physical rasters and paint cost guards."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from _demos.narrow3 import veil as module
from _demos.narrow3.veil import BackgroundEdgeVeil, VeilOverlay


class ContinuousVeilTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def source(width, height, dpr):
        source = QImage(round(width * dpr), round(height * dpr),
                        QImage.Format.Format_ARGB32_Premultiplied)
        source.setDevicePixelRatio(dpr)
        source.fill(QColor(198, 142, 87))
        return source

    def test_profile_normalizes_to_height_and_has_no_quantized_steps(self):
        self.assertEqual(module.BLUR_LEVELS, 8)
        for height in (640, 800):
            values = [VeilOverlay.band_strength(y + .5, height) for y in range(height)]
            self.assertTrue(all(a >= b for a, b in zip(values[:height // 2], values[1:height // 2])))
            self.assertTrue(all(a <= b for a, b in zip(values[height // 2:], values[height // 2 + 1:])))
            self.assertEqual(values, list(reversed(values)))
            self.assertAlmostEqual(VeilOverlay.band_strength(.14 * height, height), 1.)
            self.assertAlmostEqual(VeilOverlay.band_strength(.34 * height, height), 0.)
            ramp = values[round(.14 * height) + 1:round(.34 * height) - 1]
            self.assertEqual(len(set(ramp)), len(ramp))

    def test_whole_frame_background_has_no_seam_at_both_sizes_and_dprs(self):
        for width, height in ((360, 640), (480, 800)):
            for dpr in (1., 2.):
                with self.subTest(size=(width, height), dpr=dpr):
                    source = self.source(width, height, dpr)
                    veil = BackgroundEdgeVeil()
                    veil.prepare(source, width, height)
                    for progress in (0., .5, 1.):
                        output = source.copy()
                        with QPainter(output) as painter:
                            veil.paint(painter, QRectF(0, 0, width, height), progress)
                        colors = [output.pixelColor(output.width() // 2, y)
                                  for y in range(output.height())]
                        column = [(.2126 * color.red() + .7152 * color.green() + .0722 * color.blue()) / 255
                                  for color in colors]
                        self.assertLess(max(abs(a - b) for a, b in zip(column, column[1:])), 3 / 255)
                    mask = veil._states[0]
                    alphas = [mask.pixelColor(0, y).alpha() for y in range(mask.height())]
                    half = mask.height() // 2
                    self.assertTrue(all(a >= b for a, b in zip(alphas[:half], alphas[1:half])))
                    self.assertTrue(all(a <= b for a, b in zip(alphas[half:], alphas[half + 1:])))
                    self.assertEqual(veil.veil_prepares, 1)
                    self.assertLess(veil.veil_seam_max_step, 3.)

    def test_two_full_frame_image_draws_and_no_paint_raster_allocation(self):
        source = self.source(360, 640, 1.)
        veil = BackgroundEdgeVeil()
        veil.prepare(source, 360, 640)
        output = source.copy()
        with QPainter(output) as painter:
            class Recorder:
                draws = 0

                def __getattr__(self, name):
                    return getattr(painter, name)

                def drawImage(self, *args):
                    self.draws += 1
                    return painter.drawImage(*args)

            recorder = Recorder()
            with patch.object(module, 'QImage', side_effect=AssertionError('paint allocated raster')):
                for progress in (.1, .2, .3, .4, .5):
                    recorder.drawImage(QRectF(0, 0, 360, 640), source)
                    veil.paint(recorder, QRectF(0, 0, 360, 640), progress)
            self.assertEqual(recorder.draws, 10)

    def test_native_sharp_core_is_live_and_prepared_overlay_is_frozen(self):
        source = self.source(360, 640, 2.)
        with QPainter(source) as painter:
            painter.fillRect(QRectF(99, 299, 1, 1), Qt.GlobalColor.white)
        veil = BackgroundEdgeVeil()
        veil.prepare(source, 360, 640)
        output = source.copy()
        with QPainter(output) as painter:
            veil.paint(painter, QRectF(0, 0, 360, 640), .5)
        self.assertEqual(output.copy(0, 500, 720, 250), source.copy(0, 500, 720, 250))
        source.fill(Qt.GlobalColor.green)
        changed = source.copy()
        with QPainter(changed) as painter:
            veil.paint(painter, QRectF(0, 0, 360, 640), .5)
        self.assertEqual(changed.pixelColor(360, 640), QColor(Qt.GlobalColor.green))
        self.assertEqual(veil.builds, 1)
        self.assertNotEqual(output, changed)


if __name__ == '__main__':
    unittest.main()
