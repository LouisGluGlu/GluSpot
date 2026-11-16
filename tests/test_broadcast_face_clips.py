import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.broadcast_face_clips import (
    CLIPS, CORNER_SPECIAL_CLIPS, LOADING_CLIPS, SPEAKING_CLIPS, OVERSHOOT,
    BLEND_SECONDS, ClipBlender, bezier_ease, clip_pose, corner_clip, finish_seconds, loading_clip,
    mix_poses, speaking_clip,
)
from local.broadcast_indicator import paint_broadcast_indicator


class BezierTests(unittest.TestCase):
    def test_ends_are_fixed_and_overshoot_leaves_the_unit_range(self):
        for curve in ((0.42, 0.0, 0.58, 1.0), OVERSHOOT):
            self.assertAlmostEqual(bezier_ease(0.0, curve), 0.0, places=5)
            self.assertAlmostEqual(bezier_ease(1.0, curve), 1.0, places=5)
        self.assertGreater(max(bezier_ease(x / 50.0, OVERSHOOT) for x in range(51)), 1.0)


class ScheduleTests(unittest.TestCase):
    def test_speaking_and_loading_pick_from_the_requested_sets(self):
        self.assertEqual({speaking_clip(seed) for seed in range(200)}, set(SPEAKING_CLIPS))
        self.assertEqual({loading_clip(seed) for seed in range(200)}, set(LOADING_CLIPS))
        self.assertEqual(set(SPEAKING_CLIPS), {"working", "coding"})
        self.assertEqual(set(LOADING_CLIPS), {"suspicious", "thinking"})

    def test_corner_rests_on_idle_or_turntable_and_plays_one_special_every_fifteen_seconds(self):
        self.assertEqual(corner_clip(0.0)[0], "idle")
        bases = set()
        for seed in range(6):
            for window in range(1, 40):
                start = window * 15.0
                name, local = corner_clip(start + 0.01, seed)
                self.assertIn(name, CORNER_SPECIAL_CLIPS)
                self.assertAlmostEqual(local, 0.01, places=5)
                # After the special, the base shuffle resumes.
                after = corner_clip(start + CLIPS[name].duration + 0.05, seed)[0]
                bases.add(after)
                self.assertIn(after, {"idle", "turntable"})
        self.assertEqual(bases, {"idle", "turntable"})
        specials = {corner_clip(w * 15.0 + 0.01, 3)[0] for w in range(1, 400)}
        self.assertEqual(specials, set(CORNER_SPECIAL_CLIPS))

    def test_every_clip_has_a_pose_through_its_whole_length(self):
        for name, clip in CLIPS.items():
            for step in range(11):
                pose = clip_pose(name, clip.duration * step / 10.0)
                self.assertFalse(hasattr(pose, "squash_x"), name)
            # No clip scales the body (the user saw it as zooming).
            self.assertFalse({"squash_x", "squash_y"} & set(clip.tracks), name)
        self.assertEqual(clip_pose("happy", 0.5).shape, "arc")
        # No decorations at all: only expressions and motion.
        self.assertFalse(any(clip.decor or clip.eyes_hidden for clip in CLIPS.values()))
        self.assertNotIn("logo", CLIPS)
        self.assertLess(clip_pose("playful", 0.9).lid_right, 0.1)
        self.assertGreater(finish_seconds(), 1.0)


class BlendTests(unittest.TestCase):
    def test_switching_clips_eases_from_the_pose_on_screen(self):
        blender = ClipBlender()
        now = 0.0
        for _ in range(60):
            now += 1 / 60
            blender.pose("thinking", now, now)
        before = blender.shown
        steps = []
        last = before
        for frame in range(40):
            now += 1 / 60
            pose = blender.pose("working", frame / 60, now)
            steps.append(max(abs(pose.yaw - last.yaw), abs(pose.pitch - last.pitch), abs(pose.roll - last.roll)))
            last = pose
        # The thinking head (yaw -16, pitch -16) reaches the working pose
        # (yaw 6, pitch 9) through small steps, not one jump.
        self.assertLess(max(steps), 6.0)
        self.assertAlmostEqual(last.pitch, clip_pose("working", 39 / 60).pitch, delta=0.01)
        self.assertGreater(BLEND_SECONDS, 0.2)

    def test_a_one_shot_clip_starting_over_also_eases(self):
        blender = ClipBlender()
        blender.pose("proud", 1.0, 1.0)
        held = blender.shown.pitch
        pose = blender.pose("proud", 0.0, 1.0 + 1 / 60)
        self.assertLess(abs(pose.pitch - held), 3.0)

    def test_eye_shape_changes_behind_a_blink(self):
        clip = CLIPS["happy"]
        edge = clip.shape_window[0]
        self.assertLess(clip_pose("happy", edge).lid_left, 0.05)
        self.assertGreater(clip_pose("happy", edge + 0.2).lid_left, 0.99)
        a, b = clip_pose("working", 0.5), clip_pose("happy", 0.6)
        self.assertNotEqual(a.shape, b.shape)
        self.assertLess(mix_poses(a, b, 0.5).lid_left, 0.05)
        self.assertGreater(mix_poses(a, b, 0.95).lid_left, 0.85)

    def test_yaw_takes_the_short_way_round(self):
        end = clip_pose("turntable", 4.0)
        start = clip_pose("idle", 0.0)
        self.assertAlmostEqual(end.yaw, 360.0, places=3)
        middle = mix_poses(end, start, 0.5)
        self.assertAlmostEqual(middle.yaw % 360.0, 0.0, places=3)


class PaintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_each_clip_paints_inside_the_cover_and_the_corner(self):
        for name, clip in CLIPS.items():
            for side in (200, 30):
                image = QImage(side + 20, side + 20, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                with QPainter(image) as painter:
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                    paint_broadcast_indicator(
                        painter, QRectF(10, 10, side, side), side / 2.0, style="face", state="idle",
                        elapsed=2.0, color=QColor(40, 110, 170), ring_painter=None, body_mode="album",
                        clip=name, clip_t=clip.duration * 0.4,
                    )
                self.assertGreater(image.pixelColor(10 + side // 2, 10 + side // 2 + side // 4).alpha(), 200, name)
                # Nothing leaks outside the cover rectangle.
                self.assertEqual(image.pixelColor(4, 4).alpha(), 0, name)

    def test_every_clip_paints_the_same_body_size(self):
        def width(name, t):
            image = QImage(220, 220, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                paint_broadcast_indicator(painter, QRectF(10, 10, 200, 200), 20, style="face", state="idle",
                                          elapsed=0.0, color=QColor(40, 110, 170), ring_painter=None,
                                          body_mode="album", clip=name, clip_t=t)
            row = 110 + 40
            xs = [x for x in range(220) if image.pixelColor(x, row).alpha() > 128]
            return xs[-1] - xs[0] if xs else 0
        sizes = {width(name, clip.duration * k / 6.0) for name, clip in CLIPS.items() for k in range(7)}
        self.assertLessEqual(max(sizes) - min(sizes), 26, sizes)

    def test_body_is_shaded_not_flat(self):
        image = QImage(220, 220, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            paint_broadcast_indicator(painter, QRectF(10, 10, 200, 200), 20, style="face", state="idle",
                                      elapsed=0.0, color=QColor(40, 110, 170), ring_painter=None,
                                      body_mode="album", clip="idle", clip_t=0.0)
        upper_left = image.pixelColor(70, 80).lightness()
        lower_right = image.pixelColor(170, 175).lightness()
        self.assertGreater(upper_left, lower_right + 15)


if __name__ == "__main__":
    unittest.main()
