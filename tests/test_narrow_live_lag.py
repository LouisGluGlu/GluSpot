"""Shared-clock focus and scroll regressions for the live narrow surface."""
from __future__ import annotations

import math
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from _demos.narrow3.narrow_window import NarrowStudyWindow


TIMED = [(0.0, 'first'), (10.0, 'second'), (20.0, 'third')]


class FrameHarness:
    """Exercise production frame methods without constructing native widgets."""

    _lyric_frame = NarrowStudyWindow._lyric_frame
    _prepare_live_commit = NarrowStudyWindow._prepare_live_commit
    _refresh_live_position_route = NarrowStudyWindow._refresh_live_position_route
    playback_position = NarrowStudyWindow.playback_position

    def __init__(self, *, source='windows-media', position=1.0, focus=0, route=''):
        self.live = True
        self.track = 'fixture-a'
        self.owner = SimpleNamespace(
            media_position_source=source,
            lyric_last_sync_position=position,
            lyric_index=focus,
            prev_lyric_index=0,
            lyric_changed_at=99.0,
            media_duration_seconds=30.0,
            media_capture_epoch_id=0,
            media_position_sampled_at=100.0,
            _stable_lyric_source_position=Mock(side_effect=AssertionError('private source clock')),
            data_provider=SimpleNamespace(diagnostics=Mock(return_value={
                'capture': {'position_route': route}})))
        self._lyric_motion = SimpleNamespace(
            last_position=None,
            frame=Mock(side_effect=AssertionError('private motion clock')))
        self._browse_track_key = self.track
        self._clear_lyric_browse = Mock()
        self._hovered_lyric_index = None
        self._committed_track = None
        self._settled_epoch = None
        self._live_position_route = ''
        self._live_scroll_index = self._live_scroll_at = None
        self._live_lyric_diagnostics = {}
        self._lyric_word_hit_rects = []
        self._lyric_hit_rects = []
        self.veil = SimpleNamespace(clear=Mock())
        self._content_frames = {}
        self._diagnostics = dict(swap_commits=0, settle_commits=0,
                                 first_sample_settle_outlier=0)

    def _visual_track_identity(self):
        return self.track

    def seed_scroll(self, value, at=100.0):
        self._prepare_live_commit()
        self._live_scroll_index = value
        self._live_scroll_at = at


