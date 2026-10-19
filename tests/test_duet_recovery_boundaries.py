from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.models import TimedLyricLine, TimedLyricUnit
from local.island_stack_visual import IslandStackVisual
from local.lyric_unit_timing import duet_estimated_read_start
from local.music_lyric_motion import LyricEmphasisPlan
from local.music_lyric_presentation import INTRO_COUNTDOWN_TEXT
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_lyric_visual_layers import _LayerOwner


# Exact 188.34 s group from the reported local generated cache. Only the first
# voice has enhanced-LRC units; its singer label ends at the 191.62 s body onset.
_CACHE_GROUP = """[ar:薛之谦/刘惜君]
[ti:聊表心意]
[by:netease]
[03:08.340]<03:08.340>薛 <03:09.980>: <03:11.620>我<03:11.830>记<03:12.550>得<03:12.690>那<03:13.320>草<03:13.920>坪 <03:15.680>一<03:15.740>览<03:16.190>无<03:16.360>遗<03:17.520>
[03:08.340]刘 : 我等你的草坪 高楼耸起
[03:17.520]下一句歌词
[03:23.000]最后一句歌词
"""


class DuetRecoveryBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _coalesce(self, source):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.artist = "薛之谦/刘惜君"
        return visual._coalesced_timed_lyric_data(source)

    def _cached_data(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        path = root / "duet-cache.lrc"
        path.write_text(_CACHE_GROUP, encoding="utf-8")
        return self._coalesce(LocalLyricsStore(root)._read_lrc(path))

    def _render_data(self, data, position):
        rows, units, kinds = data
        owner = _LayerOwner()
        owner.artist = "薛之谦/刘惜君"
        owner.media_position_source = "local-seek"
        owner.media_position_sampled_at = 100.0
        owner.media_position_seconds = position
        owner.timed_lyrics = rows
        owner.timed_lyric_units = units
        owner.timed_lyric_timing_kinds = kinds
        surface = MusicSurfaceWindow(owner)
        surface.resize(960, 540)
        self.addCleanup(owner.close)
        self.addCleanup(surface.shutdown)
        painted = {}
        native_paint = surface._paint_wrapped_lifted_lyric

        def capture(painter, row, text, font, layout, progress, **kwargs):
            painted[text] = (progress, kwargs.get("real_progresses"))
            return native_paint(painter, row, text, font, layout, progress, **kwargs)

        image = QImage(960, 540, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        # This suite inspects ordinary reading fronts and original source
        # boundaries. Highlight classification is covered independently.
        with (
            patch.object(surface, "_paint_wrapped_lifted_lyric", side_effect=capture),
            patch("local.music_surface_window.time.monotonic", return_value=100.0),
            patch("local.music_surface_window.lyric_emphasis_plan", return_value=LyricEmphasisPlan()),
            patch("local.music_surface_window.explicit_slow_unit_groups", return_value=()),
        ):
            surface.render(image)
        displayed = surface._display_lyrics()[1]
        self.assertEqual(displayed[0], (0., INTRO_COUNTDOWN_TEXT))
        self.assertEqual(surface._display_timed_units[0], ())
        self.assertEqual(surface._display_timed_kinds[0], "line")
        self.assertEqual(surface._display_raw_source_texts[0], INTRO_COUNTDOWN_TEXT)
        return surface, painted

    @staticmethod
    def _voice_units(text, body_onset, *, source_start=188.34):
        label_end = (source_start + body_onset) * 0.5
        return (
            TimedLyricUnit(source_start, label_end, text[:2]),
            TimedLyricUnit(label_end, body_onset, text[2:4]),
            *(TimedLyricUnit(body_onset + index * 0.25, body_onset + (index + 1) * 0.25, char)
              for index, char in enumerate(text[4:])),
        )

    def _row_plan(self, surface, source_row):
        displayed = surface._display_lyrics()[1]
        return surface._display_row_timings[displayed.index(source_row)]

    def _assert_visible_unread(self, painted, body):
        # The raw row fraction includes the hidden singer prefix. Assertions
        # therefore inspect the actual projected front supplied to the native
        # painter for every wrapped visible body row.
        fronts = painted[body][1]
        self.assertIsNotNone(fronts)
        self.assertTrue(fronts)
        self.assertTrue(all(front == 0. for front in fronts), fronts)

    def _assert_visible_started(self, painted, body):
        fronts = painted[body][1]
        self.assertIsNotNone(fronts)
        self.assertTrue(any(0. < front < 1. for front in fronts), fronts)

    def test_same_time_generated_cache_stays_unread_until_real_body_onset(self):
        data = self._cached_data()
        rows, units, kinds = data
        self.assertEqual([start for start, _text in rows[:2]], [188.34, 188.34])
        self.assertEqual([len(group) for group in units[:2]], [12, 0])
        self.assertEqual(kinds[:2], ["unit", "line"])
        right_body = "我等你的草坪 高楼耸起"
        for position in (188.34, 190.0, 191.6, 191.62):
            with self.subTest(position=position):
                surface, painted = self._render_data(data, position)
                self._assert_visible_unread(painted, right_body)
                plan = self._row_plan(surface, rows[1])
                self.assertEqual(plan.quality, "estimated")
                self.assertEqual((plan.start_seconds, plan.end_seconds), (191.62, 197.52))
                self.assertEqual(plan.display_units[0].timing_origin, "label")
                self.assertEqual(plan.display_units[0].start_seconds, plan.display_units[0].end_seconds)
                self.assertEqual(surface._display_lyrics()[1][1:], rows)
        _surface, painted = self._render_data(data, 191.7)
        self._assert_visible_started(painted, right_body)

    def test_same_time_real_voices_keep_independent_body_clocks_in_native_painter(self):
        rows, units, kinds = self._cached_data()
        units[0] = self._voice_units(rows[0][1], 191.62)
        units[1] = self._voice_units(rows[1][1], 188.6)
        kinds[1] = "unit"
        surface, painted = self._render_data((rows, units, kinds), 188.7)
        displayed = surface._display_lyrics()[1]
        voice_indices = [index for index, row in enumerate(displayed) if row in rows[:2]]
        self.assertEqual([displayed[index] for index in voice_indices], rows[:2])
        self.assertEqual(tuple(surface._display_timed_units[index] for index in voice_indices), tuple(units[:2]))
        for row, row_units in zip(rows[:2], units[:2]):
            plan = self._row_plan(surface, row)
            self.assertEqual(plan.quality, "word")
            self.assertEqual(plan.source_units, row_units)
            self.assertEqual(tuple((unit.start_seconds, unit.end_seconds) for unit in plan.display_units),
                             tuple((unit.start_seconds, unit.end_seconds) for unit in row_units))
        self._assert_visible_unread(painted, "我记得那草坪 一览无遗")
        self._assert_visible_started(painted, "我等你的草坪 高楼耸起")

    def test_measured_sentence_segment_keeps_own_anchors_instead_of_borrowing_peer_onset(self):
        left = "薛 : 我记得那草坪一览无遗"
        right = "刘 : 我等你的草坪高楼耸起"
        data = self._coalesce([
            TimedLyricLine(188.34, left, (TimedLyricUnit(188.34, 198.0, left),), "unit"),
            TimedLyricLine(188.34, right, self._voice_units(right, 191.62), "unit"),
            TimedLyricLine(198.0, "下一句歌词"),
            TimedLyricLine(203.0, "最后一句歌词"),
        ])
        surface, painted = self._render_data(data, 191.4)
        displayed = surface._display_lyrics()[1]
        self.assertEqual(displayed[surface._lyric_motion.active_index], (188.34, right))
        left_plan = self._row_plan(surface, (188.34, left))
        right_plan = self._row_plan(surface, (188.34, right))
        self.assertEqual(left_plan.quality, "segment")
        self.assertEqual((left_plan.start_seconds, left_plan.end_seconds), (188.34, 198.))
        self.assertEqual(left_plan.source_units, (TimedLyricUnit(188.34, 198., left),))
        self.assertTrue(all(unit.estimated for unit in left_plan.display_units))
        self.assertEqual(left_plan.display_units[0].timing_origin, "segment")
        self.assertEqual(right_plan.quality, "word")
        self._assert_visible_started(painted, left[4:])
        self._assert_visible_unread(painted, right[4:])
        later_surface, later = self._render_data(data, 191.7)
        self._assert_visible_started(later, left[4:])
        self._assert_visible_started(later, right[4:])
        self.assertEqual(self._row_plan(later_surface, (188.34, left)), left_plan)

    def test_staggered_duet_keeps_estimate_at_its_own_source_start(self):
        data = self._cached_data()
        rows, units, kinds = data
        for first_start, second_start, position, body in (
            (188.34, 188.84, 189.0, "我等你的草坪 高楼耸起"),
            (188.34, 188.84, 188.6, "我记得那草坪 一览无遗"),
        ):
            with self.subTest(body=body):
                staggered_rows = [(first_start, rows[0][1]), (second_start, rows[1][1]), *rows[2:]]
                staggered_units = list(units)
                staggered_kinds = list(kinds)
                if body.startswith("我记得"):
                    # The first, estimated voice has already started while its
                    # genuinely timed partner enters half a second later.
                    staggered_units[0] = ()
                    staggered_kinds[0] = "line"
                    staggered_units[1] = self._voice_units(
                        staggered_rows[1][1], 191.62, source_start=second_start,
                    )
                    staggered_kinds[1] = "unit"
                surface, painted = self._render_data(
                    (staggered_rows, staggered_units, staggered_kinds), position,
                )
                self.assertEqual(len(surface._display_duet_presentation.groups), 1)
                start = first_start if body.startswith("我记得") else second_start
                source_row = staggered_rows[0 if body.startswith("我记得") else 1]
                plan = self._row_plan(surface, source_row)
                self.assertEqual(plan.quality, "estimated")
                self.assertEqual((plan.start_seconds, plan.end_seconds), (start, rows[2][0]))
                self.assertEqual(plan.display_units[0].timing_origin, "label")
                self.assertEqual(plan.display_units[1].start_seconds, start)
                self._assert_visible_started(painted, body)

    def test_shared_onset_requires_known_matching_source_times(self):
        text = "薛 : 我记得那草坪一览无遗"
        units = self._voice_units(text, 191.62)
        offsets = tuple(range(4, len(text) + 1))
        self.assertEqual(duet_estimated_read_start(188.34, text, units, offsets), 188.34)
        for peer_start in (188.3396, 188.34, 188.3404):
            with self.subTest(peer_start=peer_start):
                self.assertEqual(duet_estimated_read_start(
                    188.34, text, units, offsets, peer_source_start=peer_start,
                ), 191.62)
        for peer_start in (188.3394, 188.3406, 188.84):
            with self.subTest(peer_start=peer_start):
                self.assertEqual(duet_estimated_read_start(
                    188.34, text, units, offsets, peer_source_start=peer_start,
                ), 188.34)


if __name__ == "__main__":
    unittest.main()
