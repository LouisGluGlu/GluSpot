import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from local import sea_lyric_background as sea


class SeaInkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_every_song_reads_in_black_and_in_white(self):
        palettes = [((), ())]
        for hue in range(0, 360, 15):
            for value in (0.25, 0.55, 0.95):
                for sat in (0.2, 0.6, 1.0):
                    main = QColor.fromHsvF(hue / 360.0, sat, value)
                    second = QColor.fromHsvF(((hue + 40) % 360) / 360.0, 0.7, 0.8)
                    palettes.append(((main, second), (0.6, 0.3)))
        palettes.append(((QColor("#202020"), QColor("#f0f0f0")), (0.7, 0.3)))
        for colors, weights in palettes:
            before, before_ink, after, after_ink = sea.pick_colors(colors, weights)
            with self.subTest(colors=[QColor(c).name() for c in colors]):
                self.assertEqual({before_ink, after_ink}, {"black", "white"})
                for field, ink in ((before, before_ink), (after, after_ink)):
                    target = QColor("black") if ink == "black" else QColor("white")
                    self.assertGreaterEqual(sea._contrast(field, target), 4.4)


if __name__ == "__main__":
    unittest.main()