class NarrowLiveLagTests(unittest.TestCase):
    def test_direct_cef_and_playprogress_focus_advances_before_owner(self):
        for source in ('netease-cef', 'NetEase-PlayProgress'):
            with self.subTest(source=source):
                window = FrameHarness(source=source, position=9.75, focus=0)
                frame = window._lyric_frame(TIMED, 100.0)
                self.assertEqual((frame.active_index, frame.scroll_index), (1, 1.0))
                self.assertEqual(window.owner.lyric_index, 0)
                self.assertEqual(window._live_lyric_diagnostics['lead_s'], .28)
                window.owner.data_provider.diagnostics.assert_not_called()

    def test_cef_boundary_uses_280ms_lead_and_keeps_raw_word_clock(self):
        window = FrameHarness(source='cef', position=9.71)
        self.assertEqual(window._lyric_frame(TIMED, 100.0).active_index, 0)
        window.owner.lyric_last_sync_position = 9.72
        frame = window._lyric_frame(TIMED, 100.01)
        self.assertEqual(frame.active_index, 1)
        self.assertEqual(frame.reading_progress, 0.0)
        self.assertEqual(window._lyric_motion.last_position, 9.72)
        self.assertEqual(window.playback_position(), 9.72)
        window._lyric_motion.frame.assert_not_called()
        window.owner._stable_lyric_source_position.assert_not_called()

    def test_capture_model_uses_cached_cef_route_without_frame_polling(self):
        window = FrameHarness(source='capture-model', position=9.75, route='cef')
        self.assertEqual(window._lyric_frame(TIMED, 100.0).active_index, 0)
        window._refresh_live_position_route()
        for at in (100.0, 100.0, 100.04):
            self.assertEqual(window._lyric_frame(TIMED, at).active_index, 1)
        window.owner.data_provider.diagnostics.assert_called_once_with()

    def test_route_refresh_observes_fallback_without_using_physical_clock(self):
        window = FrameHarness(source='capture-model', position=9.75, route='cef')
        window._refresh_live_position_route()
        self.assertEqual(window._lyric_frame(TIMED, 100.0).active_index, 1)
        window.owner.data_provider.diagnostics.return_value = {'capture': {'position_route': 'uia'}}
        window._refresh_live_position_route()
        self.assertEqual(window._lyric_frame(TIMED, 100.04).active_index, 0)
        self.assertEqual(window.owner.data_provider.diagnostics.call_count, 2)
        window.owner._stable_lyric_source_position.assert_not_called()

    def test_other_sources_keep_owner_focus_including_uia_and_unavailable(self):
        for source, route in (('windows-media', ''), ('owned-player', 'cef'),
                              ('capture-model', 'smtc'), ('capture-model', 'uia'),
                              ('netease-uia-lyrics', 'cef'), ('cef-uia-lyrics', 'cef'),
                              ('capture-unavailable', 'cef')):
            with self.subTest(source=source, route=route):
                window = FrameHarness(source=source, position=1.0, focus=2)
                window._live_position_route = route
                frame = window._lyric_frame(TIMED, 100.0)
                self.assertEqual((frame.active_index, frame.scroll_index), (2, 2.0))
                if source.endswith('-uia-lyrics') or source == 'capture-unavailable':
                    self.assertEqual(window._live_lyric_diagnostics['lead_s'], 0.0)
                window.owner.data_provider.diagnostics.assert_not_called()

    def test_route_refresh_ignores_owned_and_unavailable_sources(self):
        for source in ('owned-player', 'capture-unavailable'):
            with self.subTest(source=source):
                window = FrameHarness(source=source, route='cef')
                window._live_position_route = 'cef'
                window._refresh_live_position_route()
                self.assertEqual(window._live_position_route, '')
                window.owner.data_provider.diagnostics.assert_not_called()

    def test_missing_or_failed_route_diagnostics_keeps_owner_focus(self):
        window = FrameHarness(source='capture-model', position=9.75, focus=0)
        window.owner.data_provider.diagnostics.side_effect = RuntimeError('diagnostics unavailable')
        window._refresh_live_position_route()
        self.assertEqual(window._lyric_frame(TIMED, 100.0).active_index, 0)
        window.owner.data_provider = SimpleNamespace()
        window._refresh_live_position_route()
        self.assertEqual(window._lyric_frame(TIMED, 100.04).active_index, 0)

    def test_full_line_or_larger_scroll_displacement_snaps_both_directions(self):
        for initial, focus in ((0.0, 1), (1.0, 0), (0.0, 2), (2.0, 0)):
            with self.subTest(initial=initial, focus=focus):
                window = FrameHarness(focus=focus)
                window.seed_scroll(initial)
                frame = window._lyric_frame(TIMED, 100.01)
                self.assertEqual(frame.active_index, focus)
                self.assertEqual(frame.scroll_index, float(focus))

    def test_fractional_scroll_uses_120ms_tau_with_immediate_focus(self):
        window = FrameHarness(focus=1)
        window.seed_scroll(.5)
        frame = window._lyric_frame(TIMED, 100.12)
        self.assertEqual(frame.active_index, 1)
        self.assertAlmostEqual(frame.scroll_index, 1.0 - .5 * math.exp(-1.0))
        self.assertNotEqual(frame.scroll_index, float(frame.active_index))

    def test_repeated_same_timestamp_frame_is_idempotent(self):
        window = FrameHarness(focus=1)
        window.seed_scroll(.5)
        first = window._lyric_frame(TIMED, 100.04)
        for _ in range(20):
            self.assertEqual(window._lyric_frame(TIMED, 100.04), first)
        self.assertEqual(window._live_scroll_at, 100.04)

    def test_backward_timestamp_does_not_rewind_or_reapply_elapsed_time(self):
        window = FrameHarness(focus=1)
        window.seed_scroll(.5, at=100.1)
        self.assertEqual(window._lyric_frame(TIMED, 100.0).scroll_index, .5)
        self.assertEqual(window._lyric_frame(TIMED, 100.1).scroll_index, .5)
        self.assertEqual(window._live_scroll_at, 100.1)

    def test_small_scroll_tail_settles_for_stable_noop_frames(self):
        window = FrameHarness(focus=1)
        window.seed_scroll(.9995)
        self.assertEqual(window._lyric_frame(TIMED, 100.04).scroll_index, 1.0)
        self.assertEqual(window._lyric_frame(TIMED, 200.0).scroll_index, 1.0)

    def test_track_swap_and_capture_epoch_settle_reset_fractional_scroll(self):
        for change in ('track', 'epoch'):
            with self.subTest(change=change):
                window = FrameHarness(focus=1)
                window.seed_scroll(.75)
                if change == 'track':
                    window.track = 'fixture-b'
                else:
                    window.owner.media_capture_epoch_id = 'new-epoch'
                self.assertEqual(window._lyric_frame(TIMED, 100.01).scroll_index, 1.0)
                self.assertEqual(window._diagnostics['swap_commits' if change == 'track'
                                                       else 'settle_commits'], 1)

    def test_empty_timing_resets_pose_and_diagnostics(self):
        window = FrameHarness(focus=1)
        window.seed_scroll(.75)
        frame = window._lyric_frame([], 100.0)
        self.assertEqual((frame.active_index, frame.scroll_index), (0, 0.0))
        self.assertIsNone(window._live_scroll_index)
        self.assertEqual(window._live_lyric_diagnostics, {})
        self.assertEqual(window._lyric_frame(TIMED, 100.01).scroll_index, 1.0)

    def test_diagnostics_observe_selected_index_and_unmodified_position(self):
        window = FrameHarness(source='cef', position=9.75, focus=0)
        frame = window._lyric_frame(TIMED, 100.0)
        snapshot = window._live_lyric_diagnostics
        self.assertEqual(snapshot['lyric_index'], frame.active_index)
        self.assertEqual(snapshot['scroll_index'], frame.scroll_index)
        self.assertEqual(snapshot['owner_lyric_index'], 0)
        self.assertEqual(snapshot['position_ms'], 9750.0)
        self.assertEqual(snapshot['highlighted_line_start_ms'], 10000.0)
        self.assertEqual(snapshot['lag_ms'], 250.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
