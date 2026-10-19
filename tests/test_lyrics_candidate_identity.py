from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.lyrics import LocalLyricsStore


class LyricsCandidateIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = LocalLyricsStore(Path(temporary.name))

    def test_exact_title_does_not_accept_wrong_artist_via_combined_score(self):
        wrong_artist = {"trackName": "Shine on", "artistName": "Unrelated performer", "duration": 180.0}
        candidate = self.store._pick_best_candidate(
            [wrong_artist], "Shine on", "Example Artist", "", self.store._lrclib_candidate_text, 180.0,
        )
        self.assertIsNone(candidate)

    def test_exact_artist_does_not_accept_wrong_title_via_combined_score(self):
        wrong_title = {"trackName": "Different title", "artistName": "Example Artist", "duration": 180.0}
        candidate = self.store._pick_best_candidate(
            [wrong_title], "Shine on", "Example Artist", "", self.store._lrclib_candidate_text, 180.0,
        )
        self.assertIsNone(candidate)

    def test_three_source_duration_units_reject_different_recording(self):
        cases = (
            (self.store._netease_candidate_text, {"name": "Shine on", "artists": [{"name": "Example Artist"}], "dt": 220000}),
            (self.store._netease_candidate_text, {"name": "Shine on", "artists": [{"name": "Example Artist"}], "duration": 220000}),
            (self.store._qq_candidate_text, {"songname": "Shine on", "singer": [{"name": "Example Artist"}], "interval": 220}),
            (self.store._lrclib_candidate_text, {"trackName": "Shine on", "artistName": "Example Artist", "duration": 220}),
        )
        for resolver, row in cases:
            with self.subTest(source=resolver.__name__, fields=row):
                self.assertIsNone(self.store._pick_best_candidate(
                    [row], "Shine on", "Example Artist", "", resolver, 180.0,
                ))
                self.assertIs(self.store._pick_best_candidate(
                    [row], "Shine on", "Example Artist", "", resolver, 220.0,
                ), row)

    def test_known_duration_tolerance_and_missing_duration_are_conservative(self):
        row = {"trackName": "Shine on", "artistName": "Example Artist", "duration": 183.5}
        self.assertIs(self.store._pick_best_candidate(
            [row], "Shine on", "Example Artist", "", self.store._lrclib_candidate_text, 180.0,
        ), row)
        row["duration"] = 183.7
        self.assertIsNone(self.store._pick_best_candidate(
            [row], "Shine on", "Example Artist", "", self.store._lrclib_candidate_text, 180.0,
        ))
        row.pop("duration")
        self.assertIs(self.store._pick_best_candidate(
            [row], "Shine on", "Example Artist", "", self.store._lrclib_candidate_text, 180.0,
        ), row)

    def test_explicit_version_conflicts_are_rejected(self):
        for title in ("Shine on (Live)", "Shine on - Remix", "Shine on (Instrumental)", "Shine on Studio Version"):
            with self.subTest(candidate_title=title):
                row = {"trackName": title, "artistName": "Example Artist"}
                self.assertIsNone(self.store._pick_best_candidate(
                    [row], "Shine on", "Example Artist", "", self.store._lrclib_candidate_text,
                ))
        studio = {"trackName": "Shine on (Studio)", "artistName": "Example Artist"}
        self.assertIsNone(self.store._pick_best_candidate(
            [studio], "Shine on (Live)", "Example Artist", "", self.store._lrclib_candidate_text,
        ))
        live = {"trackName": "Shine on (Live)", "artistName": "Example Artist"}
        self.assertIs(self.store._pick_best_candidate(
            [live], "Shine on (Live)", "Example Artist", "", self.store._lrclib_candidate_text,
        ), live)

    def test_live_in_original_song_name_is_not_invented_version_metadata(self):
        self.assertFalse(self.store._explicit_version_tags("Live Forever"))
        self.assertFalse(self.store._explicit_version_tags("Alive"))

    def test_radio_edit_extended_and_specific_remixer_conflicts_are_rejected(self):
        long_title = "Lose My Mind (feat. Doja Cat)"
        for requested, supplied in (
            (long_title, long_title + " (Radio Edit)"),
            (long_title, long_title + " Extended Mix"),
            (long_title + " (A Remix)", long_title + " (B Remix)"),
            (long_title + " (Extended Mix)", long_title + " (Radio Edit)"),
            (long_title + " (Live at Wembley)", long_title + " (Live at Glastonbury)"),
            (long_title + " (2011 Remastered)", long_title + " (2022 Remastered)"),
        ):
            with self.subTest(requested=requested, supplied=supplied):
                self.assertFalse(self.store._candidate_identity_matches(
                    requested, "Don Toliver / Doja Cat", supplied, "Don Toliver / Doja Cat", 209., 209.,
                ))

    def test_version_formatting_and_feat_parentheses_do_not_invent_conflicts(self):
        for requested, supplied in (
            ("Shine on (A Remix)", "Shine on - A Remix Version"),
            ("Shine on (Radio Edit)", "Shine on Radio Version"),
            ("Shine on (Extended Mix)", "Shine on - Extended Version"),
            ("Shine on (Live)", "Shine on Live Version"),
            ("Lose My Mind (feat. Doja Cat)", "Lose My Mind (feat. Doja Cat)"),
        ):
            with self.subTest(requested=requested, supplied=supplied):
                self.assertTrue(self.store._candidate_identity_matches(
                    requested, "Don Toliver / Doja Cat", supplied, "Don Toliver / Doja Cat", 209., 209.,
                ))
        self.assertFalse(self.store._explicit_version_tags("Lose My Mind (feat. Doja Cat)"))

    def test_resolver_does_not_bind_a_different_named_remix_as_verified_identity(self):
        title = "Lose My Mind (feat. Doja Cat) (A Remix)"
        search = {"result": {"songs": [{
            "id": 123, "name": title.replace("A Remix", "B Remix"),
            "ar": [{"name": "Don Toliver"}, {"name": "Doja Cat"}], "dt": 209000,
        }]}}
        with patch.object(self.store, "_fetch_json", return_value=search) as fetch:
            self.assertIsNone(self.store._resolve_netease(title, "Don Toliver / Doja Cat", "", 209.))
        fetch.assert_called_once()
        self.assertFalse(self.store._trusted_platform_ids)

    def test_netease_resolver_rejects_duration_mismatch_before_fetching_lyrics(self):
        search = {"result": {"songs": [{
            "id": 123, "name": "Shine on", "artists": [{"name": "Example Artist"}], "dt": 220000,
        }]}}
        with patch.object(self.store, "_fetch_json", return_value=search) as fetch:
            self.assertIsNone(self.store._resolve_netease("Shine on", "Example Artist", "", 180.0))
            fetch.assert_called_once()

    def test_qq_resolver_rejects_duration_mismatch_before_fetching_lyrics(self):
        search = {"data": {"song": {"list": [{
            "songmid": "mid123", "songname": "Shine on", "singer": [{"name": "Example Artist"}], "interval": 220,
        }]}}}
        with patch.object(self.store, "_fetch_json", return_value=search) as fetch:
            self.assertIsNone(self.store._resolve_qq("Shine on", "Example Artist", "", 180.0))
            fetch.assert_called_once()

    def test_lrclib_get_metadata_mismatch_falls_back_to_valid_search(self):
        wrong = {"trackName": "Shine on", "artistName": "Unrelated performer", "duration": 180,
                 "syncedLyrics": "[00:01.00]wrong recording"}
        correct = {"trackName": "Shine on", "artistName": "Example Artist", "duration": 180,
                   "syncedLyrics": "[00:01.00]right recording"}
        with patch.object(self.store, "_fetch_json", side_effect=[wrong, [correct]]) as fetch:
            result = self.store._resolve_lrclib("Shine on", "Example Artist", "", 180.0)
        self.assertEqual(result.timed_lyrics[0].text, "right recording")
        self.assertEqual(fetch.call_count, 2)

    def test_lrclib_get_known_duration_and_version_are_checked(self):
        for metadata in ({"duration": 220}, {"trackName": "Shine on (Live)", "duration": 180}):
            with self.subTest(metadata=metadata):
                wrong = {"trackName": "Shine on", "artistName": "Example Artist", **metadata,
                         "syncedLyrics": "[00:01.00]wrong recording"}
                with patch.object(self.store, "_fetch_json", side_effect=[wrong, []]):
                    self.assertIsNone(self.store._resolve_lrclib("Shine on", "Example Artist", "", 180.0))

    def test_lrclib_get_verified_metadata_returns_without_search(self):
        payload = {"trackName": "Shine on", "artistName": "Example Artist", "albumName": "Album",
                   "duration": 180, "syncedLyrics": "[00:01.00]correct recording"}
        with patch.object(self.store, "_fetch_json", return_value=payload) as fetch:
            result = self.store._resolve_lrclib("Shine on", "Example Artist", "Album", 180.0)
            fetch.assert_called_once()
        self.assertEqual(result.timed_lyrics[0].text, "correct recording")

    def test_lrclib_get_legacy_lyric_only_fixture_remains_supported(self):
        with patch.object(self.store, "_fetch_json", return_value={"syncedLyrics": "[00:01.00]legacy"}) as fetch:
            result = self.store._resolve_lrclib("Shine on", "Example Artist", "", 180.0)
            fetch.assert_called_once()
        self.assertEqual(result.timed_lyrics[0].text, "legacy")


if __name__ == "__main__":
    unittest.main()
