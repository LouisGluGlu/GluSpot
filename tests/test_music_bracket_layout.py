from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from local.music_surface_window import MusicSurfaceWindow


class _BracketOwner(QWidget):
    def __init__(self):
        super().__init__()
        self._music_windowed_preference = False
        self.music_intro_cleanup_enabled = False
        self.enable_dual_line_lyrics = True
        self.music_surface_reading_strategy = "gold"
        self.media_track_id = "attached-brackets"
        self.media_is_playing = True
        self.media_position_seconds = 1.0
        self.timed_lyrics = [
            (0.0, "For so long (long)\n翻译：漫长岁月里（岁月里）"),
            (20.0, "Second line"),
            (40.0, "Third line"),
            (60.0, "Fourth line"),
        ]
        self.timed_lyric_units = [(), (), (), ()]
        self.timed_lyric_timing_kinds = ["line"] * 4


class MusicBracketLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _BracketOwner()
        self.window = MusicSurfaceWindow(self.owner)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def render(self, width: int, height: int, now: float):
        self.window.resize(width, height)
        image = QImage(width, height, QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            self.window.render(image)
        return image

    def render_at_position(self, position: float, now: float, *, width: int = 960, height: int = 540):
        self.owner.media_position_seconds = position
        self.owner.media_raw_position_seconds = position
        self.owner.media_position_sampled_at = now
        self.owner.media_position_observed_seconds = position
        # These samples model verified player seeks. The motion clock must
        # accept each requested point before the display-only aside clock is
        # compared with it; arbitrary painted positions are not source time.
        self.owner.media_position_source = "local-seek"
        self.owner._estimated_media_position = lambda _now: position
        return self.render(width, height, now)

    def test_bracket_stack_has_no_extra_sentence_gap_at_window_and_fullscreen_sizes(self):
        for index, (width, height) in enumerate(((960, 540), (1280, 720), (1920, 1080))):
            with self.subTest(size=(width, height)):
                self.window._fullscreen = index == 2
                self.render(width, height, 100.0 + index)
                _secondary, secondary_height, secondary_top = self.window._song_secondary_layouts[0]
                _bracket, bracket_height, bracket_top = self.window._song_parenthetical_layouts[0]
                _translated, translated_height, translated_top = (
                    self.window._song_parenthetical_translation_layouts[0]
                )
                self.assertAlmostEqual(bracket_top, self.window._song_layouts[0][1])
                self.assertAlmostEqual(translated_top, bracket_top + bracket_height)
                self.assertGreater(secondary_top, translated_top + translated_height)
                self.assertAlmostEqual(
                    self.window._song_parenthetical_extra_heights[0],
                    bracket_height + translated_height,
                )
                sentence_gap = self.window._song_row_tops[1] - self.window._song_row_heights[0]
                self.assertGreaterEqual(sentence_gap, height * 0.035)

    def test_wrapped_original_without_translation_attaches_bracket_to_last_visual_line(self):
        self.owner.timed_lyrics[0] = (
            0.0, "But my head is holding up my heart, my heart (echo)",
        )
        self.render(960, 540, 100.0)
        layout, primary_height = self.window._song_layouts[0]
        self.assertGreater(layout.lineCount(), 1)
        self.assertIsNone(self.window._song_secondary_layouts[0])
        _bracket, _height, bracket_top = self.window._song_parenthetical_layouts[0]
        last = layout.lineAt(layout.lineCount() - 1)
        self.assertAlmostEqual(bracket_top, primary_height)
        self.assertAlmostEqual(bracket_top, last.y() + last.height())

    def test_unread_bracket_is_painted_immediately_below_its_sentence_block(self):
        self.owner.music_surface_reading_strategy = "low_perception"
        self.render(960, 540, 100.0)
        captured = {}
        original = self.window._paint_parenthetical_highlight
        original_reading = self.window._paint_wrapped_lifted_lyric

        def record(painter, row, text, font, layout, progress, *, line_offsets=()):
            offsets = line_offsets or (0.0,)
            # The primary reading row reserves the entire companion stack.
            # Measure the shaped primary's own final line instead of treating
            # the stack's row.bottom() as its sung sentence baseline.
            last = layout.lineAt(layout.lineCount() - 1)
            captured[text] = (
                row.top() + offsets[0],
                row.top() + last.y() + last.height() + offsets[-1], progress,
            )
            return original(
                painter, row, text, font, layout, progress, line_offsets=line_offsets,
            )

        def record_reading(painter, row, text, font, layout, progress, *args, **kwargs):
            if text in {"For so long", "漫长岁月里"}:
                offsets = kwargs.get("line_offsets") or (0.0,)
                last = layout.lineAt(layout.lineCount() - 1)
                captured[text] = (
                    row.top() + offsets[0],
                    row.top() + last.y() + last.height() + offsets[-1], progress,
                )
            return original_reading(painter, row, text, font, layout, progress, *args, **kwargs)

        static = self.window._draw_sprung_cached_wrapped_layout

        def record_static(painter, layout, origin, text, font, width, height, color, offsets=(), **kwargs):
            captured[text] = (origin.y() + (offsets[0] if offsets else 0.0),
                              origin.y() + height + (offsets[-1] if offsets else 0.0), 1.0)
            return static(painter, layout, origin, text, font, width, height, color, offsets, **kwargs)

        with (
            patch.object(self.window, "_paint_parenthetical_highlight", side_effect=record),
            patch.object(self.window, "_paint_wrapped_lifted_lyric", side_effect=record_reading),
            patch.object(self.window, "_draw_sprung_cached_wrapped_layout", side_effect=record_static),
        ):
            self.render(960, 540, 100.6)
        self.assertEqual(captured["long"][2], 0.0)
        self.assertEqual(captured["岁月里"][2], 0.0)
        self.assertAlmostEqual(captured["long"][0], captured["For so long"][1])
        self.assertAlmostEqual(captured["岁月里"][0], captured["long"][1])
        self.assertGreater(captured["漫长岁月里"][0], captured["岁月里"][1])

    @staticmethod
    def _short_parenthetical_fixture(owner):
        owner.timed_lyrics = [
            (0.0, "I know every word you said to me so long ago (echo)\n翻译：我记得你曾对我说过的每个字（回声）"),
            (4.0, "Now we keep walking through another quiet summer"),
            (8.0, "Later we remember everything together"),
            (12.0, "Final words carry us home"),
        ]
        owner.timed_lyric_units = [(), (), (), ()]
        owner.timed_lyric_timing_kinds = ["line"] * 4
        owner.enable_dual_line_lyrics = True
        owner.music_surface_reading_strategy = "gold"

    def test_short_parenthetical_reads_across_main_to_next_quarter_then_blurs(self):
        self._short_parenthetical_fixture(self.owner)
        self.window.resize(960, 540)
        highlighted = []
        blurred = []
        current_draws = {}
        position = [0.0]
        original_highlight = self.window._paint_parenthetical_highlight
        original_reading = self.window._paint_wrapped_lifted_lyric
        original_static = self.window._draw_sprung_cached_wrapped_layout
        original_future = self.window._draw_future_wrapped_layout
        original_cold = self.window._paint_cold_emphasis_reading
        row_dispatch = set()

        def dispatch_row(text, draw):
            is_current = text.startswith("Now we keep walking")
            outer = is_current and text not in row_dispatch
            if outer:
                current_draws[position[0]] = current_draws.get(position[0], 0) + 1
                row_dispatch.add(text)
            try:
                return draw()
            finally:
                if outer:
                    row_dispatch.remove(text)

        def record_highlight(painter, row, text, font, layout, progress, *, line_offsets=()):
            if text in {"echo", "回声"}:
                highlighted.append((position[0], text, progress, row.top()))
            return original_highlight(
                painter, row, text, font, layout, progress, line_offsets=line_offsets,
            )

        def record_static(painter, layout, origin, text, font, width, height, color, offsets=(), **kwargs):
            if text.startswith("I know every word"):
                main_tops[position[0]] = origin.y()
            if text in {"echo", "回声"}:
                blurred.append((position[0], text, kwargs.get("blur_level", 0.0), origin.y()))
            return dispatch_row(text, lambda: original_static(
                painter, layout, origin, text, font, width, height, color, offsets, **kwargs,
            ))

        def record_future(painter, layout, origin, text, font, width, height, offsets=(), blur_level=0):
            if text.startswith("I know every word"):
                main_tops[position[0]] = origin.y()
            return dispatch_row(text, lambda: original_future(
                painter, layout, origin, text, font, width, height, offsets, blur_level,
            ))

        def record_cold(painter, row, text, font, layout, progress, unread_color, line_offsets, **kwargs):
            if text.startswith("I know every word"):
                main_tops[position[0]] = row.top()
            return dispatch_row(text, lambda: original_cold(
                painter, row, text, font, layout, progress, unread_color, line_offsets, **kwargs,
            ))

        def record_reading(painter, row, text, font, layout, progress, *args, **kwargs):
            if text.startswith("I know every word"):
                main_tops[position[0]] = row.top()
            return dispatch_row(text, lambda: original_reading(
                painter, row, text, font, layout, progress, *args, **kwargs,
            ))

        main_tops = {}

        def sample(at, now):
            position[0] = at
            with (
                patch.object(self.window, "_paint_parenthetical_highlight", side_effect=record_highlight),
                patch.object(self.window, "_paint_wrapped_lifted_lyric", side_effect=record_reading),
                patch.object(self.window, "_draw_sprung_cached_wrapped_layout", side_effect=record_static),
                patch.object(self.window, "_draw_future_wrapped_layout", side_effect=record_future),
                patch.object(self.window, "_paint_cold_emphasis_reading", side_effect=record_cold),
            ):
                self.render_at_position(at, now)
            self.assertAlmostEqual(self.window._lyric_motion.last_position, at)

        sample(0.5, 99.0)  # Let the annotation entrance settle before following its row.
        sample(2.4, 100.0)  # 80% of A's estimated read interval [0, 3].
        self.assertEqual(self.window._last_lyric_emphasis_mode, "none")
        self.assertAlmostEqual(self.window._parenthetical_read_window(0, self.window._display_lyrics()[1]).start_seconds, 2.4)
        self.assertTrue(any(at == 2.4 and text == "echo" and progress == 0.0 for at, text, progress, _top in highlighted))

        sample(4.0, 101.0)  # C starts; the same aside is still on A's row.
        at_c_start = next(progress for at, text, progress, _top in highlighted if at == 4.0 and text == "echo")
        self.assertAlmostEqual(at_c_start, (4.0 - 2.4) / (4.75 - 2.4))
        self.assertEqual(sum(at == 4.0 and text == "echo" for at, text, _p, _top in highlighted), 1)
        self.assertEqual(current_draws[4.0], 1)
        c_start_top = next(top for at, text, _progress, top in highlighted if at == 4.0 and text == "echo")
        attached_offset_at_start = next(top for at, top in main_tops.items() if at == 2.4)
        attached_offset_at_c = main_tops[4.0]
        aside_layer_top = self.window._song_parenthetical_layouts[0][2]
        self.assertAlmostEqual(c_start_top - attached_offset_at_c, aside_layer_top, delta=1.0)
        aside_top_at_start = next(top for at, text, _progress, top in highlighted if at == 2.4 and text == "echo")
        self.assertAlmostEqual(aside_top_at_start - attached_offset_at_start, aside_layer_top, delta=1.0)
        self.assertAlmostEqual(
            c_start_top - attached_offset_at_c,
            aside_top_at_start - attached_offset_at_start,
            delta=1.0,
        )
        self.assertTrue(any(at == 4.0 and text == "回声" for at, text, _progress, _top in highlighted))
        _lines, timed = self.window._display_lyrics()
        self.assertEqual(len(timed), 4)
        self.assertEqual(sum("Now we keep walking" in text for _start, text in timed), 1)
        self.assertFalse(any(text.strip() == "(echo)" for _start, text in timed))

        sample(4.74, 101.74)
        near_quarter = next(progress for at, text, progress, _top in highlighted if at == 4.74 and text == "echo")
        self.assertLess(near_quarter, 1.0)
        self.assertGreater(near_quarter, at_c_start)

        sample(4.75, 101.75)  # End of C's first quarter starts its ordinary blur settle.
        endpoint_blur = next(value for at, text, value, _top in blurred if at == 4.75 and text == "echo")
        self.assertEqual(endpoint_blur, 0.0)
        sample(4.84, 101.84)
        halfway_blur = next(value for at, text, value, _top in blurred if at == 4.84 and text == "echo")
        self.assertGreater(halfway_blur, endpoint_blur)
        sample(4.94, 101.94)
        settled_blur = next(value for at, text, value, _top in blurred if at == 4.94 and text == "echo")
        self.assertGreater(settled_blur, halfway_blur)
        self.assertTrue(any(at == 4.0 and text == "echo" and top == c_start_top for at, text, _p, top in highlighted))

    def test_parenthetical_clock_is_shared_by_cover_modes_fullscreen_and_translation_toggle(self):
        self._short_parenthetical_fixture(self.owner)
        progress_by_mode = {}
        now = 110.0
        for layout in ("lyrics", "cover_lyrics"):
            for fullscreen in (False, True):
                with self.subTest(layout=layout, fullscreen=fullscreen):
                    self.owner.media_track_id = f"bracket-{layout}-{fullscreen}"
                    self.window.set_content_layout(layout, animate=False)
                    self.window._fullscreen = fullscreen
                    position = 3.575  # Halfway through the attached display interval.
                    self.render_at_position(position, now - 0.6)
                    found = []
                    original = self.window._paint_parenthetical_highlight

                    def record(painter, row, text, font, lyric_layout, progress, *, line_offsets=()):
                        if text in {"echo", "回声"}:
                            found.append((text, progress))
                        return original(
                            painter, row, text, font, lyric_layout, progress,
                            line_offsets=line_offsets,
                        )

                    with patch.object(self.window, "_paint_parenthetical_highlight", side_effect=record):
                        self.render_at_position(position, now)
                    self.assertTrue(any(text == "echo" for text, _progress in found))
                    self.assertTrue(any(text == "回声" for text, _progress in found))
                    progress_by_mode[(layout, fullscreen)] = next(
                        progress for text, progress in found if text == "echo"
                    )
                    self.assertAlmostEqual(progress_by_mode[(layout, fullscreen)], 0.5)

                    self.owner.enable_dual_line_lyrics = False
                    self.render_at_position(position, now + 1.0)  # Begin the existing translation exit.
                    found.clear()
                    with patch.object(self.window, "_paint_parenthetical_highlight", side_effect=record):
                        self.render_at_position(position, now + 2.0)
                    self.assertTrue(any(text == "echo" for text, _progress in found))
                    self.assertFalse(any(text == "回声" for text, _progress in found))
                    self.owner.enable_dual_line_lyrics = True
                    now += 2.0

        self.assertEqual(len(set(progress_by_mode.values())), 1)

    def test_parenthetical_highlight_recalculates_after_pause_and_backward_seek(self):
        self._short_parenthetical_fixture(self.owner)
        self.owner.media_is_playing = False
        self.window.resize(960, 540)
        seen = []
        original = self.window._paint_parenthetical_highlight

        def record(painter, row, text, font, layout, progress, *, line_offsets=()):
            if text == "echo":
                seen.append((self.window._lyric_motion.last_position, progress))
            return original(painter, row, text, font, layout, progress, line_offsets=line_offsets)

        self.render_at_position(2.4, 119.0)
        previous_position = None
        for position, now in ((3.0, 120.0), (3.0, 120.5), (2.4, 121.0)):
            self.owner.media_position_source = "local-seek" if (
                previous_position is not None and position < previous_position
            ) else ""
            with patch.object(self.window, "_paint_parenthetical_highlight", side_effect=record):
                self.render_at_position(position, now)
            self.assertAlmostEqual(self.window._lyric_motion.last_position, position)
            previous_position = position
        self.assertEqual(seen[0], seen[1])
        self.assertGreater(seen[1][1], 0.0)
        self.assertEqual(seen[2][1], 0.0)

    def test_cold_short_aside_still_has_visible_light_before_character_assets_are_ready(self):
        font = self.window._surface_font(24)
        layout, height = self.window._wrapped_lyric_layout("echo", font, 220.)
        row = QRectF(50, 40, 220, height)
        images = []
        for highlight in (False, True):
            image = QImage(320, 160, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            with QPainter(image) as painter:
                painter.setOpacity(.4)
                if highlight:
                    with patch.object(self.window, "_lyric_emphasis_assets_ready", return_value=False):
                        self.window._paint_parenthetical_highlight(painter, row, "echo", font, layout, .5)
                else:
                    self.window._paint_cold_emphasis_reading(
                        painter, row, "echo", font, layout, .5, self.window._future_lyric_color(row), (),
                    )
            images.append(image)
        # The mandatory treatment cannot disappear just because a late
        # provider result arrives before per-character halos have warmed.
        self.assertNotEqual(images[0], images[1])
        self.assertGreater(sum(images[1].pixelColor(x, y).alpha() for x in range(320) for y in range(160)),
                           sum(images[0].pixelColor(x, y).alpha() for x in range(320) for y in range(160)))


if __name__ == "__main__":
    unittest.main()
