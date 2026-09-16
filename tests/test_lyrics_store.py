from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from api.lyrics import LocalLyricsStore, LyricsMatch
from api.models import TimedLyricLine


class LyricsStoreTests(unittest.TestCase):
    def test_ember_dual_line_setting_enables_dual_line_lyric_enrichment_for_ember_style(self):
        store = LocalLyricsStore.__new__(LocalLyricsStore)
        store.settings_getter = lambda key, default=None: {
            "v2_enable_dual_line_lyrics": False,
            "v2_lyric_flow_style": "ember_fade",
            "v2_ember_dual_line_enabled": True,
        }.get(key, default)

        self.assertTrue(store._dual_line_lyrics_enabled())

    def test_ember_dual_line_setting_does_not_enable_other_lyric_styles(self):
        store = LocalLyricsStore.__new__(LocalLyricsStore)
        store.settings_getter = lambda key, default=None: {
            "v2_enable_dual_line_lyrics": False,
            "v2_lyric_flow_style": "wave_stream",
            "v2_ember_dual_line_enabled": True,
        }.get(key, default)

        self.assertFalse(store._dual_line_lyrics_enabled())

    def test_cache_key_ignores_duration_jitter(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            first = store._cache_key("Song", "Artist", "Album", 180.0)
            second = store._cache_key("Song", "Artist", "Album", 0.0)
            third = store._cache_key("Song", "Artist", "Album", 181.2)

        self.assertEqual(first, second)
        self.assertEqual(second, third)

    def test_cache_key_ignores_late_album_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            pending_key = store._cache_key("Song", "Artist", "", 0.0)
            enriched_key = store._cache_key("Song", "Artist", "Late Album", 0.0)

        self.assertEqual(pending_key, enriched_key)

    def test_parse_lrc_applies_global_offset(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            timed, _plain = store._parse_lrc_text("[offset:500]\n[00:10.00]hello\n[00:11.00]world")

        self.assertEqual([line.text for line in timed], ["hello", "world"])
        self.assertAlmostEqual(timed[0].time_seconds, 10.5)
        self.assertAlmostEqual(timed[1].time_seconds, 11.5)

    def test_parse_lrc_clamps_negative_offset_at_zero(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            timed, _plain = store._parse_lrc_text("[offset:-700]\n[00:00.30]start")

        self.assertAlmostEqual(timed[0].time_seconds, 0.0)

    def test_parse_lrc_supports_multiple_time_tags(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            timed, _plain = store._parse_lrc_text("[00:01.00][00:02.50]repeat")

        self.assertEqual([line.text for line in timed], ["repeat", "repeat"])
        self.assertAlmostEqual(timed[0].time_seconds, 1.0)
        self.assertAlmostEqual(timed[1].time_seconds, 2.5)

    def test_parse_enhanced_lrc_strips_inline_word_timestamps(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            timed, _plain = store._parse_lrc_text("[00:03.00]<00:03.20>Hello <00:03.80>world")

        self.assertEqual(timed[0].text, "Hello world")
        self.assertAlmostEqual(timed[0].time_seconds, 3.0)

    def test_parse_yrc_downgrades_to_line_timing(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            timed, _plain = store._parse_lrc_text("[1200,2400](1200,500,0)你(1700,600,0)好")

        self.assertEqual(timed[0].text, "你好")
        self.assertAlmostEqual(timed[0].time_seconds, 1.2)

    def test_parse_lrc_keeps_credit_lines(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            timed, _plain = store._parse_lrc_text("[00:00.00]作词 : Someone\n[00:00.20]作曲：Someone\n[00:01.00]real lyric")

        self.assertEqual([line.text for line in timed], ["作词 : Someone", "作曲：Someone", "real lyric"])
        self.assertAlmostEqual(timed[0].time_seconds, 0.0)


    def test_lyrics_match_merges_same_timestamp_sources_as_two_lines(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            match = store._lyrics_match_from_text(
                (
                    "[03:11.08]薛：我记得那草坪 一览无遗",
                    "[03:11.08]刘：我等你的草坪 高楼耸起",
                ),
                "netease",
            )

        self.assertIsNotNone(match)
        self.assertEqual(match.timed_lyrics[0].text, "薛：我记得那草坪 一览无遗\n刘：我等你的草坪 高楼耸起")
        self.assertEqual(match.lyrics[0], "薛：我记得那草坪 一览无遗\n刘：我等你的草坪 高楼耸起")

    def test_netease_english_original_does_not_merge_translation_as_primary(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            lyric_text = store._extract_netease_lyric_text(
                {
                    "lrc": {"lyric": "[00:01.00]I keep dancing\n[00:02.00]Under city lights"},
                    "tlyric": {"lyric": "[00:01.00]我继续跳舞\n[00:02.00]在城市灯光下"},
                },
                prefer_original=True,
            )
            match = store._lyrics_match_from_text(lyric_text, "netease")

        self.assertIsInstance(lyric_text, str)
        self.assertIsNotNone(match)
        self.assertEqual([line.text for line in match.timed_lyrics], ["I keep dancing", "Under city lights"])

    def test_netease_english_translation_only_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            lyric_text = store._extract_netease_lyric_text(
                {"tlyric": {"lyric": "[00:01.00]我继续跳舞"}},
                prefer_original=True,
            )

        self.assertEqual(lyric_text, "")

    def test_netease_dual_line_can_prefer_transliteration(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            lyric_text = store._extract_netease_lyric_text(
                {
                    "lrc": {"lyric": "[00:01.00]\u3055\u304f\u3089"},
                    "tlyric": {"lyric": "[00:01.00]\u6a31\u82b1"},
                    "romalrc": {"lyric": "[00:01.00]sakura"},
                },
                secondary_source="transliteration",
            )
            match = store._lyrics_match_from_text(lyric_text, "netease")

        self.assertIsInstance(lyric_text, tuple)
        self.assertIsNotNone(match)
        self.assertEqual(match.timed_lyrics[0].text, "\u3055\u304f\u3089\nsakura")

    def test_lyrics_match_can_convert_traditional_chinese_to_simplified(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(
                Path(root),
                settings_getter=lambda key, default=None: "simplified" if key == "v2_lyric_chinese_conversion" else default,
            )

            match = store._lyrics_match_from_text("[00:01.00]\u5f8c\u4f86\u98a8\u88e1\u6709\u59b3", "local")

        self.assertIsNotNone(match)
        self.assertEqual(match.timed_lyrics[0].text, "\u540e\u6765\u98ce\u91cc\u6709\u4f60")

    def test_lyrics_match_rejects_timestamp_only_content(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            match = store._lyrics_match_from_text("[00:01.00]03:39", "netease")

        self.assertIsNone(match)

    def test_lyrics_match_rejects_single_clock_like_plain_line(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            match = store._lyrics_match_from_text("00:00", "lrclib")

        self.assertIsNone(match)

    def test_online_resolver_order_prefers_recorded_hits(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            store._record_source_hit("lrclib")
            store._record_source_hit("lrclib")
            store._record_source_hit("qq")

            order = [name for name, _resolver in store._ordered_online_resolvers()]

        self.assertEqual(order, ["lrclib", "qq", "netease"])

    def test_resolve_online_records_successful_source_hit(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            store._resolve_netease = lambda *_args: None
            store._resolve_qq = lambda *_args: LyricsMatch((TimedLyricLine(0.0, "line"),), ("line",), "qq")
            store._resolve_lrclib = lambda *_args: None

            match = store._resolve_online("Song", "Artist", "", 0.0)
            reloaded = LocalLyricsStore(Path(root))

        self.assertEqual(match.source, "qq")
        self.assertEqual(reloaded._source_hit_counts["qq"], 1)

    def test_resolve_online_stops_after_first_successful_source(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            calls = []

            store._resolve_netease = lambda *_args: calls.append("netease") or LyricsMatch((TimedLyricLine(0.0, "line"),), ("line",), "netease")
            store._resolve_qq = lambda *_args: calls.append("qq") or None
            store._resolve_lrclib = lambda *_args: calls.append("lrclib") or None

            match = store._resolve_online("Song", "Artist", "", 0.0)

        self.assertEqual(match.source, "netease")
        self.assertEqual(calls, ["netease"])

    def test_transient_lookup_miss_uses_short_retry_window(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            store.TRANSIENT_MISS_RETRY_SECONDS = 2.0
            store._resolve_online = lambda *_args: LyricsMatch((), (), "")
            key = store._cache_key("Song", "Artist", "", 0.0)

            store._store_miss(key, now=100.0, retry_seconds=2.0)
            with patch("api.lyrics.time.monotonic", return_value=101.0):
                blocked = store.resolve_in_background("Song", "Artist")
            with patch("api.lyrics.time.monotonic", return_value=102.1):
                retried = store.resolve_in_background("Song", "Artist")

        self.assertFalse(blocked)
        self.assertTrue(retried)

    def test_background_empty_lookup_uses_transient_retry_window(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            store.TRANSIENT_MISS_RETRY_SECONDS = 2.0
            store._resolve_online = lambda *_args: LyricsMatch((), (), "")
            key = store._cache_key("Song", "Artist", "", 0.0)

            with patch("api.lyrics.time.monotonic", return_value=100.0):
                store._resolve_background(key, "Song", "Artist", "", 0.0)
            with patch("api.lyrics.time.monotonic", return_value=101.0):
                blocked = store.resolve_in_background("Song", "Artist")
            with patch("api.lyrics.time.monotonic", return_value=102.1):
                retried = store.resolve_in_background("Song", "Artist")

        self.assertFalse(blocked)
        self.assertTrue(retried)

    def test_source_hit_counts_merge_legacy_and_generated_cache(self):
        with tempfile.TemporaryDirectory() as root:
            local_root = Path(root) / "lyrics"
            generated_root = Path(root) / "generated"
            local_root.mkdir()
            generated_root.mkdir()
            (local_root / ".source_hits.json").write_text('{"lrclib": 5, "netease": 1}', encoding="utf-8")
            (generated_root / ".source_hits.json").write_text('{"qq": 2, "lrclib": 0}', encoding="utf-8")

            store = LocalLyricsStore(local_root, generated_root=generated_root)
            order = [name for name, _resolver in store._ordered_online_resolvers()]

        self.assertEqual(store._source_hit_counts["lrclib"], 5)
        self.assertEqual(store._source_hit_counts["qq"], 2)
        self.assertEqual(order, ["lrclib", "qq", "netease"])

    def test_resolved_cache_keeps_bounded_recent_working_set(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            store.RESOLVED_CACHE_LIMIT = 3
            keys = []
            for index in range(3):
                key = store._cache_key(f"Track {index}", "", "", 0.0)
                keys.append(key)
                store._store_cache(key, LyricsMatch((TimedLyricLine(0.0, f"line {index}"),), (f"line {index}",), "test"))

            cached = store.resolve_cached_or_local("TRACK 0", "", "")
            key3 = store._cache_key("Track 3", "", "", 0.0)
            store._store_cache(key3, LyricsMatch((TimedLyricLine(0.0, "line 3"),), ("line 3",), "test"))

        self.assertEqual(cached.lyrics, ("line 0",))
        self.assertIn(keys[0], store._resolved_cache)
        self.assertNotIn(keys[1], store._resolved_cache)
        self.assertEqual(list(store._resolved_cache), [keys[2], keys[0], key3])

    def test_local_lyric_folder_matches_normalized_filename_when_exact_name_differs(self):
        with tempfile.TemporaryDirectory() as root:
            local_root = Path(root)
            store = LocalLyricsStore(local_root)
            (local_root / "alex vaughn muni long - iykyk.lrc").write_text("[00:01.00]line\n", encoding="utf-8")

            cached = store.resolve_cached_or_local("IYKYK", "Alex Vaughn, Muni Long")

        self.assertEqual(cached.lyrics, ("line",))
        self.assertEqual(cached.source, "local:alex vaughn muni long - iykyk.lrc")

    def test_local_lyric_folder_fuzzy_matches_extra_title_and_artist_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            local_root = Path(root)
            store = LocalLyricsStore(local_root)
            (local_root / "huntr x - golden.lrc").write_text("[00:01.00]golden line\n", encoding="utf-8")

            cached = store.resolve_cached_or_local(
                "Golden (from the Netflix film KPop Demon Hunters)",
                "HUNTR/X, EJAE, Audrey Nuna, REI AMI",
            )

        self.assertEqual(cached.lyrics, ("golden line",))
        self.assertEqual(cached.source, "local:huntr x - golden.lrc")

    def test_local_lyric_folder_fuzzy_does_not_match_different_title(self):
        with tempfile.TemporaryDirectory() as root:
            local_root = Path(root)
            store = LocalLyricsStore(local_root)
            (local_root / "artist - golden hour.lrc").write_text("[00:01.00]wrong line\n", encoding="utf-8")

            cached = store.resolve_cached_or_local("Golden", "Artist")

        self.assertEqual(cached.lyrics, ())
        self.assertEqual(cached.source, "")

    def test_persisted_online_lyrics_use_active_lyrics_folder_by_default(self):
        with tempfile.TemporaryDirectory() as root:
            local_root = Path(root)
            store = LocalLyricsStore(local_root)
            match = LyricsMatch((TimedLyricLine(1.0, "line"),), ("line",), "netease")

            store._persist_match("Song", "Artist", "", match)
            cached = LocalLyricsStore(local_root).resolve_cached_or_local("Song", "Artist")
            local_files = [path for path in local_root.glob("*.lrc") if path.is_file()]

        self.assertEqual([path.name for path in local_files], ["artist - song.lrc"])
        self.assertEqual(cached.lyrics, ("line",))

    def test_default_online_lyrics_cache_uses_user_cache_dir_not_source_lyrics(self):
        with tempfile.TemporaryDirectory() as root:
            source_root = Path(root) / "source-lyrics"
            cache_root = Path(root) / "runtime-cache"
            with patch.dict(os.environ, {"GLUSPOT_LYRICS_CACHE_DIR": str(cache_root)}):
                store = LocalLyricsStore(root=None)
                store.root = source_root
                store.root.mkdir(parents=True, exist_ok=True)
                store._legacy_source_hits_path = store.root / ".source_hits.json"
                store._source_hits_path = store.generated_root / ".source_hits.json"
                match = LyricsMatch((TimedLyricLine(1.0, "line"),), ("line",), "netease")

                store._persist_match("Song", "Artist", "", match)

                self.assertFalse((source_root / "artist - song.lrc").exists())
                self.assertTrue((cache_root / "artist - song.lrc").exists())

    def test_default_playback_lookup_skips_indexed_local_scan_before_online_lookup(self):
        with tempfile.TemporaryDirectory() as root:
            source_root = Path(root) / "source-lyrics"
            cache_root = Path(root) / "runtime-cache"
            source_root.mkdir(parents=True)
            (source_root / "unrelated - song.lrc").write_text("[00:01.00]line\n", encoding="utf-8")
            with patch.dict(os.environ, {"GLUSPOT_LYRICS_CACHE_DIR": str(cache_root)}):
                store = LocalLyricsStore(root=None)
                store.root = source_root
                store._legacy_source_hits_path = store.root / ".source_hits.json"
                store._source_hits_path = store.generated_root / ".source_hits.json"

                def fail_indexed_scan(_title, _artist):
                    raise AssertionError("playback lookup should not build the indexed local lyric scan")

                store._indexed_candidate_paths = fail_indexed_scan

                match = store.resolve_cached_or_local("Missing Song", "Missing Artist")

        self.assertEqual(match.lyrics, ())
        self.assertEqual(match.source, "")

    def test_local_miss_cache_skips_repeated_directory_scan_until_roots_change(self):
        with tempfile.TemporaryDirectory() as root:
            local_root = Path(root)
            store = LocalLyricsStore(local_root)
            calls = []
            original = store._lyric_file_paths

            def tracking_paths(directory):
                calls.append(str(directory))
                return original(directory)

            store._lyric_file_paths = tracking_paths

            first = store.resolve_cached_or_local("Missing Song", "Missing Artist")
            second = store.resolve_cached_or_local("Missing Song", "Missing Artist")

        self.assertEqual(first.lyrics, ())
        self.assertEqual(second.lyrics, ())
        self.assertEqual(len(calls), 1)

    def test_large_local_folder_skips_fuzzy_scan_after_exact_index_miss(self):
        with tempfile.TemporaryDirectory() as root:
            local_root = Path(root)
            for index in range(4):
                (local_root / f"artist - unrelated {index}.lrc").write_text("[00:01.00]line\n", encoding="utf-8")
            store = LocalLyricsStore(local_root)
            store.LOCAL_FUZZY_FILE_LIMIT = 3

            match = store.resolve_cached_or_local("Missing Song", "Artist")

        self.assertEqual(match.lyrics, ())

    def test_separate_generated_lyrics_cache_prunes_oldest_without_deleting_local_files(self):
        with tempfile.TemporaryDirectory() as root:
            local_root = Path(root)
            generated_root = Path(root) / "generated"
            store = LocalLyricsStore(local_root, generated_root=generated_root)
            store.PERSISTED_CACHE_FILE_LIMIT = 2
            local_file = local_root / "artist - local.lrc"
            local_file.write_text("[00:01.00]local\n", encoding="utf-8")
            store.generated_root.mkdir(parents=True, exist_ok=True)
            generated_paths = []
            for index in range(3):
                path = store.generated_root / f"cached-{index}.lrc"
                path.write_text(f"[00:0{index}.00]line {index}\n", encoding="utf-8")
                os.utime(path, (float(index + 1), float(index + 1)))
                generated_paths.append(path)

            store._prune_persisted_cache(now=10.0)

            remaining = sorted(path.name for path in store.generated_root.glob("*.lrc"))
            local_exists = local_file.exists()
            oldest_exists = generated_paths[0].exists()

        self.assertTrue(local_exists)
        self.assertFalse(oldest_exists)
        self.assertEqual(remaining, ["cached-1.lrc", "cached-2.lrc"])

    def test_missed_cache_is_bounded_for_long_sessions(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            store.MISSED_CACHE_LIMIT = 3
            for index in range(5):
                store._store_miss(f"missing-{index}", now=float(index))

        self.assertEqual(list(store._missed_at), ["missing-2", "missing-3", "missing-4"])
        self.assertLessEqual(len(store._missed_at), 3)


if __name__ == "__main__":
    unittest.main()
