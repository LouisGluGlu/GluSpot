from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from api.providers import WindowsMediaProvider
from local.media_position_pipeline import media_position_update_plan
from local.music_lyric_motion import MusicLyricMotion
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_fullscreen import _Owner
from tests.test_provider_state import FakeLyricsStore, FakeSession, FakeTimeSpan


class MusicClockObservationTests(unittest.TestCase):
    """SDK polling is not an independent timeline event or a new time anchor."""

    def _provider(self):
        with patch.object(WindowsMediaProvider, "_start_loop", return_value=None):
            provider = WindowsMediaProvider()
        session = FakeSession("Generic.Music", "Song", "Artist", True, 10.0, 180.0)
        session.timeline.last_updated_time = datetime(2026, 9, 30, tzinfo=timezone.utc)
        provider.lyrics_store = FakeLyricsStore()
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider._music_player_adapter_for = lambda *_args: "system"
        provider._is_source_app_foreground = lambda *_args: False
        provider._thumbnail_for_track = lambda *_args: asyncio.sleep(0, result=b"")
        provider._get_session = lambda: asyncio.sleep(0, result=session)
        return provider, session

    def _read(self, provider, now):
        with patch("api.providers.time.monotonic", return_value=now):
            data = asyncio.run(provider._read_visual_data())
        self.assertIsNotNone(data)
        delivered = provider._advance_delayed_media_sample(data, now)
        provider._last_data = delivered
        provider._last_ready_at = now
        return delivered

    @staticmethod
    def _owner_plan(track, previous_position, previous_raw, previous_sync, now):
        return media_position_update_plan(
            track_changed=False,
            incoming_is_playing=track.is_playing,
            incoming_position_seconds=track.position_seconds,
            incoming_position_source=track.position_source,
            incoming_position_confidence=track.position_confidence,
            incoming_position_interactive=track.position_interactive,
            incoming_source_foreground=track.source_app_foreground,
            previous_position_seconds=previous_position,
            previous_sync_at=previous_sync,
            previous_raw_position_seconds=previous_raw,
            previous_raw_position_source=track.position_source,
            now=now,
        )

    def test_same_sdk_timeline_keeps_identity_and_projects_original_anchor_once(self):
        provider, _session = self._provider()
        first = self._read(provider, 100.0)
        for now in (100.65, 101.30, 101.95):
            data = self._read(provider, now)
            self.assertEqual(data.track.position_sampled_at, first.track.position_sampled_at)
            self.assertAlmostEqual(data.track.position_seconds, 10.0 + now - 100.0)
            projected = provider._project_visual_position(data, now + 0.20, now)
            self.assertAlmostEqual(projected.track.position_seconds, 10.0 + now + 0.20 - 100.0)

    def test_duplicate_sdk_spike_cannot_skip_rows_through_owner_and_motion(self):
        provider, session = self._provider()
        timed = [(float(index * 4), str(index)) for index in range(40)]
        motion = MusicLyricMotion()
        first = self._read(provider, 100.0)
        motion.frame(timed, first.track.position_seconds, 100.0, first.track.track_id,
                     sample_id=first.track.position_sampled_at)
        session.timeline.position = FakeTimeSpan(50.0)
        session.timeline.last_updated_time += timedelta(seconds=1)
        previous_owner = first.track.position_seconds
        previous_raw = previous_owner
        previous_sync = 100.0
        spike_identity = None
        for now in (100.10, 100.75, 101.40, 102.05):
            data = self._read(provider, now)
            if spike_identity is None:
                spike_identity = data.track.position_sampled_at
            self.assertEqual(data.track.position_sampled_at, spike_identity)
            owner = self._owner_plan(data.track, previous_owner + now - previous_sync,
                                     previous_raw, previous_sync, now)
            frame = motion.frame(timed, owner.position_seconds, now, data.track.track_id,
                                 sample_id=data.track.position_sampled_at)
            self.assertLessEqual(frame.active_index, 3)
            self.assertAlmostEqual(motion.last_position, 10.0 + now - 100.0)
            previous_owner, previous_raw, previous_sync = (
                owner.position_seconds, owner.raw_position_seconds, now,
            )

        # A genuinely updated event is independent evidence and still permits
        # the existing confirmed external-seek path; duplicate polls do not.
        session.timeline.position = FakeTimeSpan(52.6)
        session.timeline.last_updated_time += timedelta(seconds=1)
        data = self._read(provider, 102.70)
        self.assertNotEqual(data.track.position_sampled_at, spike_identity)
        owner = self._owner_plan(data.track, previous_owner + 0.65, previous_raw,
                                 previous_sync, 102.70)
        confirmed = motion.frame(timed, owner.position_seconds, 102.70, data.track.track_id,
                                 sample_id=data.track.position_sampled_at)
        self.assertEqual(confirmed.active_index, 13)

    def test_changed_position_or_event_stamp_is_a_new_observation(self):
        provider, session = self._provider()
        first = self._read(provider, 100.0)
        session.timeline.position = FakeTimeSpan(40.0)
        # Some clients omit event-stamp updates; a changed raw position must
        # nevertheless retain the ability to establish a new external seek.
        changed = self._read(provider, 100.5)
        self.assertNotEqual(changed.track.position_sampled_at, first.track.position_sampled_at)
        self.assertAlmostEqual(changed.track.position_seconds, 40.0)
        session.timeline.last_updated_time += timedelta(seconds=1)
        repeated_position_new_event = self._read(provider, 101.0)
        self.assertNotEqual(repeated_position_new_event.track.position_sampled_at,
                            changed.track.position_sampled_at)
        self.assertAlmostEqual(repeated_position_new_event.track.position_seconds, 40.0)

    def test_pause_and_resume_do_not_reuse_playing_observation_age(self):
        provider, session = self._provider()
        playing = self._read(provider, 100.0)
        session.playback.playback_status = 3
        paused = self._read(provider, 101.0)
        self.assertNotEqual(paused.track.position_sampled_at, playing.track.position_sampled_at)
        self.assertFalse(paused.track.is_playing)
        self.assertAlmostEqual(paused.track.position_seconds, 10.0)
        held = self._read(provider, 102.0)
        self.assertEqual(held.track.position_sampled_at, paused.track.position_sampled_at)
        self.assertAlmostEqual(held.track.position_seconds, 10.0)
        session.playback.playback_status = 4
        resumed = self._read(provider, 103.0)
        self.assertTrue(resumed.track.is_playing)
        self.assertNotEqual(resumed.track.position_sampled_at, paused.track.position_sampled_at)
        self.assertAlmostEqual(resumed.track.position_seconds, 10.0)

    def test_missing_sdk_event_stamp_still_keeps_unchanged_raw_observation(self):
        provider, session = self._provider()
        del session.timeline.last_updated_time
        first = self._read(provider, 100.0)
        cached = self._read(provider, 101.0)
        self.assertEqual(cached.track.position_sampled_at, first.track.position_sampled_at)
        self.assertAlmostEqual(cached.track.position_seconds, 11.0)
        session.timeline.position = FakeTimeSpan(40.0)
        changed = self._read(provider, 102.0)
        self.assertEqual(changed.track.position_sampled_at, 102.0)
        self.assertAlmostEqual(changed.track.position_seconds, 40.0)

    def test_a_b_a_track_generations_do_not_reuse_first_a_age(self):
        provider, session = self._provider()
        first = self._read(provider, 100.0)
        session.properties.title = "Other song"
        second = self._read(provider, 101.0)
        session.properties.title = "Song"
        returned = self._read(provider, 102.0)
        self.assertNotEqual(first.track.track_id, second.track.track_id)
        self.assertEqual(first.track.track_id, returned.track.track_id)
        self.assertEqual(returned.track.position_sampled_at, 102.0)
        self.assertAlmostEqual(returned.track.position_seconds, 10.0)

    def _use_adapter(self, provider, *, foreground):
        provider._music_player_adapter_for = lambda *_args: "netease"
        provider._player_position_reader_for = lambda *_args: None
        provider._is_source_app_foreground = lambda *_args: foreground
        provider._foreground_track_metadata = lambda *_args: None
        provider._schedule_current_netease_art_lookup = lambda *_args, **_kwargs: None

    def test_foreground_player_does_not_change_the_smtc_observation_identity(self):
        provider, _session = self._provider()
        self._use_adapter(provider, foreground=True)
        first = self._read(provider, 100.0)
        for now in (100.5, 100.9):
            data = self._read(provider, now)
            self.assertEqual(data.track.position_source, "windows-media")
            self.assertEqual(data.track.position_sampled_at, first.track.position_sampled_at)
            self.assertAlmostEqual(data.track.position_seconds, 10.0 + now - 100.0)

    def test_zero_smtc_position_does_not_reuse_private_player_projection(self):
        provider, session = self._provider()
        self._use_adapter(provider, foreground=False)
        self._read(provider, 100.0)
        session.timeline.position = FakeTimeSpan(0.0)
        for now in (101.5, 102.0):
            data = self._read(provider, now)
            self.assertEqual(data.track.position_source, "windows-media")
            self.assertEqual(data.track.position_observed_seconds, 0.0)
            self.assertEqual(data.track.position_sampled_at, 101.5)
            self.assertAlmostEqual(data.track.position_seconds, now - 101.5)

    def test_first_delivery_latency_is_projected_once_not_again_on_cached_poll(self):
        provider, _session = self._provider()
        first = self._read(provider, 100.0)
        delivered = provider._advance_delayed_media_sample(first, 100.6)
        self.assertAlmostEqual(delivered.track.position_seconds, 10.6)
        later = provider._project_visual_position(delivered, 101.0, 100.6)
        self.assertAlmostEqual(later.track.position_seconds, 11.0)


class PausedSourceEventSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _surface(self, *, playing=False, position=10.0, source="windows-media"):
        owner = _Owner()
        owner.media_track_id = "same-song"
        owner.media_is_playing = playing
        owner.media_position_seconds = position
        owner.media_position_source = source
        owner.media_position_sampled_at = 99.9
        owner.media_position_sync_at = 100.0
        owner.media_duration_seconds = 180.0
        owner._estimated_media_position = lambda _now: owner.media_position_seconds
        surface = MusicSurfaceWindow(owner)
        self.addCleanup(owner.close)
        self.addCleanup(surface.shutdown)
        timed = [(float(index * 4), str(index)) for index in range(40)]
        surface._lyric_frame(timed, 100.0)
        return owner, surface, timed

    def test_new_genuine_sdk_event_seek_while_already_paused_reanchors_once(self):
        for source in ("windows-media", "interactive"):
            with self.subTest(source=source):
                owner, surface, timed = self._surface(source=source)
                owner.media_position_seconds = 50.0
                owner.media_position_sampled_at = 100.1
                changed = surface._lyric_frame(timed, 100.1)
                self.assertEqual(changed.active_index, 12)
                self.assertAlmostEqual(surface._lyric_motion.last_position, 50.0)
                for now in (100.75, 101.4):
                    with patch.object(surface._lyric_motion, "frame",
                                      wraps=surface._lyric_motion.frame) as clock:
                        repeated = surface._lyric_frame(timed, now)
                    self.assertEqual(repeated.active_index, 12)
                    self.assertAlmostEqual(surface._lyric_motion.last_position, 50.0)
                    self.assertFalse(clock.call_args.kwargs["explicit_seek"])

    def test_first_pause_boundary_event_does_not_rewind_the_playing_frame(self):
        owner, surface, timed = self._surface(playing=True, position=50.0)
        owner.media_is_playing = False
        owner.media_position_seconds = 10.0
        owner.media_position_sampled_at = 100.1
        for now in (100.1, 100.75, 101.4):
            paused = surface._lyric_frame(timed, now)
            self.assertEqual(paused.active_index, 12)
            self.assertAlmostEqual(surface._lyric_motion.last_position, 50.0)

    def test_duplicate_non_sdk_paused_value_does_not_bypass_confirmation(self):
        owner, surface, timed = self._surface(source="netease-uia-progress")
        owner.media_position_seconds = 50.0
        owner.media_position_sampled_at = 100.1
        for now in (100.1, 100.75, 101.4):
            pending = surface._lyric_frame(timed, now)
            self.assertEqual(pending.active_index, 2)
            self.assertAlmostEqual(surface._lyric_motion.last_position, 10.0)

    def test_verified_local_seek_while_paused_remains_immediate(self):
        owner, surface, timed = self._surface()
        owner.media_position_seconds = 50.0
        owner.media_position_source = "local-seek"
        owner.media_position_sampled_at = 100.1
        seek = surface._lyric_frame(timed, 100.1)
        self.assertEqual(seek.active_index, 12)
        self.assertAlmostEqual(surface._lyric_motion.last_position, 50.0)

    def test_track_reset_clears_paused_event_but_same_track_timeline_keeps_it(self):
        for reset in ("track", "timeline"):
            with self.subTest(reset=reset):
                owner, surface, timed = self._surface()
                owner.media_position_seconds = 50.0
                owner.media_position_sampled_at = 100.1
                if reset == "track":
                    owner.media_track_id = "other-song"
                else:
                    timed = [(start, text + " new source") for start, text in timed]
                with patch.object(surface._lyric_motion, "frame",
                                  wraps=surface._lyric_motion.frame) as clock:
                    reset_frame = surface._lyric_frame(timed, 100.1)
                self.assertEqual(clock.call_args.kwargs["explicit_seek"], reset == "timeline")
                self.assertEqual(reset_frame.active_index, 12)
                self.assertAlmostEqual(surface._lyric_motion.last_position, 50.0)

    def test_genuine_paused_event_can_seek_back_within_the_same_sentence(self):
        owner, surface, timed = self._surface()
        owner.media_position_seconds = 9.5
        owner.media_position_sampled_at = 100.1
        seek = surface._lyric_frame(timed, 100.1)
        self.assertEqual(seek.active_index, 2)
        self.assertAlmostEqual(surface._lyric_motion.last_position, 9.5)


if __name__ == "__main__":
    unittest.main()
