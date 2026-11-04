"""Collapsed lyric width changes reserve native bounds before growing ink."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from PySide6.QtCore import QRectF

from local.island_stack_visual import IslandStackVisual
from local.island_visual_stack import IslandKind, interaction_mask_update_plan


class CollapsedPillMaskTests(unittest.TestCase):
    @staticmethod
    def plan(rects, previous=QRectF(), **overrides):
        options = dict(platform_win32=True, finder_mode=True, scene_suppressed=False,
                       interaction_mask_active=not previous.isNull(), finder_hover_rect=QRectF(),
                       last_mask_rect=previous, now=100., mask_deferred_until=0.,
                       last_pointer_move_at=0., last_mask_update_at=0., unit=1.)
        options.update(overrides)
        return interaction_mask_update_plan(rects, **options)

    def test_growth_bypasses_all_three_busy_gates(self):
        old = self.plan([QRectF(200, 10, 140, 32)]).mask_rect
        target = QRectF(120, 10, 300, 32)
        gates = ({"mask_deferred_until": 101.}, {"last_pointer_move_at": 99.99},
                 {"last_mask_update_at": 99.99},
                 {"mask_deferred_until": 101., "last_pointer_move_at": 99.99,
                  "last_mask_update_at": 99.99})
        for gate in gates:
            with self.subTest(gate=gate):
                plan = self.plan([target], old, **gate)
                self.assertEqual(plan.action, "apply")
                self.assertTrue(plan.mask_rect.contains(old))
                self.assertTrue(plan.mask_rect.contains(target))
                self.assertTrue(plan.record_update_at)

    def test_subpixel_growth_cannot_wait_for_the_old_delta_threshold(self):
        old_shell = QRectF(200, 10, 140, 32)
        old_mask = self.plan([old_shell]).mask_rect
        target = old_shell.adjusted(-.25, 0., .25, 0.)
        plan = self.plan([target], old_mask, last_mask_update_at=99.999)
        self.assertEqual(plan.action, "apply")
        self.assertTrue(plan.mask_rect.contains(old_mask))
        self.assertGreater(plan.mask_rect.width(), old_mask.width())

    def test_shrink_tracks_current_shell_even_while_motion_and_pointer_are_busy(self):
        target = QRectF(200, 10, 140, 32)
        old_mask = self.plan([QRectF(120, 10, 300, 32)]).mask_rect
        moving = self.plan([target], old_mask, defer_shrink=True)
        self.assertEqual(moving.action, "apply")
        self.assertEqual(moving.mask_rect, target.adjusted(-2., -2., 2., 2.))
        settled = self.plan([target], old_mask, defer_shrink=False)
        self.assertEqual(settled.action, "apply")
        self.assertTrue(settled.mask_rect.contains(target))
        self.assertLess(settled.mask_rect.width(), old_mask.width())

    def test_short_long_short_frame_sequence_always_contains_the_painted_shell(self):
        mask = QRectF()
        # Every actual spring pose, including overshoot, defines its own region.
        for widths, target_width in (((140, 150, 190, 250, 295, 302, 300), 300),
                                     ((300, 300, 280, 220, 160, 138, 140), 140)):
            for index, width in enumerate(widths):
                shell = QRectF(270-width/2, 10, width, 32)
                target = QRectF(270-target_width/2, 10, target_width, 32)
                plan = self.plan([shell], mask, defer_shrink=index < len(widths)-1)
                if plan.action == "apply":
                    mask = plan.mask_rect
                self.assertTrue(mask.contains(shell), (width, target_width, mask))
                self.assertTrue(shell.adjusted(-2., -2., 2., 2.).contains(mask))


class _CompactFixture:
    prepare = IslandStackVisual._prepare_compact_width_transition
    snapshot = IslandStackVisual._compact_lyric_paint_snapshot
    row_x = IslandStackVisual._island_lyric_row_x
    _scrolling_text_x = IslandStackVisual._scrolling_text_x

    def __init__(self):
        self.expanded = False
        self.media_track_id = "width-order-fixture"
        self.text = "short"
        self._compact_music_text = lambda: self.text
        self._u = lambda value: value
        self.scroll_phase = 0.

    def visual(self, width):
        return [SimpleNamespace(kind=IslandKind.MUSIC, rect=QRectF(300-width/2, 0, width, 32))]


class CollapsedPillContentOrderTests(unittest.TestCase):
    def setUp(self):
        self.pill = _CompactFixture()
        self.pill.prepare(self.pill.visual(140), 100.)
        self.pill._island_lyric_frame_at = 100.2
        self.old_lines, self.old_phase = ("short",), {"progress": .4, "active_index": 0}
        self.pill.snapshot(self.old_lines, self.old_phase)

    def test_grow_holds_old_line_until_the_painted_shell_is_ready(self):
        self.pill.text = "a much longer lyric"
        self.pill.prepare(self.pill.visual(300), 101.)
        self.pill._compact_shell_waiting = True
        new_phase = {"progress": .1, "active_index": 1}
        for now in (101., 101.05, 101.1, 101.2):
            self.pill._island_lyric_frame_at = now
            lines, phase, alpha = self.pill.snapshot((self.pill.text,), new_phase)
            self.assertEqual(lines, self.old_lines)
            self.assertEqual(phase, self.old_phase)
            self.assertEqual(alpha, 1.)
        self.pill._compact_shell_waiting = False
        self.pill._island_lyric_frame_at = 101.3
        lines, phase, alpha = self.pill.snapshot((self.pill.text,), new_phase)
        self.assertEqual(lines, (self.pill.text,))
        self.assertEqual(phase, new_phase)
        self.assertEqual(alpha, 0.)
        self.pill._island_lyric_frame_at = 101.4
        self.assertAlmostEqual(self.pill.snapshot(lines, phase)[2], 1.)

    def test_first_lyric_after_a_gap_waits_for_growth_without_an_old_text_snapshot(self):
        del self.pill._compact_lyric_presented
        self.pill.text = "a long first lyric after the interlude"
        self.pill.prepare(self.pill.visual(300), 101.)
        self.pill._compact_shell_waiting = True
        self.pill._island_lyric_frame_at = 101.05
        lines, phase, alpha = self.pill.snapshot((self.pill.text,), {"progress": .1})
        self.assertEqual(lines, ("",))
        self.assertEqual(alpha, 1.)
        self.pill._compact_shell_waiting = False
        self.pill._island_lyric_frame_at = 101.2
        self.assertEqual(self.pill.snapshot((self.pill.text,), {"progress": .1})[0],
                         (self.pill.text,))

    def test_shrink_reveals_new_text_before_the_shell_may_move(self):
        self.pill.text = "long initial text"
        self.pill.prepare(self.pill.visual(300), 101.)
        self.pill._compact_shell_waiting = False
        self.pill._island_lyric_frame_at = 101.2
        self.pill.snapshot((self.pill.text,), {"progress": .9})
        self.pill.text = "short again"
        self.pill.prepare(self.pill.visual(140), 102.)
        self.assertGreater(self.pill._compact_shrink_after, 102.)
        self.pill._island_lyric_frame_at = 102.05
        lines, phase, alpha = self.pill.snapshot((self.pill.text,), {"progress": 0.})
        self.assertEqual(lines, ("short again",))
        self.assertEqual(phase["progress"], 0.)
        self.assertGreater(alpha, 0.)
        self.assertLess(alpha, 1.)
        self.assertLess(self.pill._island_lyric_frame_at, self.pill._compact_shrink_after)

    def test_same_lyric_does_not_rearm_the_width_delay_on_every_frame(self):
        self.pill.text = "shorter"
        self.pill._compact_target_width = 300
        self.pill.prepare(self.pill.visual(140), 101.)
        delay = self.pill._compact_shrink_after
        for now in (101.03, 101.06, 101.09, 101.12):
            self.pill.prepare(self.pill.visual(140), now)
            self.assertEqual(self.pill._compact_shrink_after, delay)

    def test_expansion_releases_a_pending_compact_text_hold(self):
        self.pill.text = "long"
        self.pill.prepare(self.pill.visual(300), 101.)
        self.pill.expanded = True
        self.pill.prepare(self.pill.visual(300), 101.05)
        self.assertIsNone(self.pill._compact_held_lyric)
        self.assertIsNone(self.pill._compact_width_token)
        self.assertEqual(self.pill._compact_shrink_after, 0.)

    def test_overlong_lyric_marquee_keeps_the_highlight_visible_and_never_reverses(self):
        rect = QRectF(50, 0, 240, 32)
        positions = []
        for progress in (0., .15, .3, .45, .6, .75, .9, 1.):
            self.pill.scroll_phase += 100.  # Metadata animation cannot steer lyrics.
            x = self.pill.row_x(rect, 900., True, {"progress": progress})
            positions.append(x)
            self.assertGreaterEqual(x + 900.*progress, rect.left())
            self.assertLessEqual(x + 900.*progress, rect.right())
        self.assertEqual(positions, sorted(positions, reverse=True))
        self.assertEqual(positions[0], rect.left())
        self.assertEqual(positions[-1] + 900., rect.right())


if __name__ == "__main__":
    unittest.main()
