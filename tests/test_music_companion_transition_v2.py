from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF
from PySide6.QtGui import QFont, QImage
from PySide6.QtWidgets import QApplication

from api.models import TimedLyricUnit
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_lyric_visual_layers import _LayerOwner


@dataclass(frozen=True)
class _PaintedCompanion:
    text: str
    top: float
    bottom: float
    opacity: float
    progress: float | None
    blur: float
    color: tuple[int, int, int, int] | None = None
    real_progresses: tuple[float, ...] | None = None
    pixel_size: int = 0
    weight: int = 0


class MusicCompanionTransitionV2Tests(unittest.TestCase):
    """Exercise actual Qt paint dispatch; observers also call the real painters."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _LayerOwner()
        self.owner.music_sparse_highlight_seconds_per_unit = 2.0
        self.owner.music_surface_reading_lift_level = 0
        self.owner.media_position_source = "local-seek"
        self.owner.media_position_sampled_at = 100.0
        self.owner.set_enable_dual_line_lyrics = lambda enabled: setattr(
            self.owner, "enable_dual_line_lyrics", enabled,
        )
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _rows(self, first: str, second: str = "Another sentence continues here"):
        self.owner.timed_lyrics = [
            (0.0, first), (20.0, second),
            (40.0, "Third sentence continues here"),
            (60.0, "Fourth sentence continues here"),
        ]
        self.owner.timed_lyric_units = [()] * 4
        self.owner.timed_lyric_timing_kinds = ["line"] * 4

    def _render(self, now: float, position: float | None = None, *, fresh_sample: bool = True):
        if position is not None:
            self.owner.media_position_seconds = position
        if fresh_sample:
            self.owner.media_position_sampled_at = now
        painted: list[_PaintedCompanion] = []
        original_read = self.window._paint_wrapped_lifted_lyric
        original_whole = self.window._draw_sprung_cached_wrapped_layout
        original_future = self.window._draw_future_wrapped_layout
        original_block = self.window._draw_low_perception_block_wipe
        whole_depth = 0
        future_depth = 0

        def observe(painter, origin, layout, text, offsets, progress, blur=0.0, color=None, real_progresses=None):
            first = layout.lineAt(0)
            last = layout.lineAt(layout.lineCount() - 1)
            first_offset = offsets[0] if offsets else 0.0
            last_offset = offsets[-1] if offsets else 0.0
            top = painter.transform().map(QPointF(origin.x(), origin.y() + first.y() + first_offset)).y()
            bottom = painter.transform().map(QPointF(
                origin.x(), origin.y() + last.y() + last.height() + last_offset,
            )).y()
            painted.append(_PaintedCompanion(
                text, top, bottom, painter.opacity(), progress, float(blur),
                color.getRgb() if color is not None else None,
                real_progresses,
                layout.font().pixelSize(), int(layout.font().weight()),
            ))

        def read(painter, row, text, font, layout, progress, *args, **kwargs):
            observe(painter, row.topLeft(), layout, text, kwargs.get("line_offsets", ()), progress,
                    color=kwargs.get("read_color"), real_progresses=kwargs.get("real_progresses"))
            return original_read(painter, row, text, font, layout, progress, *args, **kwargs)

        def whole(painter, layout, origin, text, font, width, height, color, offsets, *args, **kwargs):
            nonlocal whole_depth
            if whole_depth == 0:
                blur = kwargs.get("blur_level", args[0] if args else 0.0)
                observe(painter, origin, layout, text, offsets, None, blur, color)
            whole_depth += 1
            try:
                return original_whole(
                    painter, layout, origin, text, font, width, height, color, offsets, *args, **kwargs,
                )
            finally:
                whole_depth -= 1

        def future(painter, layout, origin, text, font, width, height, offsets=(), *args, **kwargs):
            nonlocal future_depth
            if future_depth == 0:
                blur = kwargs.get("blur_level", args[0] if args else 0.0)
                observe(painter, origin, layout, text, offsets, 0.0, blur)
            future_depth += 1
            try:
                return original_future(painter, layout, origin, text, font, width, height, offsets, *args, **kwargs)
            finally:
                future_depth -= 1

        def block(painter, layers, progress, unread, **kwargs):
            poses = kwargs.get("layer_poses", ())
            for index, (layout, origin, offsets) in enumerate(layers):
                opacity, slide = poses[index] if index < len(poses) else (1.0, 0.0)
                painter.save()
                painter.setOpacity(painter.opacity() * opacity)
                painter.translate(0.0, slide)
                observe(painter, origin, layout, layout.text(), offsets, progress)
                painter.restore()
            return original_block(painter, layers, progress, unread, **kwargs)

        with (
            patch.object(self.window, "_paint_wrapped_lifted_lyric", side_effect=read),
            patch.object(self.window, "_draw_sprung_cached_wrapped_layout", side_effect=whole),
            patch.object(self.window, "_draw_future_wrapped_layout", side_effect=future),
            patch.object(self.window, "_draw_low_perception_block_wipe", side_effect=block),
            patch("local.music_surface_window.time.monotonic", return_value=now),
        ):
            image = QImage(960, 540, QImage.Format.Format_RGB32)
            self.window.render(image)
        return painted

    def _paint(self, painted, text):
        matches = [item for item in painted if item.text == text]
        self.assertTrue(matches, f"Native paint did not draw {text!r}: {[p.text for p in painted]}")
        return matches[0]

    def _toggle(self, now):
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            self.window._toggle_translation()

    def _wait_for_companion(self, now, position, text):
        """Drive normal display frames while neighboring rows reserve room."""
        for step in range(101):
            instant = now + step * 0.02
            at = position + step * 0.02
            painted = self._render(instant, at)
            if any(item.text == text and item.opacity > 0.001 for item in painted):
                return instant, at, painted
        self.fail(f"The entrance never drew {text!r} within two seconds")

    def _animate_for(self, now, position, duration):
        elapsed = 0.0
        painted = None
        while elapsed < duration - 1e-8:
            elapsed = min(duration, elapsed + 0.02)
            painted = self._render(now + elapsed, position + elapsed)
        return painted

    def test_obo_prefocused_aside_enters_before_its_reading_clock(self):
        self._rows("Alpha beta", "Next sentence starts here (aside)")
        self.owner.timed_lyric_units[0] = (
            SimpleNamespace(start_seconds=0.0, end_seconds=4.0, text="Alpha "),
            SimpleNamespace(start_seconds=4.0, end_seconds=8.0, text="beta"),
        )
        self.owner.timed_lyric_timing_kinds[0] = "unit"
        first = self._render(100.0, 9.0)
        entering = self._paint(self._render(100.08, 9.08), "aside")
        settled = self._paint(self._render(100.55, 9.55), "aside")
        self.assertFalse(any(item.text == "aside" and item.opacity > 0.001 for item in first))
        self.assertGreater(entering.opacity, 0.0)
        self.assertLess(entering.opacity, settled.opacity)
        self.assertEqual(entering.progress, 0.0)
        self.assertEqual(settled.progress, 0.0)

    def test_future_bracket_has_no_empty_lane_and_translation_moves_elastically_on_focus(self):
        main = "下一句在夜色里轻轻回响"
        translated = "The next sentence echoes"
        self._rows("Opening sentence continues", main + " (echo)\n翻译：" + translated + "（回声）")
        future = self._render(100.0, 3.0)
        future_primary = self._paint(future, main)
        future_translation = self._paint(future, translated)
        self.assertEqual(self.window._parenthetical_reserve_factors(0, 100.0)[1], 0.0)
        self.assertFalse(any(p.text in {"echo", "回声"} for p in future))
        gap_before = future_translation.top - future_primary.bottom
        self.assertLess(gap_before, future_primary.pixel_size * 0.5)

        focus = self._render(101.0, 20.0)
        main_at_focus = self._paint(focus, main)
        translated_at_focus = self._paint(focus, translated)
        self.assertAlmostEqual(
            translated_at_focus.top - main_at_focus.bottom, gap_before, delta=1.0,
        )
        entering = self._animate_for(101.0, 20.0, 0.08)
        aside = self._paint(entering, "echo")
        self.assertGreater(aside.opacity, 0.0)
        self.assertEqual(aside.progress, 0.0)
        early_gap = self._paint(entering, translated).top - self._paint(entering, main).bottom
        settled = self._animate_for(101.08, 20.08, 0.52)
        settled_gap = self._paint(settled, translated).top - self._paint(settled, main).bottom
        self.assertGreater(early_gap, gap_before)
        self.assertGreater(settled_gap, early_gap)
        self.assertAlmostEqual(self._paint(settled, "echo").top, self._paint(settled, main).bottom, delta=1.0)
        self.assertGreaterEqual(self._paint(settled, translated).top, self._paint(settled, "回声").bottom)

    def test_completed_aside_remains_white_and_follows_its_parent_upward_with_same_blur(self):
        main = "月光照耀着我今晚仍会继续轻轻歌唱"
        self._rows(main + " (echo)\n翻译：月光（回声）继续歌唱")
        self._render(100.0, 18.0)
        before_frame = self._render(100.6, 18.6)
        before = self._paint(before_frame, "echo")
        regular_translation = self._paint(before_frame, "月光继续歌唱")
        self.assertGreater(before.progress, 0.5)
        self.assertEqual(before.color, (220, 220, 220, 255))
        self.assertAlmostEqual(before.pixel_size / 2, regular_translation.pixel_size, delta=0.5)
        self.assertEqual(before.pixel_size, round((self.window.LYRIC_PIXEL_SIZE - 6) * self.window._surface_scale()))
        self.assertEqual(before.weight, int(QFont.Weight.Bold))
        self.assertEqual(regular_translation.weight, int(QFont.Weight.Normal))
        self._render(100.7, 20.2)
        completed = self._render(101.3, 20.8)
        main_paint = self._paint(completed, main)
        aside = self._paint(completed, "echo")
        translated_aside = self._paint(completed, "回声")
        self.assertIsNone(aside.progress)
        self.assertEqual(aside.color, (220, 220, 220, 255))
        self.assertEqual(main_paint.color, (255, 255, 255, 255))
        self.assertEqual(translated_aside.color, (255, 255, 255, 255))
        self.assertAlmostEqual(aside.opacity, main_paint.opacity)
        self.assertAlmostEqual(aside.blur, main_paint.blur)
        self.assertAlmostEqual(translated_aside.blur, main_paint.blur)
        self.assertGreater(aside.blur, 0.0)
        self.assertLess(aside.top, before.top)

    def test_cached_translation_toggle_has_visible_elastic_entrance(self):
        self.owner.enable_dual_line_lyrics = False
        self.window._translation_setting = False
        self._rows("月光照耀着我今晚仍会继续轻轻歌唱\n翻译：夜色依然温柔")
        self.assertFalse(any(p.text == "夜色依然温柔" for p in self._render(100.0, 4.0)))
        self._toggle(100.01)
        self._render(100.01, 4.0)
        began, position, _ = self._wait_for_companion(100.01, 4.01, "夜色依然温柔")
        early = self._paint(self._animate_for(began, position, 0.08), "夜色依然温柔")
        peak = self._paint(self._animate_for(began + 0.08, position + 0.08, 0.20), "夜色依然温柔")
        settled = self._paint(self._animate_for(began + 0.28, position + 0.28, 0.32), "夜色依然温柔")
        self.assertGreater(early.opacity, 0.0)
        self.assertLess(early.opacity, settled.opacity)
        self.assertLess(early.top, settled.top)
        self.assertGreater(peak.top, settled.top)

    def test_delayed_translation_starts_its_entrance_when_content_arrives(self):
        self.owner.enable_dual_line_lyrics = False
        self.window._translation_setting = False
        main = "月光照耀着我今晚仍会继续轻轻歌唱"
        self._rows(main)
        self._render(100.0, 4.0)
        self._toggle(100.1)
        self._render(100.8, 4.8)
        self.owner.timed_lyrics[0] = (0.0, main + "\n翻译：夜色依然温柔")
        arrival = self._render(100.9, 4.9)
        self.assertFalse(any(p.text == "夜色依然温柔" and p.opacity > 0.001 for p in arrival))
        began, position, _ = self._wait_for_companion(100.9, 4.9, "夜色依然温柔")
        entering = self._paint(self._animate_for(began, position, 0.08), "夜色依然温柔")
        settled = self._paint(self._animate_for(began + 0.08, position + 0.08, 0.52), "夜色依然温柔")
        self.assertLess(entering.opacity, settled.opacity)
        self.assertLess(entering.top, settled.top)

    def test_close_keeps_provider_removed_translation_until_collapse_finishes(self):
        main = "月光照耀着我今晚仍会继续轻轻歌唱"
        self._rows(main + "\n翻译：夜色依然温柔")
        self._render(100.0, 4.0)
        visible = self._paint(self._render(100.6, 4.6), "夜色依然温柔")
        self._toggle(100.61)
        self.owner.timed_lyrics[0] = (0.0, main)
        collapsing = self._paint(self._render(100.78, 4.78), "夜色依然温柔")
        self.assertGreater(collapsing.opacity, 0.0)
        self.assertLess(collapsing.opacity, visible.opacity)
        self.assertLess(collapsing.top, visible.top)
        finished = self._render(101.1, 5.1)
        self.assertFalse(any(p.text == "夜色依然温柔" for p in finished))
        self.assertIsNone(self.window._translation_exit_source)

    def test_close_during_entrance_continues_from_visible_opacity_and_position(self):
        self.owner.enable_dual_line_lyrics = False
        self.window._translation_setting = False
        main = "月光照耀着我今晚仍会继续轻轻歌唱"
        self._rows(main + "\n翻译：夜色依然温柔")
        self._render(100.0, 4.0)
        self._toggle(100.01)
        self._render(100.01, 4.01)
        began, position, _ = self._wait_for_companion(100.01, 4.01, "夜色依然温柔")
        before = self._animate_for(began, position, 0.03)
        before_translation = self._paint(before, "夜色依然温柔")
        before_main = self._paint(before, main)
        self.assertGreater(before_translation.opacity, 0.0)
        self.assertLess(before_translation.opacity, 0.5)
        self._toggle(began + 0.03)
        after = self._render(began + 0.03, position + 0.03)
        after_translation = self._paint(after, "夜色依然温柔")
        after_main = self._paint(after, main)
        self.assertAlmostEqual(after_translation.opacity, before_translation.opacity, delta=0.03)
        self.assertAlmostEqual(
            after_translation.top - after_main.bottom,
            before_translation.top - before_main.bottom, delta=1.0,
        )

    def test_reopen_during_exit_keeps_current_translation_visible(self):
        main = "月光照耀着我今晚仍会继续轻轻歌唱"
        self._rows(main + "\n翻译：夜色依然温柔")
        self._render(100.0, 4.0)
        self._toggle(100.01)
        before = self._render(100.18, 4.18)
        before_translation = self._paint(before, "夜色依然温柔")
        before_main = self._paint(before, main)
        self.assertGreater(before_translation.opacity, 0.25)
        self.assertLess(before_translation.opacity, 0.75)
        self._toggle(100.18)
        after = self._render(100.18, 4.18)
        after_translation = self._paint(after, "夜色依然温柔")
        after_main = self._paint(after, main)
        self.assertAlmostEqual(after_translation.opacity, before_translation.opacity, delta=0.03)
        self.assertAlmostEqual(
            after_translation.top - after_main.bottom,
            before_translation.top - before_main.bottom, delta=1.0,
        )

    def test_low_perception_translation_toggle_keeps_shared_half_second_read_front(self):
        self.owner.music_surface_reading_strategy = "low_perception"
        self.owner.enable_dual_line_lyrics = False
        self.window._translation_setting = False
        main = "月光照耀着我今晚仍会继续轻轻歌唱"
        self._rows(main + "\n翻译：夜色依然温柔")
        self._render(100.0, 0.1)
        self._toggle(100.01)
        self._render(100.01, 0.11)
        _, _, painted = self._wait_for_companion(100.01, 0.11, "夜色依然温柔")
        main_paint = self._paint(painted, main)
        translated = self._paint(painted, "夜色依然温柔")
        self.assertGreater(main_paint.progress, 0.0)
        self.assertLess(main_paint.progress, 1.0)
        self.assertGreater(translated.opacity, 0.0)
        self.assertEqual(translated.progress, main_paint.progress)
        self.assertIsNone(translated.color, "An entering translation must not bypass the shared wipe with full white ink")

    def test_rapid_open_close_reopen_preserves_each_visible_pose(self):
        self.owner.enable_dual_line_lyrics = False
        self.window._translation_setting = False
        main = "月光照耀着我今晚仍会继续轻轻歌唱"
        self._rows(main + "\n翻译：夜色依然温柔")
        self._render(100.0, 4.0)
        self._toggle(100.01)
        began, position, _ = self._wait_for_companion(100.01, 4.01, "夜色依然温柔")
        for duration in (0.03, 0.04, 0.05, 0.06):
            before = self._animate_for(began, position, duration)
            began += duration
            position += duration
            before_translation = self._paint(before, "夜色依然温柔")
            before_main = self._paint(before, main)
            self._toggle(began)
            after = self._render(began, position)
            after_translation = self._paint(after, "夜色依然温柔")
            after_main = self._paint(after, main)
            self.assertAlmostEqual(after_translation.opacity, before_translation.opacity, delta=0.03)
            self.assertAlmostEqual(
                after_translation.top - after_main.bottom,
                before_translation.top - before_main.bottom, delta=1.0,
            )

    def test_translation_entry_waits_for_next_primary_clearance_then_visibly_overshoots(self):
        self.owner.enable_dual_line_lyrics = False
        self.window._translation_setting = False
        main = "月光照耀着我今晚仍会继续轻轻歌唱"
        next_main = "Another sentence continues here"
        self._rows(main + " (echo)\n翻译：月光（回声）继续歌唱", next_main)
        self._render(100.0, 4.0)
        before = self._render(100.6, 4.6)
        next_before = self._paint(before, next_main).top
        self._toggle(100.61)
        frames = []
        for step in range(61):
            now = 100.61 + step * 0.02
            frames.append(self._render(now, 4.61 + step * 0.02))
        visible_frames = [frame for frame in frames if any(
            p.text == "月光继续歌唱" and p.opacity > 0.01 for p in frame
        )]
        self.assertTrue(visible_frames, "The translation must eventually perform its entrance")
        for frame in visible_frames:
            translated = self._paint(frame, "月光继续歌唱")
            next_primary = self._paint(frame, next_main)
            self.assertLessEqual(translated.bottom, next_primary.top + 0.5,
                                 "Visible companion ink must not overlap the following primary row")
        earliest = visible_frames[0]
        self.assertGreater(self._paint(earliest, next_main).top, next_before + 1.0)
        settled = visible_frames[-1]
        settled_offset = self._paint(settled, "月光继续歌唱").top - self._paint(settled, main).bottom
        relative_tops = [
            self._paint(frame, "月光继续歌唱").top - self._paint(frame, main).bottom
            for frame in visible_frames
        ]
        self.assertLess(min(relative_tops), settled_offset - 1.0)
        self.assertGreater(max(relative_tops), settled_offset + 0.4,
                           "Reserving space must preserve the visible spring overshoot")

    def test_initial_enabled_translation_is_visible_without_an_unrequested_toggle(self):
        self._rows("月光照耀着我今晚仍会继续轻轻歌唱\n翻译：夜色依然温柔")
        translated = self._paint(self._render(100.0, 4.0), "夜色依然温柔")
        self.assertEqual(translated.opacity, 1.0)

    def test_external_settings_flag_uses_same_entrance_and_exit(self):
        self.owner.enable_dual_line_lyrics = False
        self.window._translation_setting = False
        self._rows("月光照耀着我今晚仍会继续轻轻歌唱\n翻译：夜色依然温柔")
        self._render(100.0, 4.0)
        self.owner.enable_dual_line_lyrics = True
        self._render(100.01, 4.01)
        began, position, _ = self._wait_for_companion(100.01, 4.01, "夜色依然温柔")
        entering = self._paint(self._animate_for(began, position, 0.08), "夜色依然温柔")
        settled = self._paint(self._animate_for(began + 0.08, position + 0.08, 0.52), "夜色依然温柔")
        self.assertLess(entering.opacity, settled.opacity)
        self.owner.enable_dual_line_lyrics = False
        self._render(began + 0.61, position + 0.61)
        exiting = self._paint(self._animate_for(began + 0.61, position + 0.61, 0.17), "夜色依然温柔")
        self.assertLess(exiting.opacity, settled.opacity)
        self.assertGreater(exiting.opacity, 0.0)
        self.assertFalse(any(p.text == "夜色依然温柔" for p in self._animate_for(began + 0.78, position + 0.78, 0.30)))

    def test_exit_snapshot_freezes_in_place_provider_unit_and_kind_updates(self):
        main = "月光照耀着我今晚仍会继续轻轻歌唱"
        self._rows(main + "\n翻译：夜色依然温柔")
        self.owner.timed_lyric_units[0] = tuple(
            TimedLyricUnit(index * 0.5, (index + 1) * 0.5, char)
            for index, char in enumerate(main)
        )
        self.owner.timed_lyric_timing_kinds[0] = "unit"
        before = self._paint(self._render(100.0, 4.2), main)
        self.assertIsNotNone(before.real_progresses)
        self._toggle(100.0)
        self.owner.timed_lyrics[0] = (0.0, main)
        self.owner.timed_lyric_units[0] = ()
        self.owner.timed_lyric_timing_kinds[0] = "line"
        after = self._paint(self._render(100.0, 4.2), main)
        self.assertEqual(after.real_progresses, before.real_progresses)
        self.assertIsNotNone(after.real_progresses)
        self.assertTrue(any(p.text == "夜色依然温柔" for p in self._render(100.1, 4.3)))

    def test_track_change_during_exit_cannot_leak_old_companion_or_reuse_entry_pose(self):
        main = "月光照耀着我今晚仍会继续轻轻歌唱"
        self._rows(main + "\n翻译：旧曲翻译")
        self._render(100.0, 4.0)
        self._toggle(100.01)
        self._render(100.1, 4.1)
        self.owner.media_track_id = "a-new-song"
        self.owner.track_title = "A new song"
        self._rows(main + "\n翻译：新曲翻译")
        self._render(100.2, 4.2)
        settled = self._render(100.7, 4.7)
        self.assertFalse(any(p.text in {"旧曲翻译", "新曲翻译"} for p in settled))
        self._toggle(100.71)
        self._render(100.71, 4.71)
        began, position, _ = self._wait_for_companion(100.71, 4.71, "新曲翻译")
        entering = self._paint(self._animate_for(began, position, 0.03), "新曲翻译")
        self.assertGreater(entering.opacity, 0.0)
        self.assertLess(entering.opacity, 0.5)

    def test_toggle_preserves_main_spring_slots_and_confirmed_clock_during_source_spike(self):
        self.owner.enable_dual_line_lyrics = False
        self.window._translation_setting = False
        self.owner.media_position_source = "windows-media"
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self._rows("A sentence with enough words to wrap over several visual lines " * 3 + "\n翻译：夜色依然温柔")
        self._render(100.0, 10.0)
        self._render(100.1, 90.0)
        motion = self.window._lyric_motion
        self.assertLess(motion.last_position, 11.0)
        pending = (motion.pending_clock_position, motion.pending_clock_sample, motion.pending_clock_at)
        self.assertIsNotNone(pending[0])
        slots = self.window._logical_line_spring.positions
        spring_key = self.window._logical_line_spring.key
        visual_counts = self.window._song_visual_counts
        self._toggle(100.11)
        self._render(100.12, fresh_sample=False)
        self.assertEqual(self.window._song_visual_counts, visual_counts)
        self.assertEqual(self.window._logical_line_spring.key, spring_key)
        self.assertIs(self.window._logical_line_spring.positions, slots)
        self.assertAlmostEqual(motion.last_position, 10.12)
        self.assertEqual((motion.pending_clock_position, motion.pending_clock_sample, motion.pending_clock_at), pending)

    def test_native_aside_stack_touches_main_then_aside_translation_before_regular_translation(self):
        main = "月光照耀着我今晚仍会继续轻轻歌唱"
        self._rows(main + " (echo)\n翻译：月光（回声）继续歌唱")
        self._render(100.0, 4.0)
        painted = self._render(100.6, 4.6)
        main_paint = self._paint(painted, main)
        aside = self._paint(painted, "echo")
        aside_translation = self._paint(painted, "回声")
        normal_translation = self._paint(painted, "月光继续歌唱")
        self.assertAlmostEqual(aside.top, main_paint.bottom, delta=1.0)
        self.assertAlmostEqual(aside_translation.top, aside.bottom, delta=1.0)
        self.assertGreater(normal_translation.top, aside_translation.bottom)
        self.assertLess(normal_translation.top - aside_translation.bottom, 12.0)

    def test_standalone_aside_stays_attached_to_previous_sung_sentence(self):
        main = "月光照耀着我今晚仍会继续轻轻歌唱"
        self.owner.timed_lyrics = [
            (0.0, main), (5.0, "(echo)"),
            (20.0, "Second sentence"), (40.0, "Third sentence"), (60.0, "Fourth sentence"),
        ]
        self.owner.timed_lyric_units = [()] * 5
        self.owner.timed_lyric_timing_kinds = ["line"] * 5
        self._render(100.0, 0.5)
        painted = self._render(100.6, 1.1)
        aside = self._paint(painted, "echo")
        main_paint = self._paint(painted, main)
        self.assertEqual(len(self.window._song_layouts), 4)
        self.assertEqual(aside.progress, 0.0)
        self.assertAlmostEqual(aside.top, main_paint.bottom, delta=1.0)

    def test_offscreen_translations_do_not_keep_a_paused_page_on_lyric_repaints(self):
        self.owner.media_is_playing = False
        self.owner.enable_dual_line_lyrics = False
        self.window._translation_setting = False
        self.owner.timed_lyrics = [
            (index * 20.0, f"Sentence number {index} continues here\n翻译：第{index}句的译文")
            for index in range(20)
        ]
        self.owner.timed_lyric_units = [()] * 20
        self.owner.timed_lyric_timing_kinds = ["line"] * 20
        self._render(100.0, 4.0)
        self._toggle(100.01)
        for frame in range(1, 101):
            self._render(100.01 + frame * 0.02, 4.0)
        self.assertFalse(self.window._lyric_spring_active)
        with (
            patch("local.music_surface_window.time.monotonic", return_value=102.03),
            patch.object(self.window, "update") as update,
        ):
            self.window._on_animation_tick()
        self.assertFalse(any(
            call.args == (self.window._lyric_animation_rect,)
            for call in update.call_args_list
        ), "Unseen translations must not keep requesting lyric-region paints")

    def test_toggle_after_song_replacement_before_first_paint_cannot_capture_old_lyrics(self):
        main = "月光照耀着我今晚仍会继续轻轻歌唱"
        self._rows(main + "\n翻译：旧曲翻译")
        self._render(100.0, 4.0)
        self.owner.media_track_id = "replacement-before-first-paint"
        new_main = "A new original sentence with many words that keeps ordinary reading while the next song loads"
        self._rows(new_main + "\n翻译：新曲翻译")
        self._toggle(100.1)
        painted = self._render(100.11, 4.11)
        self.assertFalse(any(p.text in {main, "旧曲翻译", "新曲翻译"} for p in painted))
        self._paint(painted, new_main)
        self.assertIsNone(self.window._translation_exit_source)


if __name__ == "__main__":
    unittest.main()
