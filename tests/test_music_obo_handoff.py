from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget

from local.music_surface_window import MusicSurfaceWindow


class _Owner(QWidget):
    def __init__(self):
        super().__init__()
        self._music_windowed_preference = False
        self.music_intro_cleanup_enabled = False
        self.enable_dual_line_lyrics = True
        self.music_surface_reading_strategy = "gold"
        self.media_is_playing = True
        self.media_track_id = "obo-handoff"
        self.media_position_source = "player"
        self.media_position_seconds = 0.0
        self.timed_lyrics = [
            (0.0, "Hello world"), (3.0, "Next sentence has four words"),
            (6.0, "Later sentence"), (9.0, "Final sentence"),
        ]
        self.timed_lyric_units = [(
            SimpleNamespace(start_seconds=0.0, end_seconds=0.7, text="Hello "),
            SimpleNamespace(start_seconds=0.7, end_seconds=1.2, text="world"),
        ), (), (), ()]
        self.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]


class MusicOboHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _Owner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.output = QImage(960, 540, QImage.Format.Format_RGB32)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _render(self, position: float, *, now: float | None = None):
        now = 100.0 + position if now is None else now
        self.owner.media_position_seconds = position
        self.owner.media_position_sampled_at = now
        painted = []
        original = self.window._paint_wrapped_lifted_lyric

        def record(painter, row, text, font, layout, progress, *args, **kwargs):
            units = []
            draw = self.window._draw_continuous_read_unit
            draw_batch = self.window._draw_sparse_static_run
            expected_unread = self.window._future_visual_line_colors(layout, row.topLeft())[0].rgba()

            def record_unit(painter, source, origin, *draw_args, **draw_kwargs):
                # Remove viewport/group-roll position, but retain the actual
                # painter translation that bridges the unread baseline.
                visual_offset = (kwargs.get("line_offsets") or (0.0,))[0]
                units.append((
                    origin.y() + painter.transform().dy() - row.top() - visual_offset,
                    int(source.cacheKey()),
                    draw_args[3].rgba(),
                ))
                return draw(painter, source, origin, *draw_args, **draw_kwargs)

            def record_batch(painter, batch_units, origin, unread_color):
                painted = draw_batch(painter, batch_units, origin, unread_color)
                if painted:
                    visual_offset = (kwargs.get("line_offsets") or (0.0,))[0]
                    ink_color = QColor(unread_color) if unread_color is not None else QColor(255, 255, 255)
                    units.extend((
                        origin.y() + painter.transform().dy() - row.top() - visual_offset,
                        int(source.cacheKey()), ink_color.rgba(),
                    ) for source, _left in batch_units)
                return painted

            with patch.object(self.window, "_draw_continuous_read_unit", side_effect=record_unit), patch.object(
                self.window, "_draw_sparse_static_run", side_effect=record_batch,
            ):
                original(painter, row, text, font, layout, progress, *args, **kwargs)
            painted.append((text, progress, units, expected_unread))

        with (
            patch("local.music_surface_window.time.monotonic", return_value=now),
            patch.object(self.window, "_paint_wrapped_lifted_lyric", side_effect=record),
            patch.object(self.window, "_lyric_view_anchor", wraps=self.window._lyric_view_anchor) as anchor,
        ):
            self.window.render(self.output)
        return anchor.call_args.args[0], painted

    def test_early_focus_prepares_next_unread_pose_without_advancing_source_row(self):
        for position in (1.2, 1.25, 1.35, 1.5, 2.99):
            focus, painted = self._render(position)
            following = [entry for entry in painted if entry[0] == "Next sentence has four words"]
            with self.subTest(position=position):
                self.assertEqual(focus, 1)
                self.assertEqual(self.window._lyric_motion.active_index, 0)
                self.assertTrue(following)
                self.assertEqual(following[-1][1], 0.0)
                self.assertTrue(following[-1][2])
                self.assertTrue(all(unit[2] == following[-1][3] for unit in following[-1][2]))

    def test_next_row_uses_same_unread_pose_at_source_boundary(self):
        self._render(2.8)
        before = self._render(2.999)[1]
        self._render(3.0)
        after = self._render(3.04)[1]
        before_next = next(entry for entry in before if entry[0] == "Next sentence has four words")
        after_next = next(entry for entry in after if entry[0] == "Next sentence has four words")
        self.assertEqual(before_next[1], 0.0)
        self.assertEqual(self.window._lyric_motion.active_index, 1)
        self.assertEqual(before_next[2][0][1], after_next[2][0][1])
        self.assertLess(abs(after_next[2][0][0] - before_next[2][0][0]), 0.15)

    def test_early_focus_and_active_row_batch_match_individual_raster_ink(self):
        for position in (1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8):
            self._render(position)
        batch_draws = []
        draw_batch = self.window._draw_sparse_static_run

        def record_batch(*args):
            result = draw_batch(*args)
            if result:
                batch_draws.append(args[1])
            return result

        for position in (2.999, 3.04):
            now = 100.0 + position
            self.owner.media_position_seconds = position
            self.owner.media_position_sampled_at = now
            images = []
            for batched in (False, True):
                image = QImage(960, 540, QImage.Format.Format_RGB32)
                image.fill(Qt.GlobalColor.black)
                with patch("local.music_surface_window.time.monotonic", return_value=now):
                    if batched:
                        with patch.object(self.window, "_draw_sparse_static_run", side_effect=record_batch):
                            self.window.render(image)
                    else:
                        with patch.object(self.window, "_draw_sparse_static_run", return_value=False):
                            self.window.render(image)
                images.append(image)
            reference, optimized = images
            a = bytes(reference.constBits())
            b = bytes(optimized.constBits())
            differing = sum(abs(left - right) > 3 for left, right in zip(a, b))
            with self.subTest(position=position):
                self.assertLess(differing / len(a), 0.001)
        self.assertTrue(batch_draws)

    def test_player_sample_can_skip_boundary_without_dropping_next_row(self):
        self.owner.timed_lyric_units = [(
            SimpleNamespace(start_seconds=0.0, end_seconds=1.5, text="Hello "),
            SimpleNamespace(start_seconds=1.5, end_seconds=2.95, text="world"),
        ), (), (), ()]
        _focus, first_paint = self._render(2.95, now=102.95)
        waiting = next(entry for entry in first_paint if entry[0] == "Next sentence has four words")
        first_pose = self.window._obo_advanced_focus_pose
        _focus, boundary_paint = self._render(3.04, now=103.04)
        reading = next(entry for entry in boundary_paint if entry[0] == "Next sentence has four words")
        self.assertEqual(self.window._lyric_motion.active_index, 1)
        self.assertEqual(waiting[1], 0.0)
        self.assertGreater(reading[1], 0.0)
        self.assertLess(
            abs(reading[2][0][0] - waiting[2][0][0]), 0.25,
            (waiting[2][0][0], reading[2][0][0], first_pose, self.window._obo_advanced_focus_pose),
        )

    def test_short_gap_preparation_is_continuous_and_finishes_before_reading(self):
        self.owner.timed_lyric_units[0] = (
            SimpleNamespace(start_seconds=0.0, end_seconds=1.5, text="Hello "),
            SimpleNamespace(start_seconds=1.5, end_seconds=2.95, text="world"),
        )
        poses = []
        for position in (2.95, 2.96, 2.97, 2.98, 2.99, 3.0, 3.04):
            focus, painted = self._render(position)
            following = next(entry for entry in painted if entry[0] == "Next sentence has four words")
            poses.append(following[2][0][0])
            self.assertEqual(focus, 1)
            if position < 3.0:
                self.assertEqual(following[1], 0.0)
        self.assertGreater(poses[5], poses[0])
        self.assertTrue(all(right >= left - 0.01 for left, right in zip(poses[:6], poses[1:6])))
        self.assertLess(abs(poses[6] - poses[5]), 0.15)
        self.assertLess(max(right - left for left, right in zip(poses[:6], poses[1:6])), 1.0)

    def test_paused_gap_holds_unread_pose_and_zero_gap_never_early_focuses(self):
        self._render(1.2)
        waiting = self._render(1.3)[1]
        held = next(entry for entry in waiting if entry[0] == "Next sentence has four words")
        self.owner.media_is_playing = False
        for now in (101.5, 102.0, 103.0):
            focus, painted = self._render(1.3, now=now)
            following = next(entry for entry in painted if entry[0] == "Next sentence has four words")
            self.assertEqual(focus, 1)
            self.assertEqual(self.window._lyric_motion.active_index, 0)
            self.assertEqual(following[1], 0.0)
            self.assertAlmostEqual(following[2][0][0], held[2][0][0], delta=0.01)

        self.owner.media_track_id = "zero-gap"
        self.owner.media_is_playing = True
        zero_gap_units = (
            SimpleNamespace(start_seconds=0.0, end_seconds=1.5, text="Hello "),
            SimpleNamespace(start_seconds=1.5, end_seconds=3.0, text="world"),
        )
        self.owner.timed_lyric_units = [zero_gap_units, *self.owner.timed_lyric_units[1:]]
        focus, painted = self._render(2.99, now=104.0)
        self.assertEqual(self.window._display_timed_units[0][-1].end_seconds, 3.0)
        self.assertEqual(focus, 0)
        self.assertIsNone(self.window._obo_advanced_focus_pose)
        self.assertFalse(any(entry[0] == "Next sentence has four words" for entry in painted))

    def test_paired_duet_does_not_advance_only_one_voice_early(self):
        self.owner.artist = "薛之谦、刘惜君"
        self.owner.media_track_id = "duet-obo"
        self.owner.timed_lyrics = [
            (0.0, "薛：我先开始唱"), (0.75, "刘：我后开始唱"),
            (3.0, "薛：第三句继续"), (3.75, "刘：第四句继续"),
        ]
        self.owner.timed_lyric_units = [(
            SimpleNamespace(start_seconds=0.0, end_seconds=0.3, text="薛："),
            SimpleNamespace(start_seconds=0.3, end_seconds=0.5, text="我先开始唱"),
        ), (), (), ()]
        self.owner.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]
        focus, _painted = self._render(0.55)
        self.assertEqual(
            [(group.first_index, group.second_index) for group in self.window._display_duet_presentation.groups],
            [(0, 1), (2, 3)],
        )
        self.assertEqual(focus, 0)
        self.assertIsNone(self.window._obo_advanced_focus_pose)

        # The second voice is a line-estimated row in an otherwise truly
        # timed song. It must not advance the shared duet group by itself.
        focus, _painted = self._render(2.2)
        self.assertEqual(self.window._lyric_motion.active_index, 1)
        self.assertEqual(focus, 0)
        self.assertIsNone(self.window._obo_advanced_focus_pose)

    def test_low_perception_mixed_timing_song_prepares_next_unread_row(self):
        self.owner.music_surface_reading_strategy = "low_perception"
        focus, painted = self._render(1.5)
        self.assertEqual(focus, 1)
        self.assertEqual(self.window._lyric_motion.active_index, 0)
        self.assertIsNotNone(self.window._obo_advanced_focus_pose)
        next_row = next(entry for entry in painted if entry[0] == "Next sentence has four words")
        self.assertEqual(next_row[1], 0.0)

    def test_low_perception_without_real_units_switches_whole_lines_only(self):
        self.owner.music_surface_reading_strategy = "low_perception"
        self.owner.timed_lyric_units = [(), (), (), ()]
        self.owner.timed_lyric_timing_kinds = ["line"] * 4
        focus, painted = self._render(2.3)
        self.assertEqual(focus, 0)
        self.assertIsNone(self.window._obo_advanced_focus_pose)
        self.assertFalse(any(entry[0] == "Next sentence has four words" for entry in painted))

    def test_gold_without_real_units_reads_but_does_not_focus_early(self):
        self.owner.timed_lyric_units = [(), (), (), ()]
        self.owner.timed_lyric_timing_kinds = ["line"] * 4
        focus, _painted = self._render(2.3)
        self.assertEqual(focus, 0)
        self.assertIsNone(self.window._obo_advanced_focus_pose)

    def test_mixed_song_estimated_row_finishes_one_second_early_without_source_seek(self):
        self._render(1.5)
        self._render(3.2)
        focus_mid, painted_mid = self._render(4.5)
        self.assertEqual(focus_mid, 1)
        active_mid = next(entry for entry in painted_mid if entry[0] == "Next sentence has four words")
        self.assertGreater(active_mid[1], 0.65)
        self.assertLess(active_mid[1], 0.9)
        focus, painted = self._render(5.05)
        self.assertEqual(focus, 2)
        self.assertEqual(self.window._lyric_motion.active_index, 1)
        self.assertEqual(self.owner.media_position_seconds, 5.05)
        self.assertIsNotNone(self.window._obo_advanced_focus_pose)
        next_row = next(entry for entry in painted if entry[0] == "Later sentence")
        self.assertEqual(next_row[1], 0.0)

    def test_mixed_song_short_estimated_row_uses_full_interval(self):
        self.owner.timed_lyrics = [
            (0.0, "Hello world"), (3.0, "Next sentence has four words"),
            (3.7, "Later sentence"), (7.0, "Final sentence"),
        ]
        self._render(1.5)
        self._render(3.1)
        focus, _painted = self._render(3.5)
        self.assertEqual(focus, 1)
        self.assertEqual(self.window._lyric_motion.active_index, 1)

    def test_seek_and_track_change_discard_early_pose(self):
        self._render(1.5)
        self.assertIsNotNone(self.window._obo_advanced_focus_pose)
        self.owner.media_position_source = "local-seek"
        focus, painted = self._render(0.5, now=102.0)
        self.assertEqual(focus, 0)
        self.assertIsNone(self.window._obo_advanced_focus_pose)
        self.assertFalse(any(entry[0] == "Next sentence has four words" for entry in painted))

        self.owner.media_position_source = "player"
        self._render(1.5, now=103.0)
        self.assertIsNotNone(self.window._obo_advanced_focus_pose)
        self.owner.media_track_id = "different-song"
        self._render(0.5, now=104.0)
        self.assertIsNone(self.window._obo_advanced_focus_pose)


if __name__ == "__main__":
    unittest.main()
