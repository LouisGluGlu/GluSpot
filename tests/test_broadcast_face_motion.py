"""Style-two enter/exit uses the shared critically damped spring."""
from __future__ import annotations

import unittest

import json

from PySide6.QtGui import QColor

from local.broadcast_face_player import BroadcastFaceMotion, tinted_face_path
from local.broadcast_indicator import broadcast_face_colors
from local.spring_motion import WIDE_NARROW_SPRING


class BroadcastFaceMotionTests(unittest.TestCase):
    def test_shared_spring_is_critically_damped_and_does_not_overshoot(self):
        self.assertEqual(WIDE_NARROW_SPRING.damping_ratio, 1.0)
        motion = BroadcastFaceMotion()
        peak = 0.0
        now = 10.0
        for _ in range(180):
            now += 1.0 / 60.0
            peak = max(peak, motion.advance(True, now))
        self.assertLessEqual(peak, 1.001)
        self.assertTrue(motion.playing)
        self.assertGreater(motion.spring.value, 0.99)
        floor = 1.0
        for _ in range(180):
            now += 1.0 / 60.0
            floor = min(floor, motion.advance(False, now))
        self.assertGreaterEqual(floor, -0.001)
        self.assertTrue(motion.idle)

    def test_same_timestamp_does_not_advance_twice(self):
        motion = BroadcastFaceMotion()
        first = motion.advance(True, 5.0)
        second = motion.advance(True, 5.0)
        self.assertEqual(first, second)
        self.assertEqual(first, 0.0)

    def test_tinted_face_is_opaque_accent_and_eyes_stay_white(self):
        source = QColor(92, 158, 232)
        body, eyes = broadcast_face_colors(source, "accent")
        payload = json.loads(tinted_face_path(source).read_text(encoding="utf-8"))
        expected = [channel / 255.0 for channel in body.getRgb()[:3]]
        fills = []
        strokes = []
        eye_fills = []

        def walk(node, eye=False):
            if isinstance(node, dict):
                eye = eye or node.get("nm") == "eyes"
                if isinstance(node.get("c"), dict) and node.get("ty") in {"fl", "st"}:
                    color = node["c"]["k"]
                    opacity = node.get("o", {}).get("k")
                    if eye and node.get("ty") == "fl":
                        eye_fills.append((color, opacity))
                    elif node.get("ty") == "st":
                        strokes.append((color, opacity))
                    elif node.get("ty") == "fl":
                        fills.append((color, opacity))
                for value in node.values():
                    walk(value, eye)
            elif isinstance(node, list):
                for item in node:
                    walk(item, eye)

        walk(payload)
        self.assertTrue(fills)
        self.assertTrue(strokes)
        self.assertTrue(eye_fills)
        for color, opacity in fills:
            self.assertEqual(opacity, 100)
            for index in range(3):
                self.assertAlmostEqual(color[index], expected[index], places=4)
            self.assertEqual(color[3], 1.0)
        for color, _opacity in strokes:
            for index in range(3):
                self.assertAlmostEqual(color[index], expected[index], places=4)
        for color, opacity in eye_fills:
            self.assertEqual(color[:3], [1, 1, 1])
            self.assertEqual(opacity, 100)
        self.assertEqual(eyes.getRgb()[:3], (245, 246, 248))


if __name__ == "__main__":
    unittest.main()
