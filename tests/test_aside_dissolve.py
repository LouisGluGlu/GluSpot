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
        self.assertEqual(dissolve.blur_settle(0.2), 0.0)
        self.assertEqual(dissolve.blur_settle(1.0), 1.0)

    def test_particles_leave_from_left_to_right_and_float_up(self):
        left = dissolve.particle_pose(0.05, 0.5, 0.5, 0.0, 0.2)
        right = dissolve.particle_pose(0.95, 0.5, 0.5, 0.0, 0.2)
        self.assertLess(left[1], 0.0)                 # moving up
        self.assertLess(left[3], 1.0)                 # fading
        self.assertEqual(right, (0.0, 0.0, 1.0, 1.0))  # still in place
        self.assertLess(dissolve.particle_pose(0.95, 0.5, 0.5, 1.0, 0.999)[3], 0.05)

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
        painter.end()
        lit = sum(1 for x in range(0, 420, 3) for y in range(0, 60, 3) if image.pixelColor(x, y).alpha() > 40)
        self.assertGreater(lit, 20)


if __name__ == "__main__":
    unittest.main()
