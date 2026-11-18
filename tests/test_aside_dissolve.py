import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QFont, QImage, QPainter, QTextLayout
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication

from local import aside_dissolve as dissolve


class AsideDissolveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_exit_runs_after_the_aside_window_only(self):
        self.assertIsNone(dissolve.dissolve_phase(None, 5.0))
        self.assertIsNone(dissolve.dissolve_phase(5.0, 4.99))
        self.assertAlmostEqual(dissolve.dissolve_phase(5.0, 5.0 + dissolve.DISSOLVE_S / 2), 0.5)
        self.assertIsNone(dissolve.dissolve_phase(5.0, 5.0 + dissolve.DISSOLVE_S + 1e-6))
        self.assertEqual(dissolve.blur_settle(0.05), 0.0)
        self.assertEqual(dissolve.blur_settle(1.0), 1.0)

    def test_grains_leave_left_to_right_and_settle_into_the_blur(self):
        left = dissolve.particle_pose(0.05, 0.5, 0.5, 0.2)
        right = dissolve.particle_pose(0.95, 0.5, 0.5, 0.2)
        self.assertLess(left[1], 0.0)                  # lifting
        self.assertEqual(right, (0.0, 0.0, 1.0, 1.0))  # still crisp
        # Late in its column a grain is back in place and nearly gone ...
        settled = dissolve.particle_pose(0.05, 0.5, 0.5, 0.99)
        self.assertLess(abs(settled[1]), 0.5)
        self.assertLess(settled[3], 0.05)
        # ... while the blur has condensed there, left before right.
        self.assertGreater(dissolve.blur_settle(0.6, 0.0), 0.9)
        self.assertLess(dissolve.blur_settle(0.6, 1.0), dissolve.blur_settle(0.6, 0.0))

    def test_grains_carry_the_blur_one_character_right(self):
        font = QFont()
        font.setPixelSize(20)
        shift = dissolve.word_shift("(echo of the light)", font)
        from PySide6.QtGui import QFontMetricsF
        self.assertAlmostEqual(shift, QFontMetricsF(font).horizontalAdvance("e"), delta=0.5)
        self.assertLessEqual(shift, 20.0)
        self.assertAlmostEqual(dissolve.word_shift("（回声）", font),
                               QFontMetricsF(font).horizontalAdvance("回"), delta=0.5)
        self.assertEqual(dissolve.word_shift("( )", font), 0.0)
        # A grain starts where it stood and lands one character right.
        self.assertEqual(dissolve.particle_pose(0.95, 0.5, 0.5, 0.2, shift=shift)[0], 0.0)
        landed = dissolve.particle_pose(0.05, 0.5, 0.5, 0.99, shift=shift)
        self.assertAlmostEqual(landed[0], shift, delta=1.0)
        # The grains are at the landing while still bright, before the blur forms there.
        local = dissolve.ARRIVE
        phase = local * dissolve.FLIGHT
        arrived = dissolve.particle_pose(0.0, 0.5, 0.5, phase, scale=0.0, shift=shift)
        self.assertAlmostEqual(arrived[0], shift, delta=0.01)
        self.assertGreater(arrived[3], 0.85)
        self.assertLess(dissolve.blur_settle(phase, 0.0), 0.35)

    def test_sprite_cells_cover_the_ink(self):
        font = QFont()
        font.setPixelSize(20)
        layout = QTextLayout("echo of the light", font)
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(400)
        layout.endLayout()
        sprite = dissolve.build_sprite(layout, "echo of the light", font, 400, 30, QColor("white"), (0.0,), 2.0)
        self.assertGreater(len(sprite.cells), 20)
        self.assertLessEqual(len(sprite.cells), dissolve.MAX_PARTICLES)
        image = QImage(420, 60, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        dissolve.paint_particles(painter, sprite, QPointF(4, 4), 0.0)
        dissolve.paint_particles(painter, sprite, QPointF(4, 4), 0.3)
        painter.end()
        lit = sum(1 for x in range(0, 420, 3) for y in range(0, 60, 3) if image.pixelColor(x, y).alpha() > 40)
        self.assertGreater(lit, 20)


if __name__ == "__main__":
    unittest.main()
