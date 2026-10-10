from __future__ import annotations

import unittest

from PySide6.QtCore import QRectF

from local.audio_output_panel_pose import output_panel_pose


class OutputPanelPoseTests(unittest.TestCase):
    def setUp(self):
        self.source = QRectF(302, 0, 42, 42)
        self.full = QRectF(0, 0, 344, 290)
        self.first = QRectF(15, 15, 312, 56)

    def pose(self, progress):
        return output_panel_pose(progress, self.source, self.full, self.first)

    def test_circle_capsule_and_full_list_are_distinct_frames(self):
        start, capsule, final = (self.pose(amount) for amount in (0, .48, 1))
        self.assertEqual(start.shell_rect, self.source)
        self.assertEqual(start.first_rect, self.source)
        self.assertEqual(capsule.first_rect, self.first)
        self.assertEqual(capsule.shell_rect.width(), self.full.width())
        self.assertLess(capsule.shell_rect.height(), final.shell_rect.height() / 2)
        self.assertEqual(capsule.row_unfold, 0)
        self.assertEqual(final.shell_rect, self.full)
        self.assertEqual(final.row_unfold, 1)

    def test_shell_keeps_top_and_right_edges_as_it_grows(self):
        for step in range(101):
            shell = self.pose(step / 100).shell_rect
            self.assertEqual(shell.top(), self.source.top())
            self.assertAlmostEqual(shell.right(), self.source.right())

    def test_short_viewport_does_not_exceed_available_height(self):
        for step in range(101):
            pose = output_panel_pose(step / 100, self.source, QRectF(0, 0, 344, 60), self.first)
            self.assertLessEqual(pose.shell_rect.height(), 60)
            self.assertLessEqual(pose.radius, pose.shell_rect.height() / 2)

    def test_direction_never_changes_pose_and_overshoot_remains_bounded(self):
        samples = [self.pose(index / 100) for index in range(101)]
        self.assertEqual(samples, list(reversed([self.pose(index / 100) for index in range(100, -1, -1)])))
        self.assertEqual(self.pose(-.04).shell_rect.height(), self.source.height() * .98)
        self.assertEqual(self.pose(1.04).shell_rect, self.pose(1).shell_rect)
        self.assertEqual(self.pose(1.04).row_unfold, 1.02)

    def test_small_spring_peak_retains_visible_bounded_row_spacing(self):
        peak = self.pose(1.007837)
        self.assertGreater(peak.row_unfold, 1)
        self.assertLessEqual(peak.row_unfold, 1.02)
        self.assertEqual(peak.shell_rect, self.full)
        self.assertEqual(peak.first_rect, self.first)
        self.assertAlmostEqual(self.pose(1.000001).row_unfold, 1.000002)

    def test_closing_peak_compresses_face_without_moving_its_anchor(self):
        pose = self.pose(-.007837)
        self.assertLess(pose.shell_rect.height(), self.source.height())
        self.assertGreaterEqual(pose.shell_rect.height(), self.source.height() * .98)
        self.assertEqual(pose.shell_rect.top(), self.source.top())
        self.assertEqual(pose.shell_rect.right(), self.source.right())
        self.assertEqual(pose.first_rect.height(), pose.shell_rect.height())
        self.assertAlmostEqual(self.pose(-.000001).shell_rect.height(), self.source.height(), places=3)


if __name__ == "__main__":
    unittest.main()
