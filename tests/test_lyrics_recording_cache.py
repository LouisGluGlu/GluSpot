from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.lyrics import LocalLyricsStore, LyricsMatch
from api.models import TimedLyricLine


class LyricRecordingCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.store = LocalLyricsStore(base / "manual", generated_root=base / "generated")

    @staticmethod
    def match(text="recording A", album="Album A", duration=180.0, verified=True):
        return LyricsMatch((TimedLyricLine(1.0, text),), (text,), "netease",
            verified_identity=("netease", "123", "Song", "Artist", album, duration) if verified else None)

    def test_known_album_or_duration_conflict_never_reuses_verified_memory(self):
        old = self.match()
        key = self.store._cache_key("Song", "Artist", "Album A", 180)
        self.assertTrue(self.store._store_cache(key, old))
        for album, duration in (("Album B", 180), ("Album A", 240)):
            with self.subTest(album=album, duration=duration):
                self.assertNotEqual(key, self.store._cache_key("Song", "Artist", album, duration))
                self.assertFalse(self.store.resolve_cached_or_local("Song", "Artist", album, duration).timed_lyrics)
        self.assertEqual(self.store.resolve_cached_or_local("Song", "Artist", "Album A", 181.2), old)

    def test_verified_match_refines_unknown_request_but_cannot_publish_to_conflicting_context(self):
        key = self.store._cache_key("Song", "Artist", "", 0)
        self.assertTrue(self.store._store_cache(key, self.match()))
        self.assertEqual(key, self.store._cache_key("Song", "Artist", "Album A", 180))
        self.assertNotEqual(key, self.store._cache_key("Song", "Artist", "Album B", 180))
        self.assertFalse(self.store._store_cache(key, self.match(album="Album B")))
        self.assertEqual(self.store._resolved_cache[key].lyrics, ("recording A",))

    def test_pending_unknown_and_conflicting_recordings_are_independent(self):
        with patch("api.lyrics.threading.Thread") as thread:
            self.assertTrue(self.store.resolve_in_background("Song", "Artist", "", 0))
            old_key = self.store._cache_key("Song", "Artist", "", 0)
            self.assertTrue(self.store.resolve_in_background("Song", "Artist", "Album B", 240))
            new_key = self.store._cache_key("Song", "Artist", "Album B", 240)
            self.assertNotEqual(old_key, new_key)
            self.assertEqual(thread.call_count, 2)
        with patch.object(self.store, "_resolve_online", return_value=self.match("new", "Album B", 240)):
            self.store._resolve_background(new_key, "Song", "Artist", "Album B", 240)
        with patch.object(self.store, "_resolve_online", return_value=self.match("old")):
            self.store._resolve_background(old_key, "Song", "Artist", "", 0)
        self.assertEqual(self.store.resolve_cached_or_local("Song", "Artist", "Album B", 240).lyrics, ("new",))
        self.assertEqual(self.store.resolve_cached_or_local("Song", "Artist", "Album A", 180).lyrics, ("old",))

    def test_miss_enrichment_and_trusted_ids_do_not_leak_between_recordings(self):
        first = self.store._cache_key("Song", "Artist", "Album A", 180)
        self.store._store_miss(first, retry_seconds=float("inf"))
        self.store._remember_platform_id("Song", "Artist", "Album A", 180, "netease", "123")
        self.store._enrich_attempted_at[first] = 1e30
        with patch("api.lyrics.threading.Thread") as thread:
            self.assertFalse(self.store.resolve_in_background("Song", "Artist", "Album A", 180))
            self.assertTrue(self.store.resolve_in_background("Song", "Artist", "Album B", 240))
            self.assertTrue(self.store.enrich_in_background("Song", "Artist", "Album B", 240))
            self.assertEqual(thread.call_count, 2)
        with patch.object(self.store, "resolve_trusted_lyrics") as trusted:
            self.assertIsNone(self.store._resolve_amll("Song", "Artist", "Album B", 240))
            trusted.assert_not_called()

    def test_unknown_recording_negative_and_enrich_cooldowns_cannot_certify_late_metadata(self):
        unknown = self.store._cache_key("Song", "Artist", "", 0)
        self.store._store_miss(unknown, retry_seconds=float("inf"))
        self.store._enrich_attempted_at[unknown] = 1e30
        with patch("api.lyrics.threading.Thread") as thread:
            self.assertTrue(self.store.resolve_in_background("Song", "Artist", "Album A", 180))
            self.assertTrue(self.store.enrich_in_background("Song", "Artist", "Album A", 180))
            self.assertEqual(thread.call_count, 2)
        self.assertNotEqual(unknown, self.store._cache_key("Song", "Artist", "Album A", 180))
        self.assertIn(unknown, self.store._missed_at)

    def test_conflicting_known_recordings_can_enrich_concurrently_without_suppressing_each_other(self):
        with patch("api.lyrics.threading.Thread") as thread:
            self.assertTrue(self.store.enrich_in_background("Song", "Artist", "Album A", 180))
            self.assertFalse(self.store.enrich_in_background("Song", "Artist", "Album A", 181.2))
            self.assertTrue(self.store.enrich_in_background("Song", "Artist", "Album B", 180))
            self.assertTrue(self.store.enrich_in_background("Song", "Artist", "Album A", 240))
            self.assertEqual(thread.call_count, 3)

    def test_same_platform_id_trusted_miss_cannot_suppress_other_recording_context(self):
        with patch.object(self.store, "_fetch_public_json", return_value=None) as fetch:
            for album, duration in (("Album A", 180), ("Album B", 180), ("Album A", 240)):
                self.assertIsNone(self.store.resolve_trusted_lyrics("netease", "123", "Song", "Artist", album, duration))
            self.assertEqual(fetch.call_count, 3)
            self.assertIsNone(self.store.resolve_trusted_lyrics("netease", "123", "Song", "Artist", "Album A", 180))
            self.assertEqual(fetch.call_count, 3)

    def test_generated_disk_metadata_roundtrip_fences_recording_without_persisting_trust(self):
        original = self.match()
        self.store._persist_match("Song", "Artist", "Album A", original)
        path = next(self.store.generated_root.glob("*.lrc"))
        self.assertIn("[length:180.000000]", path.read_text(encoding="utf-8"))
        self.assertIsNone(self.store._read_lrc_match(path).verified_identity)
        self.assertEqual(self.store.resolve_cached_or_local("Song", "Artist", "Album A", 181).lyrics, original.lyrics)
        self.assertFalse(self.store.resolve_cached_or_local("Song", "Artist", "Album B", 180).timed_lyrics)
        self.assertFalse(self.store.resolve_cached_or_local("Song", "Artist", "Album A", 240).timed_lyrics)

    def test_legacy_missing_metadata_requires_refresh_for_known_recording(self):
        self.store._persist_match("Song", "Artist", "Album A", self.match(verified=False))
        path = next(self.store.generated_root.glob("*.lrc"))
        self.assertTrue(self.store.resolve_cached_or_local("Song", "Artist", "Album A", 0).timed_lyrics)
        self.assertFalse(self.store.resolve_cached_or_local("Song", "Artist", "Album A", 180).timed_lyrics)
        self.assertTrue(path.exists())
        self.store._persist_match("Song", "Artist", "Album A", self.match("fresh"), duration_seconds=180)
        self.assertIn("[length:180.000000]", path.read_text(encoding="utf-8"))
        self.assertEqual(self.store.resolve_cached_or_local("Song", "Artist", "Album A", 180).lyrics, ("fresh",))

    def test_conflicting_generated_file_can_be_replaced_but_manual_file_always_wins(self):
        self.store._persist_match("Song", "Artist", "Album A", self.match())
        self.store._persist_match("Song", "Artist", "Album B", self.match("new", "Album B", 240))
        self.assertEqual(self.store.resolve_cached_or_local("Song", "Artist", "Album B", 240).lyrics, ("new",))
        manual = self.store.root / "artist - song.lrc"
        manual.write_text("[00:01]manual authority\n", encoding="utf-8")
        for album, duration in (("Album A", 180), ("Album C", 300)):
            key = self.store._cache_key("Song", "Artist", album, duration)
            self.assertEqual(self.store.resolve_cached_or_local("Song", "Artist", album, duration).lyrics, ("manual authority",))
            self.assertFalse(self.store._store_cache(key, self.match(), protect_manual=True,
                manual_names=self.store._candidate_names("Song", "Artist")))
        self.assertEqual(manual.read_text(encoding="utf-8"), "[00:01]manual authority\n")

    def test_recording_contexts_and_associated_caches_are_bounded_and_clearable(self):
        self.store.RECORDING_CONTEXT_LIMIT = 3
        for index in range(12):
            album = f"Album {index}"
            key = self.store._cache_key("Song", "Artist", album, 180)
            self.store._store_cache(key, self.match(album=album))
            self.store._store_miss(key)
            self.store._remember_platform_id("Song", "Artist", album, 180, "netease", str(index))
        for name in ("_recording_contexts", "_resolved_cache", "_missed_at", "_trusted_platform_ids"):
            self.assertLessEqual(len(getattr(self.store, name)), 3, name)
        self.store.clear_generated_cache()
        self.assertFalse(self.store._recording_contexts)


if __name__ == "__main__":
    unittest.main()
