from __future__ import annotations

from types import SimpleNamespace
import unittest

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from api.models import TimedLyricUnit
from local.music_lyric_presentation import split_display_layers
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner


class _SeekProvider:
    def __init__(self):
        self.calls = []

    def seek_playback_position(self, seconds, *, expected_track_id=None):
        self.calls.append((seconds, expected_track_id))
        return True


class MusicLyricSeekTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.provider = _SeekProvider()
        self.owner.data_provider = self.provider
        self.owner.media_source_app = "netease"
        self.owner._effective_lyric_offset_seconds = lambda **_kwargs: 1.5
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _event(self, point):
        return SimpleNamespace(
            button=lambda: Qt.MouseButton.LeftButton,
            position=lambda: point,
            globalPosition=lambda: QPointF(self.window.mapToGlobal(point.toPoint())),
            accept=lambda: None,
        )

    def _render(self):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.window.render(image)

    def test_measured_word_click_seeks_word_start_once_with_offset_and_track_fence(self):
        self.owner.media_track_id = "word-track"
        self.owner.timed_lyrics = [
            (0.0, "first line"), (20.0, "next"), (40.0, "later"), (60.0, "last"),
        ]
        self.owner.timed_lyric_units = [
            (
                TimedLyricUnit(2.0, None, "first  "),
                TimedLyricUnit(4.0, 6.0, "line"),
            ),
            (), (),
        ]
        self.owner.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]
        self.window._browse_track_key = self.owner.media_track_id
        self._render()
        target = next(
            (rect for (row, unit, _fragment), (rect, _seconds) in self.window._lyric_word_hit_rects.items()
             if row == 0 and unit == 1),
            None,
        )
        self.assertIsNotNone(target)
        self.assertEqual(self.window._lyric_word_target_at(target.center())[2], 4.0)
        self.window.mousePressEvent(self._event(target.center()))
        self.window.mouseReleaseEvent(self._event(target.center()))
        self.assertEqual(self.provider.calls, [(2.5, "word-track")])

    def test_line_click_remains_fallback_without_source_units(self):
        self.owner.media_track_id = "line-track"
        self.owner.timed_lyrics = [
            (0.0, "first line"), (20.0, "next"), (40.0, "later"), (60.0, "last"),
        ]
        self.owner.timed_lyric_units = [(), (), (), ()]
        self.owner.timed_lyric_timing_kinds = ["line", "line", "line", "line"]
        self.window._browse_track_key = self.owner.media_track_id
        self._render()
        self.assertFalse(self.window._lyric_word_hit_rects)
        row = self.window._lyric_hit_rects[0]
        self.window.mousePressEvent(self._event(row.center()))
        self.window.mouseReleaseEvent(self._event(row.center()))
        self.assertEqual(self.provider.calls, [(0.0, "line-track")])

    def test_word_press_is_fenced_when_track_changes_before_release(self):
        self.owner.media_track_id = "old-track"
        self.owner.timed_lyrics = [
            (0.0, "first line"), (20.0, "next"), (40.0, "later"), (60.0, "last"),
        ]
        self.owner.timed_lyric_units = [
            (TimedLyricUnit(2.0, 4.0, "first "), TimedLyricUnit(4.0, 6.0, "line")),
            (), (),
        ]
        self.owner.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]
        self.window._browse_track_key = self.owner.media_track_id
        self._render()
        target = next(iter(self.window._lyric_word_hit_rects.values()))[0]
        self.window.mousePressEvent(self._event(target.center()))
        self.owner.media_track_id = "new-track"
        self.window.mouseReleaseEvent(self._event(target.center()))
        self.assertEqual(self.provider.calls, [])

    def test_word_press_is_fenced_when_repaint_replaces_cached_track(self):
        self.owner.media_track_id = "old-track"
        self.owner.timed_lyrics = [
            (0.0, "first line"), (20.0, "next"), (40.0, "later"), (60.0, "last"),
        ]
        self.owner.timed_lyric_units = [
            (TimedLyricUnit(2.0, 4.0, "first "), TimedLyricUnit(4.0, 6.0, "line")),
            (), (),
        ]
        self.owner.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]
        self.window._browse_track_key = self.owner.media_track_id
        self._render()
        target = next(iter(self.window._lyric_word_hit_rects.values()))[0]
        self.window.mousePressEvent(self._event(target.center()))
        self.owner.media_track_id = "new-track"
        self._render()
        self.window.mouseReleaseEvent(self._event(target.center()))
        self.assertEqual(self.provider.calls, [])

    def test_estimated_or_misaligned_units_do_not_create_word_targets(self):
        self.owner.media_track_id = "guard-track"
        self.owner.timed_lyrics = [
            (0.0, "first line"), (20.0, "next"), (40.0, "later"), (60.0, "last"),
        ]
        self.owner.timed_lyric_units = [
            (
                SimpleNamespace(start_seconds=2.0, end_seconds=4.0, text="first ", estimated=True),
                SimpleNamespace(start_seconds=4.0, end_seconds=6.0, text="line", estimated=True),
            ),
            (), (),
        ]
        self.owner.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]
        self.window._browse_track_key = self.owner.media_track_id
        self._render()
        self.assertFalse(self.window._lyric_word_hit_rects)

    def test_wrapped_word_keeps_fragment_hit_rects_separate(self):
        long_word = "x" * 180
        self.owner.media_track_id = "wrapped-track"
        self.owner.timed_lyrics = [
            (0.0, "first " + long_word), (20.0, "next"),
            (40.0, "later"), (60.0, "last"),
        ]
        self.owner.timed_lyric_units = [
            (TimedLyricUnit(2.0, None, "first "), TimedLyricUnit(4.0, None, long_word)),
            (), (),
        ]
        self.owner.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]
        self.window._browse_track_key = self.owner.media_track_id
        self._render()
        fragments = [
            rect for (row, unit, _fragment), (rect, _seconds)
            in self.window._lyric_word_hit_rects.items()
            if row == 0 and unit == 1
        ]
        self.assertGreaterEqual(len(fragments), 2)
        self.assertGreaterEqual(len({round(rect.top(), 2) for rect in fragments}), 2)
        self.assertLessEqual(max(rect.height() for rect in fragments), 48.0)

    def test_timed_parenthetical_word_uses_its_own_visible_layout(self):
        self.owner.media_track_id = "aside-track"
        self.owner.timed_lyrics = [
            (0.0, "first (echo)"), (20.0, "next"),
            (40.0, "later"), (60.0, "last"),
        ]
        self.owner.timed_lyric_units = [
            (TimedLyricUnit(2.0, None, "first "), TimedLyricUnit(4.0, None, "(echo)")),
            (), (),
        ]
        self.owner.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]
        self.window._browse_track_key = self.owner.media_track_id
        self._render()
        self._render()
        target = next(
            (rect for (row, unit, _fragment), (rect, _seconds)
             in self.window._lyric_word_hit_rects.items()
             if row == 0 and unit == 1),
            None,
        )
        self.assertIsNotNone(target)
        self.assertEqual(self.window._lyric_word_target_at(target.center())[2], 4.0)
        self.window.mousePressEvent(self._event(target.center()))
        self.window.mouseReleaseEvent(self._event(target.center()))
        self.assertEqual(self.provider.calls, [(2.5, "aside-track")])

    def test_ambiguous_parenthetical_source_span_falls_back_safely(self):
        layers = split_display_layers("first (echo)")
        self.assertIsNone(
            self.window._parenthetical_source_offsets(
                "first (echo) and echo", layers, "echo",
            )
        )


if __name__ == "__main__":
    unittest.main()
