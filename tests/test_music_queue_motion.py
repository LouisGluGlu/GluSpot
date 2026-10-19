"""Visible queue selection moves continuously; placeholder clocks stay local."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication

from tests import test_music_surface_queue_scroll as queue_fixture
from tests.test_owned_artwork_retry import picture


class MusicQueueMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    render = queue_fixture.MusicSurfaceQueueScrollTests.render
    tearDown = queue_fixture.MusicSurfaceQueueScrollTests.tearDown
    wheel = queue_fixture.MusicSurfaceQueueScrollTests.wheel

    def setUp(self):
        queue_fixture.MusicSurfaceQueueScrollTests.setUp(self)
        self.window.show()
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()
        self.render()

    def test_song_change_animates_focus_and_selected_glass_before_reaching_center(self):
        start = self.window._queue_scroll
        self.state["current"] = 6
        self.render()
        motion = self.window._queue_focus_motion
        self.assertEqual(motion.state(), QAbstractAnimation.State.Running)
        self.assertEqual(self.window._queue_scroll, start)
        self.assertEqual(self.window._queue_selection_position, 0.)
        motion.setCurrentTime(220)
        self.render()
        self.assertGreater(self.window._queue_scroll, start)
        self.assertLess(self.window._queue_scroll, self.window._queue_focus_to)
        self.assertGreater(self.window._queue_selection_position, 0.)
        self.assertLess(self.window._queue_selection_position, 6.)
        motion.setCurrentTime(440)
        self.render()
        self.assertEqual(self.window._queue_selection_position, 6.)
        self.assertAlmostEqual(self.window._queue_hit_rects[6].center().y(),
            (self.window._queue_view_rect.top() + 46. * self.window._surface_scale()
             + self.window._queue_view_rect.bottom()) / 2. - 2.5 * self.window._surface_scale())

    def test_consecutive_next_retargets_from_the_visible_pose_and_wheel_takes_over(self):
        self.state["current"] = 7
        self.render()
        self.window._queue_focus_motion.setCurrentTime(100)
        pose = self.window._queue_scroll, self.window._queue_selection_position
        self.state["current"] = 20
        self.render()
        self.assertEqual((self.window._queue_scroll, self.window._queue_selection_position), pose)
        self.window._queue_focus_motion.setCurrentTime(100)
        self.wheel(pixels=-15)
        self.assertEqual(self.window._queue_focus_motion.state(), QAbstractAnimation.State.Stopped)
        self.assertEqual(self.window._queue_selection_position, 20.)
        self.assertFalse(self.window._queue_hit_rects)

    def test_replacing_queue_generation_settles_incoming_rows_and_hiding_stops_motion(self):
        self.state["current"] = 7
        self.render()
        self.assertEqual(self.window._queue_focus_motion.state(), QAbstractAnimation.State.Running)
        self.state.update(token=48, current=60)
        self.render()
        self.assertEqual(self.window._queue_focus_motion.state(), QAbstractAnimation.State.Stopped)
        self.assertIn(60, self.window._queue_hit_rects)
        self.state["current"] = 61
        self.render()
        self.window.hide()
        self.assertEqual(self.window._queue_focus_motion.state(), QAbstractAnimation.State.Stopped)
        self.assertFalse(self.window._queue_skeleton_timer.isActive())

    def test_missing_cover_pulse_stops_after_all_visible_artwork_arrives_and_on_layout_exit(self):
        self.state["songs"] = tuple(replace(song, artwork_url=f"https://fixture.invalid/{song.song_id}.png")
                                    for song in self.state["songs"])
        self.render()
        self.assertTrue(self.window._queue_skeleton_timer.isActive())
        image = picture(64)
        self.owner.data_provider.get_owned_queue_artwork = lambda song: image
        self.render()
        self.assertFalse(self.window._queue_skeleton_timer.isActive())
        self.owner.data_provider.get_owned_queue_artwork = lambda song: b""
        self.render()
        self.assertTrue(self.window._queue_skeleton_timer.isActive())
        self.window.set_content_layout("cover", animate=False)
        self.assertFalse(self.window._queue_skeleton_timer.isActive())

    def test_selected_queue_face_uses_the_shared_theme_and_glass_strength(self):
        for theme in ('light', 'dark'):
            for level in (0, 50, 100):
                self.owner.settings_getter = lambda key, default=None: (
                    level if key == 'v2_music_glass_level' else theme if key == 'v2_theme_mode' else default)
                with patch('local.music_surface_layout.paint_liquid_glass') as paint:
                    self.render()
                self.assertEqual(paint.call_count, 1)
                self.assertEqual(paint.call_args.kwargs['material_level'], level)
                self.assertEqual(paint.call_args.kwargs['light'], theme == 'light')


if __name__ == "__main__":
    unittest.main()
