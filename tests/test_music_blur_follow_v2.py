from __future__ import annotations

import unittest
from unittest.mock import patch

from PIL import ImageFilter
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
from local.translation_style_variants import B, C, CLASSIC
from tests.test_music_fullscreen import _Owner


class MusicBlurFollowV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _Owner()
        self.window = MusicSurfaceWindow(self.owner)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def test_shallow_blur_covers_two_neighbor_groups_on_both_sides(self):
        self.assertEqual(
            tuple(self.window._lyric_group_blur_level(row, 3) for row in range(7)),
            (2, 1, 1, 0, 1, 1, 2),
        )

    def test_duet_columns_count_as_one_distance_and_share_focus(self):
        pairs = (2, 5)
        self.assertEqual(
            tuple(self.window._lyric_group_blur_level(row, 2, pairs) for row in range(9)),
            (1, 1, 0, 0, 1, 1, 1, 2, 2),
        )

    def test_focus_change_does_not_change_blur_before_scroll_moves(self):
        positions = tuple(float(row * 100) for row in range(7))
        ranges = tuple((row, row + 1) for row in range(7))
        old, _ = self.window._lyric_blur_follow_levels("song", 2, positions, positions, ranges)
        targets = tuple(position - 100.0 for position in positions)
        before_motion, moved = self.window._lyric_blur_follow_levels("song", 3, positions, targets, ranges)
        self.assertFalse(moved)
        self.assertEqual(before_motion, old)
        moving_positions = tuple(position - 1.0 for position in positions)
        after_motion, moved = self.window._lyric_blur_follow_levels("song", 3, moving_positions, targets, ranges)
        self.assertTrue(moved)
        self.assertEqual(after_motion[3], 0.0)
        self.assertGreater(after_motion[2], 0.0)
        self.assertLess(after_motion[2], 1.0)
        self.assertGreater(after_motion[0], 1.0)
        self.assertLess(after_motion[0], 2.0)

    def test_staggered_rows_follow_their_own_physical_travel(self):
        positions = tuple(float(row * 100) for row in range(7))
        ranges = tuple((row, row + 1) for row in range(7))
        self.window._lyric_blur_follow_levels("song", 2, positions, positions, ranges)
        targets = tuple(position - 100.0 for position in positions)
        self.window._lyric_blur_follow_levels("song", 3, positions, targets, ranges)
        staggered = tuple(position - (60.0 if row == 0 else 0.0) for row, position in enumerate(positions))
        levels, _ = self.window._lyric_blur_follow_levels("song", 3, staggered, targets, ranges)
        self.assertAlmostEqual(levels[0], 1.648)
        self.assertEqual(levels[5], 2.0)  # Its delayed spring has not started.
        completed, _ = self.window._lyric_blur_follow_levels("song", 3, targets, targets, ranges)
        self.assertEqual(completed, (2.0, 1.0, 1.0, 0.0, 1.0, 1.0, 2.0))

    def test_growing_translation_reservation_does_not_restart_a_blur_fade(self):
        positions = tuple(float(row * 100) for row in range(7))
        ranges = tuple((row, row + 1) for row in range(7))
        self.window._lyric_blur_follow_levels("song", 2, positions, positions, ranges)
        targets = tuple(position - 100.0 for position in positions)
        half_positions = tuple(position - 50.0 for position in positions)
        half, _ = self.window._lyric_blur_follow_levels("song", 3, half_positions, targets, ranges)
        farther_targets = tuple(position - 180.0 for position in positions)
        enlarged, _ = self.window._lyric_blur_follow_levels("song", 3, half_positions, farther_targets, ranges)
        self.assertEqual(enlarged, half)
        self.assertAlmostEqual(enlarged[0], 1.5)

    def test_duet_voices_use_the_same_travel_even_with_different_visual_rows(self):
        positions = (0.0, 100.0, 200.0, 250.0, 300.0, 400.0)
        ranges = tuple((row, row + 1) for row in range(6))
        pairs = (2,)
        self.window._lyric_blur_follow_levels("song", 1, positions, positions, ranges, pair_starts=pairs)
        targets = tuple(position - 100.0 for position in positions)
        # Voice 2 has an unrelated first-line offset; it must never acquire a
        # different degree from the first voice of its grouped sentence.
        moving = tuple(position - (20.0 if row == 2 else 0.0) for row, position in enumerate(positions))
        levels, _ = self.window._lyric_blur_follow_levels("song", 4, moving, targets, ranges, pair_starts=pairs)
        self.assertEqual(levels[2], levels[3])

    def test_fractional_blur_draws_reuse_two_cached_degrees_without_filtering(self):
        text = "A deliberately wrapped lyric sentence with several visual rows"
        font = QFont("Arial")
        font.setPixelSize(34)
        layout, height = self.window._wrapped_lyric_layout(text, font, 280.0)
        self.assertGreater(layout.lineCount(), 1)
        offsets = tuple(float(row * 2) for row in range(layout.lineCount()))
        output = QImage(460, 320, QImage.Format.Format_ARGB32_Premultiplied)
        output.fill(Qt.GlobalColor.transparent)
        with QPainter(output) as painter:
            for degree in (0, 1, 2):
                self.window._draw_sprung_cached_wrapped_layout(
                    painter, layout, QPointF(30.0, 30.0), text, font,
                    280.0, height, QColor(255, 255, 255), offsets, blur_level=degree,
                )
                self.window._draw_future_wrapped_layout(
                    painter, layout, QPointF(30.0, 30.0), text, font,
                    280.0, height, offsets, blur_level=degree,
                )
        cache_count = len(self.window._lyric_blur_cache)
        with patch("local.music_surface_window.ImageFilter.GaussianBlur", side_effect=AssertionError("per-frame filter")):
            with QPainter(output) as painter:
                for frame in range(1, 160):
                    degree = frame / 80.0
                    self.window._draw_sprung_cached_wrapped_layout(
                        painter, layout, QPointF(30.0, 30.0), text, font,
                        280.0, height, QColor(255, 255, 255), offsets, blur_level=degree,
                    )
                    self.window._draw_future_wrapped_layout(
                        painter, layout, QPointF(30.0, 30.0), text, font,
                        280.0, height, offsets, blur_level=degree,
                    )
        self.assertEqual(len(self.window._lyric_blur_cache), cache_count)

    def test_fractional_degree_preserves_the_full_outer_blur_bleed(self):
        self.assertEqual(self.window._lyric_blur_layers(1.25), ((1, 0.75), (2, 0.25)))
        self.assertEqual(self.window._lyric_blur_bleed(1.25), self.window._lyric_blur_bleed(2))

    def _wrapped_blur_prewarm_inputs(self):
        self.owner.media_track_id = "wrapped-follow-prewarm"
        self.owner.media_is_playing = True
        self.window.resize(960, 2160)
        font = self.window._surface_font(28.0)
        secondary_font = self.window._surface_font(20.0)
        lines = [f"Primary row {row}\u2028wrapped words" for row in range(11)]
        secondary_lines = [f"Secondary row {row}\u2028wrapped translation" for row in range(11)]
        # Repeated text shares its raster and must not duplicate queue jobs.
        lines[1] = lines[0]
        secondary_lines[1] = secondary_lines[0]
        lines[4] = "Short past row"
        layouts, row_tops = self.window._song_lyric_layouts(
            lines, font, 320.0, 20.0,
            secondary_lines=tuple(secondary_lines), secondary_font=secondary_font,
        )
        token = object()
        positions = self.window._song_visual_tops
        targets = tuple(position - 100.0 for position in positions)
        halfway = tuple(position - 50.0 for position in positions)
        ranges = self.window._song_visual_ranges
        self.window._lyric_blur_follow_levels(token, 5, positions, positions, ranges)
        levels, _ = self.window._lyric_blur_follow_levels(token, 6, halfway, targets, ranges)
        self.assertEqual(levels[3], 1.5)
        self.window._lyric_blur_prewarm_token = None
        return dict(
            lines=lines, layouts=layouts, row_tops=row_tops, font=font, width=320.0,
            lyric_clip=QRectF(self.window.rect()), bleed=60.0, future_color=QColor(160, 90, 80),
            active=6, next_start=21.0, position=20.0, now=100.0, manual_view=False,
            secondary_lines=tuple(secondary_lines), secondary_font=secondary_font,
            blur_follow_token=token,
        )

    def test_wrapped_prewarm_keeps_inflight_primary_and_secondary_layers_under_cache_pressure(self):
        for dpr in (1.0, 2.0):
            for style in (CLASSIC, B, C):
                with (
                    self.subTest(dpr=dpr, style=style.name),
                    patch.object(self.window, "devicePixelRatioF", return_value=dpr),
                    patch.object(self.window, "_translation_style", style),
                    patch.object(self.window, "_display_playing_state", return_value=True),
                ):
                    inputs = self._wrapped_blur_prewarm_inputs()
                    # Fill the existing cache to its byte limit. Prewarming
                    # must evict old work without exceeding either bound.
                    stale = QImage(300, 300, QImage.Format.Format_ARGB32_Premultiplied)
                    stale.fill(Qt.GlobalColor.white)
                    for index in range(self.window.LYRIC_BLUR_CACHE_MAX_ENTRIES):
                        self.window._blurred_lyric_raster(
                            stale, 2, QColor(255, 255, 255), cache_key=("stale", index),
                        )
                    self.window._queue_next_lyric_blurs(**inputs)
                    jobs = tuple(self.window._lyric_blur_prewarm_queue)
                    row_text = inputs["lines"][3]
                    self.assertEqual({job[6] for job in jobs if job[0] == row_text and job[7] is not None}, {1, 2})
                    self.assertEqual({job[6] for job in jobs if job[0] == row_text and job[7] is None}, {2})
                    self.assertEqual(
                        {job[6] for job in jobs if job[0] == inputs["secondary_lines"][3]},
                        {1} if style is B else {1, 2},
                    )
                    self.assertEqual(
                        [(job[6], job[7]) for job in jobs if job[0] == inputs["lines"][4]],
                        [(2, None)],
                    )
                    # Far past rows and future rows do not gain both degrees.
                    for row, degree in ((0, 2), (9, 1)):
                        self.assertEqual({job[6] for job in jobs if job[0] == inputs["lines"][row]}, {degree})
                    for text in (inputs["lines"][0], inputs["secondary_lines"][0]):
                        forms = [(job[6], job[7]) for job in jobs if job[0] == text]
                        self.assertTrue(forms)
                        self.assertEqual(len(forms), len(set(forms)))

                    with patch("local.music_surface_window.ImageFilter.GaussianBlur", wraps=ImageFilter.GaussianBlur) as blur:
                        for tick in range(3 * len(jobs) + 1):
                            if not self.window._lyric_blur_prewarm_queue:
                                break
                            before = blur.call_count
                            self.window._advance_lyric_blur_prewarm(100.0 + tick / 60.0)
                            self.assertLessEqual(blur.call_count - before, 1)
                            self.assertLessEqual(len(self.window._lyric_blur_cache), self.window.LYRIC_BLUR_CACHE_MAX_ENTRIES)
                            self.assertLessEqual(self.window._lyric_blur_cache_bytes, self.window.LYRIC_BLUR_CACHE_MAX_BYTES)
                    self.assertFalse(self.window._lyric_blur_prewarm_queue)
                    self.assertGreater(blur.call_count, 0)
                    self.assertTrue(all(
                        image.devicePixelRatio() == min(dpr, self.window.BLURRED_LYRIC_DPR_CAP)
                        for key, (image, _size) in self.window._lyric_blur_cache.items()
                        if key[0] != "stale"
                    ))

                    output = QImage(640, 480, QImage.Format.Format_ARGB32_Premultiplied)
                    output.fill(Qt.GlobalColor.transparent)
                    primary, height = inputs["layouts"][3]
                    secondary, small_height, _top = self.window._song_secondary_layouts[3]
                    with patch("local.music_surface_window.ImageFilter.GaussianBlur", side_effect=AssertionError("moving wrapped blur")):
                        with QPainter(output) as painter:
                            for layout, text, font, row_height, factor in (
                                (primary, row_text, inputs["font"], height, 1.0),
                                (secondary, inputs["secondary_lines"][3], inputs["secondary_font"], small_height, style.blur_factor),
                            ):
                                self.assertGreater(layout.lineCount(), 1)
                                for degree, offsets in (
                                    (1.5, tuple(2.0 * index for index in range(layout.lineCount()))),
                                    (2.0, (0.0,) * layout.lineCount()),
                                ):
                                    self.window._draw_sprung_cached_wrapped_layout(
                                        painter, layout, QPointF(20.0, 20.0), text, font,
                                        320.0, row_height, self.window._sung_lyric_color(), offsets,
                                        blur_level=degree * factor,
                                    )

    def test_wrapped_prewarm_falls_back_when_follow_state_does_not_match(self):
        for mismatch in ("missing", "token", "focus", "rows"):
            with self.subTest(mismatch=mismatch), patch.object(self.window, "_display_playing_state", return_value=True):
                inputs = self._wrapped_blur_prewarm_inputs()
                state = self.window._lyric_blur_follow_state
                if mismatch == "missing":
                    self.window._lyric_blur_follow_state = None
                elif mismatch == "token":
                    inputs["blur_follow_token"] = object()
                elif mismatch == "focus":
                    state["focus"] = 5
                else:
                    state["levels"] = state["levels"][:-1]
                self.window._queue_next_lyric_blurs(**inputs)
                jobs = self.window._lyric_blur_prewarm_queue
                # Row 3 is already deep at focus 6; row 4's wrapped secondary
                # still needs shallow isolated lines, despite its short primary.
                for text, degrees in ((inputs["lines"][3], {2}), (inputs["secondary_lines"][4], {1, 2})):
                    self.assertEqual(
                        {job[6] for job in jobs if job[0] == text and job[7] is not None},
                        degrees,
                    )

    def test_countdown_dot_blur_prewarms_in_the_same_bounded_queue(self):
        self.owner.media_track_id = "intro-prewarm"
        self.owner.media_is_playing = True
        self.window._song_layout_key = ("intro-layout",)
        font = QFont("Arial")
        font.setPixelSize(34)
        text = "· · ·"
        layout, height = self.window._wrapped_lyric_layout(text, font, 280.0)
        lines = [text, "First", "Second", "Third", "Fourth"]
        layouts = tuple((layout, height) for _ in lines)
        with patch.object(self.window, "_display_playing_state", return_value=True):
            self.window._queue_next_lyric_blurs(
                lines, layouts, tuple(row * 80.0 for row in range(5)),
                font, 280.0, QRectF(0.0, 0.0, 500.0, 400.0),
                20.0, QColor(160, 90, 80), 2, 10.0, 9.0, 100.0,
                manual_view=False,
            )
            self.assertTrue(any(job[-1] == "intro-countdown" for job in self.window._lyric_blur_prewarm_queue))
            for tick in range(30):
                if not self.window._lyric_blur_prewarm_queue:
                    break
                before = len(self.window._lyric_blur_cache)
                self.window._advance_lyric_blur_prewarm(100.0 + tick * 0.04)
                self.assertLessEqual(len(self.window._lyric_blur_cache) - before, 1)
        self.assertFalse(self.window._lyric_blur_prewarm_queue)
        output = QImage(400, 160, QImage.Format.Format_ARGB32_Premultiplied)
        output.fill(Qt.GlobalColor.transparent)
        with patch("local.music_surface_window.ImageFilter.GaussianBlur", side_effect=AssertionError("paint-time dot blur")):
            with QPainter(output) as painter:
                self.window._paint_intro_countdown(
                    painter, QRectF(20.0, 20.0, 280.0, height), font, 1.0, 2,
                )


if __name__ == "__main__":
    unittest.main()
