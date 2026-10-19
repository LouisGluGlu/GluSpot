"""Trusted lyric upgrades reuse the owned player's existing worker fences."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import LyricPayload, OnlineMusicError, OnlineSong, StreamInfo
from local.owned_music_player import OwnedMusicPlayer
from tests.test_owned_playback import Tasks, ThreadRecordingPlayer


class OwnedLyricEnrichmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.settings = {"v2_enable_dual_line_lyrics": False,
                         "v2_dual_line_secondary_source": "translation"}
        self.store = LocalLyricsStore(Path(self.folder.name) / "manual",
            generated_root=Path(self.folder.name) / "generated",
            settings_getter=lambda key, default=None: self.settings.get(key, default))
        self.payload = LyricPayload("netease", "1",
            raw_lrc="[00:01.00]Shine on\n[00:04.00]Hold me",
            translation="[00:01.00]继续闪耀\n[00:04.00]抱着我",
            romanization="[00:01.00]shine romanized\n[00:04.00]hold romanized",
            source="official")
        self.candidate = self.store.parse_online_lyrics(
            raw_yrc="[1000,1000](1000,500,0)Shine (1500,500,0)on\n"
                    "[4000,1000](4000,500,0)Hold (4500,500,0)me",
            source="amll-ttml:trusted-id")
        self.lookup = Mock(return_value=self.candidate)
        self.store.resolve_trusted_lyrics = self.lookup
        self.tasks, self.prefetch, self.qt_player = Tasks(), Tasks(), ThreadRecordingPlayer()
        self.service = SimpleNamespace(lyrics=Mock(return_value=self.payload))
        self.player = OwnedMusicPlayer(self.service, self.store, player=self.qt_player,
            audio=object(), tasks=self.tasks, artwork_tasks=Tasks(),
            prefetch_tasks=self.prefetch, stream_cache=None)
        self.addCleanup(self.player.close)
        self.songs = (OnlineSong("netease", "1", "Shine", "Singer", "Album", 120),
                      OnlineSong("netease", "2", "Other", "Singer", "Album", 150))

    def start(self):
        self.player.play_queue(self.songs)
        token = self.tasks.jobs[-1][0]
        self.tasks.completed.emit(token, StreamInfo("netease", "1", "https://example.test/audio", True), None)
        token, action = self.tasks.jobs[-1]
        self.assertEqual(token[0], "lyrics")
        return token, action

    def load(self):
        token, action = self.start()
        self.tasks.completed.emit(token, action(), None)
        self.assertEqual(self.player.track.lyrics, ("Shine on", "Hold me"))
        return next(job for job in reversed(self.prefetch.jobs) if job[0][0] == "lyrics_enrichment")

    def finish(self, job, *, error=None):
        token, action = job
        self.prefetch.completed.emit(token, None if error else action(), error)

    def finish_parse(self):
        token, action = self.tasks.jobs[-1]
        self.assertEqual(token[0], "lyrics_refresh")
        self.tasks.completed.emit(token, action(), None)

    def test_official_lyrics_show_before_any_backup_io_and_keep_playback_clock(self):
        job = self.load()
        self.lookup.assert_not_called()
        self.player.track = replace(self.player.track, position_seconds=37.5,
                                    position_observed_seconds=37.5, position_sampled_at=12.)
        before = self.player.track
        self.qt_player.calls.clear()
        self.finish(job)
        self.assertEqual(self.player.track, before)
        self.finish_parse()
        self.assertEqual(self.player.track.lyrics_source, "amll-ttml:trusted-id")
        self.assertTrue(self.player.track.timed_lyrics[0].units)
        self.assertEqual(self.player.track.position_seconds, 37.5)
        self.assertEqual(self.player.track.position_observed_seconds, 37.5)
        self.assertEqual(self.qt_player.calls, [])
        self.lookup.assert_called_once_with("netease", "1", "Shine", "Singer", "Album", 120)

    def test_failure_miss_and_incompatible_results_keep_established_lyrics(self):
        for failure in ("timeout", "not_found", "other_version"):
            with self.subTest(failure=failure):
                job = self.load()
                before = self.player.track
                if failure == "not_found":
                    self.lookup.return_value = None
                elif failure == "other_version":
                    self.lookup.return_value = self.store.parse_online_lyrics(
                        raw_yrc="[1000,1000](1000,500,0)Different (1500,500,0)words",
                        source="unrelated")
                self.finish(job, error="timeout" if failure == "timeout" else None)
                self.assertEqual(self.player.track, before)
                self.assertIsNone(self.player._lyrics_enrichment_source)
                self.assertIsNone(self.player._lyrics_enrichment_pending)
                self.lookup.return_value = self.candidate

    def test_queue_rejection_does_not_clear_current_lyrics_or_retry_forever(self):
        self.prefetch.accepted = False
        self.load()
        before = self.player.track
        self.assertIsNone(self.player._lyrics_enrichment_pending)
        count = len(self.prefetch.jobs)
        self.player._apply_lyrics_match(self.store.parse_online_lyrics(raw_lrc=self.payload.raw_lrc))
        self.assertEqual(self.player.track.lyrics, before.lyrics)
        self.assertEqual(len(self.prefetch.jobs), count)

    def test_old_result_cannot_cross_song_catalog_or_stop_epochs(self):
        for change in ("next", "queue", "catalog", "stop"):
            with self.subTest(change=change):
                job = self.load()
                result = job[1]()
                if change == "next":
                    self.player.send_playback_command("next")
                elif change == "queue":
                    self.player.play_queue(self.songs)
                elif change == "catalog":
                    self.player._catalog_epoch += 1
                else:
                    self.player.stop()
                before = self.player.track
                self.prefetch.completed.emit(job[0], result, None)
                self.assertEqual(self.player.track, before)
                self.assertIsNone(self.player._lyrics_enrichment_source)

    def test_old_action_skips_network_after_rapid_song_changes(self):
        job = self.load()
        self.player.send_playback_command("next")
        self.player.play_queue(self.songs)
        self.assertIsNone(job[1]())
        self.lookup.assert_not_called()

    def test_same_id_changed_version_or_duration_is_not_the_same_target(self):
        for alteration in ({"title": "Shine (Live)"}, {"duration_seconds": 180}):
            with self.subTest(alteration=alteration):
                job = self.load()
                result = job[1]()
                self.player.queue = (replace(self.songs[0], **alteration), self.songs[1])
                before = self.player.track
                self.prefetch.completed.emit(job[0], result, None)
                self.assertEqual(self.player.track, before)
                self.assertIsNone(self.player._lyrics_enrichment_source)

    def test_in_flight_preference_parse_is_superseded_by_enrichment_without_old_overwrite(self):
        job = self.load()
        self.settings["v2_enable_dual_line_lyrics"] = True
        self.player.refresh_lyrics()
        old = self.tasks.jobs[-1]
        old_result = old[1]()
        self.finish(job)
        self.tasks.completed.emit(old[0], old_result, None)
        self.assertEqual(self.player.track.lyrics_source, "official")
        self.finish_parse()
        self.assertEqual(self.player.track.lyrics_source, "amll-ttml:trusted-id")
        self.assertIn("继续闪耀", self.player.track.lyrics[0])

    def test_raw_payload_translation_and_romanization_survive_upgrade_and_toggles(self):
        job = self.load()
        raw = self.payload
        candidate_raw = self.candidate.raw_sources
        self.finish(job)
        self.finish_parse()
        for secondary, expected in (("translation", "继续闪耀"),
                                    ("transliteration", "shine romanized"),
                                    ("none", "Shine on")):
            self.settings.update(v2_enable_dual_line_lyrics=True,
                                 v2_dual_line_secondary_source=secondary)
            self.assertTrue(self.player.refresh_lyrics())
            self.finish_parse()
            self.assertIn(expected, self.player.track.lyrics[0])
            self.assertTrue(self.player.track.timed_lyrics[0].units)
            self.assertIs(self.player._lyric_payload, raw)
            self.assertEqual(self.player._lyrics_enrichment_source.raw_sources, candidate_raw)
        self.lookup.assert_called_once()
        self.service.lyrics.assert_called_once()

    def test_failed_enrichment_reparse_keeps_current_official_lyrics(self):
        job = self.load()
        before = self.player.track
        self.finish(job)
        self.tasks.completed.emit(self.tasks.jobs[-1][0], None, "request_failed")
        self.assertEqual(self.player.track, before)

    def test_official_real_word_timing_does_not_trigger_backup_lookup(self):
        self.service.lyrics.return_value = replace(self.payload,
            raw_yrc=dict(self.candidate.raw_sources)["raw_yrc"])
        self.player.play_queue(self.songs)
        self.tasks.completed.emit(self.tasks.jobs[-1][0],
            StreamInfo("netease", "1", "https://example.test/audio", True), None)
        token, action = self.tasks.jobs[-1]
        self.tasks.completed.emit(token, action(), None)
        self.assertTrue(self.player.track.timed_lyrics[0].units)
        self.assertFalse(any(token[0] == "lyrics_enrichment" for token, _ in self.prefetch.jobs))
        self.lookup.assert_not_called()

    def test_one_real_word_row_does_not_prevent_background_upgrade(self):
        self.service.lyrics.return_value = replace(self.payload,
            raw_yrc="[1000,1000](1000,500,0)Shine (1500,500,0)on\n"
                    "[4000,1000](4000,1000,0)Hold me")
        token, action = self.start()
        self.tasks.completed.emit(token, action(), None)
        before = self.player.track
        job = next(job for job in self.prefetch.jobs if job[0][0] == "lyrics_enrichment")
        self.lookup.assert_not_called()
        self.finish(job)
        self.assertEqual(self.player.track, before)
        self.finish_parse()
        self.assertEqual(self.player.track.lyrics_source, "amll-ttml:trusted-id")
        self.assertEqual(len(self.player.track.timed_lyrics[1].units), 2)

    def test_verified_identity_comes_from_immutable_queue_song_and_matching_payload(self):
        match = self.player._parse_lyrics_payload(self.payload, self.settings, song=self.songs[0])
        self.assertEqual(match.verified_identity, ("netease", "1", "Shine", "Singer", "Album", 120))
        mismatch = self.player._parse_lyrics_payload(self.payload, self.settings, song=self.songs[1])
        self.assertIsNone(mismatch.verified_identity)

    def test_qrc_and_ttml_payload_fields_reach_existing_parser(self):
        parser = Mock(return_value=self.candidate)
        self.store.parse_online_lyrics = parser
        payload = replace(self.payload, raw_qrc="qrc-source", raw_ttml="ttml-source",
                          raw_netease='{"ytlrc":{"lyric":"raw-translation"}}')
        self.assertIs(self.player._parse_lyrics_payload(payload, self.settings), self.candidate)
        self.assertEqual(parser.call_args.kwargs["raw_qrc"], "qrc-source")
        self.assertEqual(parser.call_args.kwargs["raw_ttml"], "ttml-source")
        self.assertEqual(parser.call_args.kwargs["raw_netease"], payload.raw_netease)

    def test_official_endpoint_failure_still_queues_exact_id_fallback(self):
        self.service.lyrics.side_effect = OnlineMusicError("unavailable", "netease")
        token, action = self.start()
        payload, match = action()
        self.assertEqual((payload.source_id, payload.song_id), ("netease", "1"))
        self.assertEqual(payload.source, "netease_unavailable")
        self.assertIsNone(match)
        self.tasks.completed.emit(token, (payload, match), None)
        self.assertEqual(self.player.track.lyrics, ("暂无歌词",))
        self.assertEqual(self.player.status, "playing")
        job = self.prefetch.jobs[-1]
        self.assertEqual(job[0][0], "lyrics_enrichment")
        self.finish(job)
        self.finish_parse()
        self.assertEqual(self.player.track.lyrics_source, "amll-ttml:trusted-id")
        self.assertTrue(self.player.track.timed_lyrics[0].units)
        self.assertIs(self.player._lyric_payload, payload)

    def test_official_failure_never_clears_current_established_lyrics(self):
        self.load()
        before = self.player.track
        self.service.lyrics.side_effect = OnlineMusicError("timeout", "netease")
        result = self.player._fetch_lyrics(self.songs[0], self.settings)
        token = ("lyrics", self.player._epoch, self.player._lyrics_generation)
        self.tasks.completed.emit(token, result, None)
        self.assertEqual(self.player.track, before)
        self.tasks.completed.emit(token, None, "request_failed")
        self.assertEqual(self.player.track, before)

    def test_cancelled_or_closed_official_requests_do_not_spawn_fallback(self):
        for code in ("cancelled", "closed"):
            with self.subTest(code=code):
                self.service.lyrics.side_effect = OnlineMusicError(code, "netease")
                with self.assertRaises(OnlineMusicError):
                    self.player._fetch_lyrics(self.songs[0], self.settings)
        self.lookup.assert_not_called()
        self.assertFalse(self.prefetch.jobs)

    def test_stale_failed_official_response_cannot_spawn_a_new_song_fallback(self):
        self.service.lyrics.side_effect = OnlineMusicError("timeout", "netease")
        token, action = self.start()
        result = action()
        self.player.send_playback_command("next")
        before = self.player.track
        self.tasks.completed.emit(token, result, None)
        self.assertEqual(self.player.track, before)
        self.assertFalse(any(token[0] == "lyrics_enrichment" for token, _ in self.prefetch.jobs))

    def test_wrong_payload_identity_or_type_preserves_current_and_finishes_initial_loading(self):
        for invalid in (replace(self.payload, song_id="other"),
                        replace(self.payload, source_id="qq"), object()):
            with self.subTest(payload_type=type(invalid).__name__,
                              identity=getattr(invalid, "song_id", "wrong-type")):
                self.load()
                before, original_payload = self.player.track, self.player._lyric_payload
                token = ("lyrics", self.player._epoch, self.player._lyrics_generation)
                self.tasks.completed.emit(token, (invalid, self.candidate), None)
                self.assertEqual(self.player.track, before)
                self.assertIs(self.player._lyric_payload, original_payload)
                initial, _action = self.start()
                self.tasks.completed.emit(initial, (invalid, self.candidate), None)
                self.assertEqual(self.player.track.lyrics, ("暂无歌词",))
                self.assertIsNone(self.player._lyric_payload)
                self.assertIsNone(self.player._lyrics_enrichment_pending)


if __name__ == "__main__":
    unittest.main()
