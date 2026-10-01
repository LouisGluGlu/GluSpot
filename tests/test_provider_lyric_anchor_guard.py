from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from api.models import TimedLyricLine
from api.netease_position import NeteasePositionSample
from api.providers import WindowsMediaProvider
from tests.test_provider_state import FakeLyricsStore, FakeSession, FakeTimeSpan


class ProviderLyricAnchorGuardTests(unittest.TestCase):
    def _provider(self, *, foreground=False):
        with patch.object(WindowsMediaProvider, "_start_loop", return_value=None):
            provider = WindowsMediaProvider()
        session = FakeSession("cloudmusic.exe", "Song", "Artist", True, 10.0, 180.0)
        session.timeline.last_updated_time = 0
        rows = tuple(TimedLyricLine(float(index * 4), str(index)) for index in range(40))
        store = FakeLyricsStore()
        store.rows = rows
        store.resolve_cached_or_local = lambda *_args: SimpleNamespace(
            timed_lyrics=store.rows, lyrics=tuple(line.text for line in store.rows), source="test",
        )
        reader = SimpleNamespace(
            source_prefix="netease", sample=None,
            duration_for_track=lambda *_args: 180.0,
        )
        reader.read = lambda *_args, **_kwargs: reader.sample
        provider.lyrics_store = store
        provider.netease_position = reader
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider._music_player_adapter_for = lambda *_args: "netease"
        provider._is_source_app_foreground = lambda *_args: foreground
        provider._schedule_current_netease_art_lookup = lambda *_args, **_kwargs: None
        provider._thumbnail_for_track = lambda *_args: asyncio.sleep(0, result=b"")
        provider._get_session = lambda: asyncio.sleep(0, result=session)
        return provider, session, reader, store

    def _read(self, provider, now):
        with patch("api.providers.time.monotonic", return_value=now):
            data = asyncio.run(provider._read_visual_data())
            data = provider._advance_delayed_media_sample(data, now)
            data = provider._stabilize_incoming_position(data, now)
            data = provider._apply_recent_local_seek_to_visual_data(data, now)
        provider._last_data = data
        provider._last_ready_at = now
        return data

    def _warm(self, provider, session):
        for now, position in ((100.0, 10.0), (100.6, 10.6), (101.2, 11.2)):
            session.timeline.position = FakeTimeSpan(position)
            session.timeline.last_updated_time += 1
            self._read(provider, now)

    @staticmethod
    def _coarse(position, now, *, playing=True):
        index = min(39, int(position // 4))
        return NeteasePositionSample(position, 180.0, "netease-uia-lyrics", 0.86,
                                    index, str(index), now, playing)

    def test_repeated_wrong_rows_cannot_overwrite_independently_advancing_sdk_clock(self):
        for foreground in (False, True):
            with self.subTest(foreground=foreground):
                provider, session, reader, _store = self._provider(foreground=foreground)
                self._warm(provider, session)
                # Ordinary or uniquely enlarged UIA rows share this coarse
                # provider contract; selected geometry is not a second clock.
                for now in (101.8, 102.4, 103.0, 103.6):
                    raw = 10.0 + now - 100.0
                    session.timeline.position = FakeTimeSpan(raw)
                    session.timeline.last_updated_time += 1
                    reader.sample = self._coarse(82.0 + now - 101.8, now)
                    result = self._read(provider, now)
                    self.assertEqual(result.track.position_source, "windows-media")
                    self.assertAlmostEqual(result.track.position_seconds, raw)

    def test_reliable_raw_progress_cannot_be_paused_by_stale_lyric_button_state(self):
        provider, session, reader, _store = self._provider()
        self._warm(provider, session)
        session.timeline.position = FakeTimeSpan(11.8)
        session.timeline.last_updated_time += 1
        reader.sample = self._coarse(82.0, 101.8, playing=False)
        result = self._read(provider, 101.8)
        self.assertTrue(result.track.is_playing)
        self.assertEqual(result.track.position_source, "windows-media")
        self.assertAlmostEqual(result.track.position_seconds, 11.8)

    def test_already_contaminated_local_estimate_recovers_from_independent_raw_clock(self):
        provider, session, reader, _store = self._provider(foreground=True)
        self._warm(provider, session)
        bad_track = replace(provider._last_data.track, position_seconds=82.0, position_source="netease-uia-lyrics")
        provider._last_data = replace(provider._last_data, track=bad_track)
        session.timeline.position = FakeTimeSpan(11.8)
        session.timeline.last_updated_time += 1
        reader.sample = self._coarse(82.0, 101.8)
        result = self._read(provider, 101.8)
        self.assertAlmostEqual(result.track.position_seconds, 11.8)
        self.assertEqual(result.track.position_source, "windows-media")

    def test_translation_enrichment_does_not_reset_independent_raw_evidence(self):
        provider, session, reader, store = self._provider()
        self._warm(provider, session)
        store.rows = tuple(TimedLyricLine(row.time_seconds, row.text + "\n翻译") for row in store.rows)
        session.timeline.position = FakeTimeSpan(11.8)
        session.timeline.last_updated_time += 1
        reader.sample = self._coarse(82.0, 101.8)
        result = self._read(provider, 101.8)
        self.assertAlmostEqual(result.track.position_seconds, 11.8)
        self.assertIn("\n翻译", result.track.timed_lyrics[0].text)

    def test_precise_uia_progress_still_has_existing_external_seek_path(self):
        provider, session, reader, _store = self._provider()
        self._warm(provider, session)
        for now in (101.8, 102.4, 103.0):
            session.timeline.position = FakeTimeSpan(10.0 + now - 100.0)
            session.timeline.last_updated_time += 1
            reader.sample = NeteasePositionSample(82.0 + now - 101.8, 180.0,
                                                  "netease-uia-progress", 0.92, sampled_at=now, is_playing=True)
            result = self._read(provider, now)
        self.assertEqual(result.track.position_source, "netease-uia-progress")
        self.assertGreater(result.track.position_seconds, 82.0)

    def test_paused_external_coarse_seek_is_not_gated_by_old_playing_evidence(self):
        provider, session, reader, _store = self._provider()
        self._warm(provider, session)
        session.playback.playback_status = 3
        self._read(provider, 101.8)
        reader.sample = self._coarse(82.0, 102.4, playing=False)
        result = self._read(provider, 102.4)
        self.assertFalse(result.track.is_playing)
        self.assertEqual(result.track.position_source, "netease-uia-lyrics")
        self.assertAlmostEqual(result.track.position_seconds, 82.0)

    def test_verified_gluspot_seek_keeps_existing_override(self):
        provider, session, reader, _store = self._provider()
        self._warm(provider, session)
        with patch("api.providers.time.monotonic", return_value=101.4):
            provider._remember_local_seek_for_track(provider._last_data.track.track_id, 82.0, 180.0)
        session.timeline.position = FakeTimeSpan(11.8)
        session.timeline.last_updated_time += 1
        reader.sample = self._coarse(82.4, 101.8)
        result = self._read(provider, 101.8)
        self.assertGreater(result.track.position_seconds, 82.0)

    def test_frozen_sdk_clock_loses_authority_and_allows_coarse_fallback(self):
        provider, session, reader, _store = self._provider()
        self._warm(provider, session)
        for now in (104.0, 104.6, 105.2):
            # Marker-only changes cannot renew evidence of position advance.
            session.timeline.last_updated_time += 1
            reader.sample = self._coarse(82.0 + now - 104.0, now)
            result = self._read(provider, now)
        self.assertEqual(result.track.position_source, "netease-uia-lyrics")
        self.assertGreater(result.track.position_seconds, 82.0)

    def test_sparse_natural_sdk_updates_keep_clock_between_real_events(self):
        for interval in (5.0, 10.0):
            with self.subTest(interval=interval):
                provider, session, reader, _store = self._provider()
                for step in range(3):
                    now = 100.0 + step * interval
                    session.timeline.position = FakeTimeSpan(10.0 + step * interval)
                    session.timeline.last_updated_time += 1
                    self._read(provider, now)
                last_event = 100.0 + 2 * interval
                # A UIA row scrolled far ahead is fresh, but cannot displace
                # the naturally advancing, sparsely updated Windows clock.
                for age in (0.6, 2.8, interval - 0.1):
                    now = last_event + age
                    reader.sample = self._coarse(82.0 + age, now)
                    result = self._read(provider, now)
                    self.assertEqual(result.track.position_source, "windows-media")
                    self.assertAlmostEqual(result.track.position_seconds, 10.0 + now - 100.0)
                self.assertEqual(provider._raw_lyric_anchor_clock["natural_advances"], 2)
                self.assertEqual(provider._raw_lyric_anchor_clock["advanced_at"], last_event)

    def test_sparse_clock_expires_after_missed_event_without_marker_renewal(self):
        provider, session, reader, _store = self._provider()
        for now, position in ((100.0, 10.0), (105.0, 15.0), (110.0, 20.0)):
            session.timeline.position = FakeTimeSpan(position)
            session.timeline.last_updated_time += 1
            self._read(provider, now)
        for now in (116.0, 116.6, 117.2):
            session.timeline.last_updated_time += 1
            reader.sample = self._coarse(82.0 + now - 116.0, now)
            result = self._read(provider, now)
        self.assertEqual(result.track.position_source, "netease-uia-lyrics")
        self.assertGreater(result.track.position_seconds, 82.0)
        self.assertEqual(provider._raw_lyric_anchor_clock["advanced_at"], 110.0)
        self.assertEqual(provider._raw_lyric_anchor_clock["interval_seconds"], 5.0)

    def test_sparse_interval_does_not_make_a_large_seek_natural_progress(self):
        provider, _session, _reader, _store = self._provider()
        for now, position in ((100.0, 10.0), (110.0, 20.0), (120.0, 30.0)):
            provider._raw_timeline_corroborates_lyric_anchor(
                "song", position, now, now, is_playing=True, metadata_matches=True,
            )
        # Even over a ten-second event gap, two extra seconds of advance is
        # a source discontinuity, not a reason to trust a selected lyric row.
        self.assertFalse(provider._raw_timeline_corroborates_lyric_anchor(
            "song", 42.0, 130.0, 130.0, is_playing=True, metadata_matches=True,
        ))
        self.assertEqual(provider._raw_lyric_anchor_clock["natural_advances"], 0)
        self.assertEqual(provider._raw_lyric_anchor_clock["interval_seconds"], 0.0)

    def test_original_windows_observation_survives_delivery_and_cached_projection(self):
        for foreground in (False, True):
            with self.subTest(foreground=foreground):
                provider, session, _reader, _store = self._provider(foreground=foreground)
                self._read(provider, 100.0)
                session.timeline.position = FakeTimeSpan(15.0)
                session.timeline.last_updated_time += 1
                observed = self._read(provider, 105.0)
                self.assertIn(observed.track.position_source, {"windows-media", "interactive"})
                self.assertEqual(observed.track.position_observed_seconds, 15.0)
                delivered = provider._advance_delayed_media_sample(observed, 105.75)
                projected = provider._project_visual_position(delivered, 106.5, 105.75)
                self.assertAlmostEqual(delivered.track.position_seconds, 15.75)
                self.assertAlmostEqual(projected.track.position_seconds, 16.5)
                for result in (delivered, projected):
                    self.assertEqual(result.track.position_sampled_at, 105.0)
                    self.assertEqual(result.track.position_observed_seconds, 15.0)
                # Event-marker renewal with a frozen original Position remains
                # distinguishable even when the delivery takes longer.
                session.timeline.last_updated_time += 1
                renewed = self._read(provider, 106.75)
                late = provider._advance_delayed_media_sample(renewed, 107.5)
                self.assertEqual(late.track.position_sampled_at, 106.75)
                self.assertEqual(late.track.position_observed_seconds, 15.0)

    def test_same_song_from_another_application_starts_new_raw_observation_and_cadence(self):
        provider, session, _reader, _store = self._provider()
        self._warm(provider, session)
        prior = provider._last_data.track
        self.assertEqual(provider._raw_lyric_anchor_clock["natural_advances"], 2)
        old_marker = session.timeline.last_updated_time
        # Public song identity may stay the same to preserve loaded lyrics.
        # A second application's otherwise identical SDK timeline must not
        # inherit the first application's projection origin or clock evidence.
        session.source_app_user_model_id = "Spotify.exe"
        changed = self._read(provider, 110.0)
        self.assertEqual(session.timeline.last_updated_time, old_marker)
        self.assertEqual(changed.track.track_id, prior.track_id)
        self.assertNotEqual(changed.track.source_app, prior.source_app)
        self.assertEqual(changed.track.position_source, "windows-media")
        self.assertEqual(changed.track.position_observed_seconds, 11.2)
        self.assertEqual(changed.track.position_sampled_at, 110.0)
        self.assertAlmostEqual(changed.track.position_seconds, 11.2)
        self.assertEqual(provider._last_raw_timeline_source_app, "Spotify.exe")
        self.assertEqual(provider._raw_lyric_anchor_clock["natural_advances"], 0)
        self.assertEqual(provider._raw_lyric_anchor_clock["advanced_at"], 110.0)
        self.assertEqual(provider._raw_lyric_anchor_clock["interval_seconds"], 0.0)

    def test_selected_uia_progress_and_row_keep_original_observation(self):
        for source in ("netease-uia-progress", "netease-uia-lyrics"):
            with self.subTest(source=source):
                provider, _session, reader, _store = self._provider()
                self._read(provider, 100.0)
                reader.sample = NeteasePositionSample(
                    25.0, 180.0, source, 0.92, 6, "6", 100.2, True,
                )
                with patch("api.providers.time.monotonic", return_value=100.7):
                    fused = asyncio.run(provider._read_visual_data())
                self.assertGreater(fused.track.position_seconds, 25.0)
                self.assertEqual(fused.track.position_observed_seconds, 25.0)
                result = self._read(provider, 100.7)
                self.assertEqual(result.track.position_source, source)
                # The existing discontinuity guard can hold the display at its
                # prior clock while preserving the new independent evidence.
                self.assertLess(result.track.position_seconds, 25.0)
                self.assertEqual(result.track.position_sampled_at, 100.2)
                self.assertEqual(result.track.position_observed_seconds, 25.0)
                projected = provider._project_visual_position(result, 101.2, 100.7)
                self.assertEqual(projected.track.position_observed_seconds, 25.0)

    def test_verified_seek_keeps_initial_observation_in_replacements_and_cold_start(self):
        for cold_start in (False, True):
            with self.subTest(cold_start=cold_start):
                provider, _session, _reader, _store = self._provider()
                prior = None if cold_start else self._read(provider, 100.0)
                track_id = "" if prior is None else prior.track.track_id
                with patch("api.providers.time.monotonic", return_value=100.1):
                    provider._remember_local_seek_for_track(track_id, 82.0, 180.0)
                if not cold_start:
                    self.assertEqual(provider._last_data.track.position_observed_seconds, 82.0)
                    held = provider._apply_recent_local_seek_to_visual_data(prior, 100.6)
                    self.assertAlmostEqual(held.track.position_seconds, 82.5)
                    self.assertEqual(held.track.position_observed_seconds, 82.0)
                result = self._read(provider, 100.8)
                self.assertEqual(result.track.position_source, "local-seek")
                self.assertAlmostEqual(result.track.position_seconds, 82.7)
                self.assertEqual(result.track.position_sampled_at, 100.1)
                self.assertEqual(result.track.position_observed_seconds, 82.0)
                projected = provider._project_visual_position(result, 101.3, 100.8)
                self.assertAlmostEqual(projected.track.position_seconds, 83.2)
                self.assertEqual(projected.track.position_observed_seconds, 82.0)

    def test_zero_and_true_raw_seek_reset_natural_progress_evidence(self):
        for position in (0.0, 82.0):
            with self.subTest(position=position):
                provider, session, reader, _store = self._provider(foreground=True)
                self._warm(provider, session)
                session.timeline.position = FakeTimeSpan(position)
                session.timeline.last_updated_time += 1
                reader.sample = self._coarse(82.0, 101.8)
                # The private fusion result must not suppress an actual raw
                # seek or fallback; normal seek confirmation remains outside.
                with patch("api.providers.time.monotonic", return_value=101.8):
                    result = asyncio.run(provider._read_visual_data())
                self.assertGreaterEqual(result.track.position_seconds, 82.0)
                if position > 0.0:
                    self.assertLessEqual(result.track.position_seconds, 84.0)
                    self.assertEqual(provider._raw_lyric_anchor_clock["natural_advances"], 0)

    def test_duplicate_polls_do_not_corroborate_and_projection_age_is_added_once(self):
        provider, session, reader, _store = self._provider()
        self._warm(provider, session)
        for now in (101.4, 101.6, 101.8):
            reader.sample = self._coarse(82.0, now)
            result = self._read(provider, now)
            self.assertAlmostEqual(result.track.position_seconds, 10.0 + now - 100.0)
        clock = provider._raw_lyric_anchor_clock
        self.assertEqual(clock["natural_advances"], 2)
        self.assertEqual(clock["advanced_at"], 101.2)
        isolated, isolated_session, _reader, _store = self._provider()
        for now in (100.0, 100.6, 101.2, 101.8):
            isolated_session.timeline.last_updated_time += 1
            self._read(isolated, now)
        self.assertEqual(isolated._raw_lyric_anchor_clock["natural_advances"], 0)

    def test_track_replacement_and_metadata_mismatch_do_not_inherit_corroboration(self):
        provider, session, _reader, _store = self._provider()
        self._warm(provider, session)
        self.assertFalse(provider._raw_timeline_corroborates_lyric_anchor(
            "new-song", 30.0, 101.8, 101.8, is_playing=True, metadata_matches=True,
        ))
        self.assertEqual(provider._raw_lyric_anchor_clock["natural_advances"], 0)
        self.assertFalse(provider._raw_timeline_corroborates_lyric_anchor(
            "new-song", 30.6, 102.4, 102.4, is_playing=True, metadata_matches=False,
        ))
        self.assertIsNone(provider._raw_lyric_anchor_clock)


if __name__ == "__main__":
    unittest.main()
