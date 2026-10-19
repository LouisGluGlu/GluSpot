from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.lyrics import LocalLyricsStore, LyricsMatch
from api.models import TimedLyricLine, TimedLyricUnit


class LyricsTranslationRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.settings = {"v2_enable_dual_line_lyrics": True}
        self.store = LocalLyricsStore(
            self.base / "local", generated_root=self.base / "generated",
            settings_getter=lambda key, default=None: self.settings.get(key, default),
        )
        self.original = TimedLyricLine(1.0, "Shine on", (
            TimedLyricUnit(1.0, 1.5, "Shine "), TimedLyricUnit(1.5, 2.0, "on"),
        ), "unit")

    def _match(self, row, source="qq"):
        return LyricsMatch((row,), (row.text,), source)

    def test_preferred_unit_source_does_not_hide_later_translation(self):
        calls = []
        self.store._source_hit_counts.update(qq=20, netease=1, lrclib=0)
        self.store._resolve_qq = lambda *_args: calls.append("qq") or self._match(self.original)
        translated = self._match(TimedLyricLine(1.5, "Shine on\n继续闪耀"), "netease")
        self.store._resolve_netease = lambda *_args: calls.append("netease") or translated
        self.store._resolve_lrclib = lambda *_args: calls.append("lrclib") or None

        result = self.store._resolve_online("Song", "Artist", "", 0.0)

        self.assertEqual(calls, ["qq", "netease"])
        self.assertEqual(result.timed_lyrics[0].text, "Shine on\n继续闪耀")
        self.assertEqual(result.timed_lyrics[0].units, self.original.units)
        self.assertEqual(result.timed_lyrics[0].time_seconds, self.original.time_seconds)
        self.assertEqual(result.source, "qq")

    def test_translation_disabled_retains_existing_fast_unit_source_route(self):
        self.settings["v2_enable_dual_line_lyrics"] = False
        calls = []
        self.store._source_hit_counts.update(qq=20, netease=1)
        self.store._resolve_qq = lambda *_args: calls.append("qq") or self._match(self.original)
        self.store._resolve_netease = lambda *_args: calls.append("netease") or None

        result = self.store._resolve_online("Song", "Artist", "", 0.0)

        self.assertEqual(calls, ["qq"])
        self.assertEqual(result.timed_lyrics, (self.original,))

    def test_original_is_published_before_slow_translation_source_finishes(self):
        visible = []
        self.store._source_hit_counts.update(qq=20, netease=1)
        self.store._resolve_qq = lambda *_args: self._match(self.original)

        def later_translation(*_args):
            self.assertEqual(visible, [self._match(self.original)])
            return self._match(TimedLyricLine(1.5, "Shine on\n继续闪耀"), "netease")

        self.store._resolve_netease = later_translation
        self.store._resolve_lrclib = lambda *_args: None
        result = self.store._resolve_online("Song", "Artist", "", 0.0, visible.append)

        self.assertEqual(result.timed_lyrics[0].text, "Shine on\n继续闪耀")

    def test_line_only_translation_supplement_does_not_erase_cached_unit_clock(self):
        key = self.store._cache_key("Song", "Artist", "", 0.0)
        self.store._store_cache(key, self._match(self.original))
        translated = self._match(TimedLyricLine(1.5, "Shine on\n继续闪耀"), "netease")
        with patch.object(self.store, "_resolve_online", return_value=translated):
            self.store._enrich_background(key, "Song", "Artist", "", 0.0)

        result = self.store.resolve_cached_or_local("Song", "Artist")
        self.assertEqual(result.timed_lyrics[0].text, "Shine on\n继续闪耀")
        self.assertEqual(result.timed_lyrics[0].time_seconds, 1.0)
        self.assertEqual(result.timed_lyrics[0].units, self.original.units)

    def test_backing_vocal_does_not_prevent_translation_supplement(self):
        original_with_aside = TimedLyricLine(
            1.0, "Shine on\n(shine)", self.original.units, "unit",
        )
        self.store._source_hit_counts.update(qq=20, netease=1)
        self.store._resolve_qq = lambda *_args: self._match(original_with_aside)
        self.store._resolve_netease = lambda *_args: self._match(
            TimedLyricLine(1.5, "Shine on\n继续闪耀"), "netease",
        )
        self.store._resolve_lrclib = lambda *_args: None

        result = self.store._resolve_online("Song", "Artist", "", 0.0)

        self.assertIn("继续闪耀", result.timed_lyrics[0].text)
        self.assertIn("(shine)", result.timed_lyrics[0].text)
        self.assertEqual(result.timed_lyrics[0].units, self.original.units)

    def test_failed_translation_supplement_retries_after_short_cooldown(self):
        key = self.store._cache_key("Song", "Artist", "", 0.0)
        self.store._store_cache(key, self._match(self.original))
        with patch("api.lyrics.time.monotonic", return_value=100.0), patch("api.lyrics.threading.Thread"):
            self.assertTrue(self.store.enrich_in_background("Song", "Artist"))
        with patch("api.lyrics.time.monotonic", return_value=100.0), patch.object(
            self.store, "_resolve_online", return_value=self._match(self.original),
        ):
            self.store._enrich_background(key, "Song", "Artist", "", 0.0)
        with patch("api.lyrics.time.monotonic", return_value=101.0), patch("api.lyrics.threading.Thread"):
            self.assertFalse(self.store.enrich_in_background("Song", "Artist"))
        with patch("api.lyrics.time.monotonic", return_value=146.0), patch("api.lyrics.threading.Thread"):
            self.assertTrue(self.store.enrich_in_background("Song", "Artist"))

    def test_unrelated_source_translation_is_not_attached_to_current_song(self):
        self.store._source_hit_counts.update(qq=20, netease=1)
        self.store._resolve_qq = lambda *_args: self._match(self.original)
        self.store._resolve_netease = lambda *_args: self._match(
            TimedLyricLine(1.0, "Another song\n另一首歌"), "netease",
        )
        self.store._resolve_lrclib = lambda *_args: None
        result = self.store._resolve_online("Song", "Artist", "", 0.0)
        self.assertEqual(result.timed_lyrics, (self.original,))

    def test_later_actual_lrc_upgrades_already_published_synthetic_original(self):
        synthetic = self.store._lyrics_match_from_text("Shine on", "netease")
        timed = self._match(TimedLyricLine(8.0, "Shine on"), "qq")
        self.store._resolve_netease = lambda *_args: synthetic
        self.store._resolve_qq = lambda *_args: timed
        self.store._resolve_lrclib = lambda *_args: None
        published = []

        result = self.store._resolve_online("Song", "Artist", "", 0.0, published.append)

        self.assertEqual(published, [synthetic])
        self.assertEqual(result.timed_lyrics, timed.timed_lyrics)
        self.assertEqual(result.timed_lyrics[0].timing_kind, "line")
        self.assertEqual(result.source, "qq")

    def test_source_success_order_and_cache_eviction_keep_translations_across_songs(self):
        self.store.RESOLVED_CACHE_LIMIT = 3
        calls = []

        def units(title, *_args):
            calls.append((title, "qq"))
            text = title + " shines"
            row = TimedLyricLine(1.0, text, (
                TimedLyricUnit(1.0, 1.5, title + " "), TimedLyricUnit(1.5, 2.0, "shines"),
            ), "unit")
            return self._match(row)

        def translation(title, *_args):
            calls.append((title, "netease"))
            return self._match(TimedLyricLine(1.5, title + " shines\n翻译：继续闪耀"), "netease")

        self.store._resolve_qq = units
        self.store._resolve_netease = translation
        self.store._resolve_lrclib = lambda *_args: None
        for index in range(10):
            title = f"Song{index}"
            result = self.store.resolve(title, "Artist")
            self.assertTrue(self.store.has_secondary_companion(result.timed_lyrics))
            self.assertEqual(result.timed_lyrics[0].time_seconds, 1.0)
            self.assertEqual(len(result.timed_lyrics[0].units), 2)
            self.assertIn("继续闪耀", "\n".join(row.text for row in result.timed_lyrics))

        self.assertEqual(calls[:4], [("Song0", "netease"), ("Song0", "qq"), ("Song1", "qq"), ("Song1", "netease")])
        self.assertLessEqual(len(self.store._resolved_cache), 3)
        self.assertNotIn(self.store._cache_key("Song0", "Artist", "", 0.0), self.store._resolved_cache)
        restored = self.store.resolve_cached_or_local("Song0", "Artist")
        self.assertTrue(self.store.has_secondary_companion(restored.timed_lyrics))
        self.assertEqual(restored.timed_lyrics[0].time_seconds, 1.0)
        self.assertEqual(len(restored.timed_lyrics[0].units), 2)
        self.assertEqual(len(calls), 20)

    def test_generated_cache_provenance_lru_follows_active_song_hits(self):
        self.store.RESOLVED_CACHE_LIMIT = 3
        for title in ("A", "B", "C", "D"):
            row = TimedLyricLine(1.0, "Shine on")
            self.store._persist_match(title, "Artist", "", self._match(row))
        for title in ("A", "B", "C"):
            self.store.resolve_cached_or_local(title, "Artist")
        self.store.resolve_cached_or_local("A", "Artist")
        self.store.resolve_cached_or_local("D", "Artist")
        active_key = self.store._cache_key("A", "Artist", "", 0.0)
        self.assertIn(active_key, self.store._resolved_cache)
        self.assertIn(active_key, self.store._resolved_local_paths)
        self.assertEqual(set(self.store._resolved_local_paths), set(self.store._resolved_cache))
        with patch("api.lyrics.threading.Thread"):
            self.assertTrue(self.store.enrich_in_background("A", "Artist", unit_timing_only=True))

    def test_failed_supplement_retry_budgets_stay_bounded_across_songs(self):
        self.store.ENRICH_ATTEMPT_LIMIT = 3
        for index in range(10):
            title = f"Song{index}"
            key = self.store._cache_key(title, "Artist", "", 0.0)
            self.store._store_cache(key, self._match(self.original))
            with patch("api.lyrics.time.monotonic", return_value=100.0 + index), patch("api.lyrics.threading.Thread"):
                self.assertTrue(self.store.enrich_in_background(title, "Artist"))
            with patch.object(self.store, "_resolve_online", return_value=LyricsMatch((), (), "")):
                self.store._enrich_background(key, title, "Artist", "", 0.0)
        self.assertLessEqual(len(self.store._enrich_attempted_at), 3)
        self.assertEqual(set(self.store._enrich_retry_seconds), set(self.store._enrich_attempted_at))
        self.assertFalse(self.store._enrich_pending_keys)
        self.assertFalse(self.store._missed_at)

    def test_companion_evidence_matches_display_boundary_for_labels_asides_and_roles(self):
        from local.music_lyric_presentation import split_display_layers

        cases = (
            ("Shine on\n(shine)", False),
            ("Shine on\nSinger: keep shining", False),
            ("Shine on\n编曲：Somebody", False),
            ("Shine on\n继续闪耀", True),
            ("你好\n粤语音译：nei5 hou2", True),
            ("你好\n译文：问候你", True),
            ("こんにちは\n你好", True),
            ("你好\n今天天气不错", False),
            ("Shine on\n括号词：(shine)", False),
            ("Shine on\n翻译：", False),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                rows = (TimedLyricLine(1.0, text),)
                self.assertEqual(self.store.has_secondary_companion(rows), expected)
                layers = split_display_layers(text)
                self.assertEqual(bool(layers.translation_text or layers.parenthetical_translation_text), expected)

    def test_provider_requests_translation_for_asides_even_when_initial_search_is_gated(self):
        from api.providers import WindowsMediaProvider

        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.lyrics_store = self.store
        provider._dual_line_lyrics_enabled = lambda: True
        aside = TimedLyricLine(1.0, "Shine on\n(shine)", self.original.units, "unit")
        with patch.object(self.store, "resolve_cached_or_local", return_value=self._match(aside)), patch.object(
            self.store, "enrich_in_background", return_value=True,
        ) as enrich:
            provider._ensure_lyrics_lookup_started("Song", "Artist")
            enrich.assert_called_once_with("Song", "Artist", "", 0.0)
            enrich.reset_mock()
            provider._ensure_lyrics_lookup_started("Song", "Artist", allow_background=False)
            enrich.assert_called_once_with("Song", "Artist", "", 0.0)

        translated = TimedLyricLine(1.0, "Shine on\n继续闪耀", self.original.units, "unit")
        with patch.object(self.store, "resolve_cached_or_local", return_value=self._match(translated)), patch.object(
            self.store, "enrich_in_background", return_value=True,
        ) as enrich:
            provider._ensure_lyrics_lookup_started("Song", "Artist")
            enrich.assert_not_called()

    def test_actual_provider_caller_keeps_translation_retry_after_same_song_toggle(self):
        from tests.test_provider_lyric_anchor_guard import ProviderLyricAnchorGuardTests

        fixture = ProviderLyricAnchorGuardTests()
        provider, _session, _reader, _fake_store = fixture._provider()
        provider.lyrics_store = self.store
        provider._dual_line_lyrics_enabled = self.store._dual_line_lyrics_enabled
        self.store._persist_match("Song", "Artist", "", self._match(self.original), duration_seconds=180.0)
        self.settings["v2_enable_dual_line_lyrics"] = False
        first = fixture._read(provider, 100.0)
        self.assertEqual(first.track.timed_lyrics[0].text, "Shine on")

        self.settings["v2_enable_dual_line_lyrics"] = True
        with patch("api.lyrics.threading.Thread") as thread:
            second = fixture._read(provider, 100.6)
            fixture._read(provider, 100.8)
            self.assertEqual(thread.call_count, 1)
            self.assertEqual(second.track.track_id, first.track.track_id)
            key = self.store._cache_key("Song", "Artist", "", 180.0)
            with patch("api.lyrics.time.monotonic", return_value=100.6), patch.object(
                self.store, "_resolve_online", return_value=LyricsMatch((), (), ""),
            ):
                self.store._enrich_background(key, "Song", "Artist", "", 180.0)
            fixture._read(provider, 110.0)
            self.assertEqual(thread.call_count, 1)
            fixture._read(provider, 146.0)
            self.assertEqual(thread.call_count, 2)

            translated = self._match(TimedLyricLine(1.5, "Shine on\n继续闪耀"), "netease")
            with patch.object(self.store, "_resolve_online", return_value=translated):
                self.store._enrich_background(key, "Song", "Artist", "", 180.0)
            final = fixture._read(provider, 146.6)
            self.assertEqual(final.track.timed_lyrics[0].text, "Shine on\n继续闪耀")
            self.assertEqual(final.track.timed_lyrics[0].units, self.original.units)
            self.assertEqual(final.track.track_id, first.track.track_id)
            self.assertEqual(thread.call_count, 2)

    def test_actual_provider_caller_retries_generated_unit_upgrade_while_retaining_song(self):
        from tests.test_provider_lyric_anchor_guard import ProviderLyricAnchorGuardTests

        fixture = ProviderLyricAnchorGuardTests()
        provider, _session, _reader, _fake_store = fixture._provider()
        provider.lyrics_store = self.store
        provider._dual_line_lyrics_enabled = self.store._dual_line_lyrics_enabled
        self.settings["v2_enable_dual_line_lyrics"] = False
        self.store._persist_match("Song", "Artist", "", self._match(TimedLyricLine(1.0, "Shine on")), duration_seconds=180.0)
        with patch("api.lyrics.threading.Thread") as thread:
            first = fixture._read(provider, 100.0)
            self.assertEqual(thread.call_count, 1)
            key = self.store._cache_key("Song", "Artist", "", 180.0)
            with patch.object(self.store, "_resolve_online", return_value=LyricsMatch((), (), "")):
                self.store._enrich_background(key, "Song", "Artist", "", 180.0, True)
            fixture._read(provider, 100.6)
            self.assertEqual(thread.call_count, 1)
            second = fixture._read(provider, 1900.1)
            self.assertEqual(thread.call_count, 2)
            self.assertEqual(second.track.track_id, first.track.track_id)
            self.assertEqual(second.track.timed_lyrics, first.track.timed_lyrics)

    def test_actual_provider_caller_never_requests_online_replacement_of_manual_lyrics(self):
        from tests.test_provider_lyric_anchor_guard import ProviderLyricAnchorGuardTests

        fixture = ProviderLyricAnchorGuardTests()
        provider, _session, _reader, _fake_store = fixture._provider()
        provider.lyrics_store = self.store
        provider._dual_line_lyrics_enabled = self.store._dual_line_lyrics_enabled
        manual_path = self.store.root / "artist - song.lrc"
        manual_text = "[00:01.00]My handwritten lyric\n"
        manual_path.write_text(manual_text, encoding="utf-8")
        self.settings["v2_enable_dual_line_lyrics"] = False
        with patch("api.lyrics.threading.Thread") as thread:
            first = fixture._read(provider, 100.0)
            self.settings["v2_enable_dual_line_lyrics"] = True
            second = fixture._read(provider, 100.6)
            fixture._read(provider, 1900.1)
            thread.assert_not_called()
        self.assertEqual(first.track.timed_lyrics[0].text, "My handwritten lyric")
        self.assertEqual(second.track.timed_lyrics[0].text, "My handwritten lyric")
        self.assertEqual(manual_path.read_text(encoding="utf-8"), manual_text)

    def test_actual_provider_toggle_without_generated_file_inherits_original_and_supplements(self):
        from local.music_lyric_presentation import present_lyric_text
        from tests.test_provider_lyric_anchor_guard import ProviderLyricAnchorGuardTests

        fixture = ProviderLyricAnchorGuardTests()
        provider, _session, _reader, _fake_store = fixture._provider()
        provider.lyrics_store = self.store
        provider._dual_line_lyrics_enabled = self.store._dual_line_lyrics_enabled
        self.settings["v2_enable_dual_line_lyrics"] = False
        single_key = self.store._cache_key("Song", "Artist", "", 180.0)
        self.store._store_cache(single_key, self._match(self.original))
        first = fixture._read(provider, 100.0)
        self.assertFalse(tuple(self.store.generated_root.glob("*.lrc")))

        self.settings["v2_enable_dual_line_lyrics"] = True
        with patch("api.lyrics.threading.Thread") as thread:
            second = fixture._read(provider, 100.6)
            dual_key = self.store._cache_key("Song", "Artist", "", 180.0)
            self.assertIn(dual_key, self.store._resolved_cache)
            self.assertEqual(second.track.timed_lyrics[0].units, self.original.units)
            self.assertEqual(thread.call_count, 1)
            translated = self._match(TimedLyricLine(1.5, "Shine on\n继续闪耀"), "netease")
            with patch.object(self.store, "_resolve_online", return_value=translated):
                self.store._enrich_background(dual_key, "Song", "Artist", "", 180.0)
            shown = fixture._read(provider, 101.2)
            self.assertEqual(shown.track.timed_lyrics[0].text, "Shine on\n继续闪耀")
            self.assertEqual(shown.track.timed_lyrics[0].units, self.original.units)
            self.assertEqual(shown.track.timed_lyrics[0].time_seconds, 1.0)
            self.assertEqual(shown.track.track_id, first.track.track_id)
            self.settings["v2_enable_dual_line_lyrics"] = False
            hidden = fixture._read(provider, 101.8)
            self.assertEqual(present_lyric_text(hidden.track.timed_lyrics[0].text, show_translation=False), "Shine on")
            self.assertEqual(hidden.track.timed_lyrics[0].units, self.original.units)
            self.assertEqual(thread.call_count, 1)

    def test_pruned_generated_sibling_retains_path_provenance_without_blocking_supplement(self):
        self.settings["v2_enable_dual_line_lyrics"] = False
        self.store._persist_match("Song", "Artist", "", self._match(self.original))
        known = self.store.resolve_cached_or_local("Song", "Artist")
        single_key = self.store._cache_key("Song", "Artist", "", 0.0)
        original_path = self.store._resolved_local_paths[single_key]
        original_path.unlink()
        self.settings["v2_enable_dual_line_lyrics"] = True
        inherited = self.store.resolve_cached_or_local("Song", "Artist")
        dual_key = self.store._cache_key("Song", "Artist", "", 0.0)
        self.assertEqual(self.store._resolved_local_paths[dual_key], original_path)
        self.assertEqual(inherited.source, known.source)
        self.assertEqual(inherited.timed_lyrics[0].units, self.original.units)
        with patch("api.lyrics.threading.Thread"):
            self.assertTrue(self.store.enrich_in_background("Song", "Artist"))
        with patch.object(self.store, "_resolve_online", return_value=self._match(
            TimedLyricLine(1.5, "Shine on\n继续闪耀"), "netease",
        )):
            self.store._enrich_background(dual_key, "Song", "Artist", "", 0.0)
        self.assertEqual(self.store._resolved_cache[dual_key].timed_lyrics[0].text, "Shine on\n继续闪耀")
        self.assertEqual(self.store._resolved_cache[dual_key].timed_lyrics[0].units, self.original.units)

    def test_new_manual_file_precedes_opposite_translation_partition(self):
        self.settings["v2_enable_dual_line_lyrics"] = False
        self.store._store_cache(self.store._cache_key("Song", "Artist", "", 0.0), self._match(self.original))
        manual_path = self.store.root / "artist - song.lrc"
        manual_text = "[00:01.00]My new handwritten lyric\n"
        manual_path.write_text(manual_text, encoding="utf-8")
        self.settings["v2_enable_dual_line_lyrics"] = True
        selected = self.store.resolve_cached_or_local("Song", "Artist")
        self.assertEqual(selected.timed_lyrics[0].text, "My new handwritten lyric")
        with patch("api.lyrics.threading.Thread") as thread:
            self.assertFalse(self.store.enrich_in_background("Song", "Artist"))
            thread.assert_not_called()
        self.assertEqual(manual_path.read_text(encoding="utf-8"), manual_text)

    def test_preference_sibling_fallback_only_changes_dual_switch_and_stays_bounded(self):
        self.settings["v2_enable_dual_line_lyrics"] = False
        self.store._store_cache(self.store._cache_key("Song", "Artist", "", 0.0), self._match(self.original))
        self.settings["v2_enable_dual_line_lyrics"] = True
        for setting, value in (
            ("v2_lyric_chinese_conversion", "traditional"),
            ("v2_dual_line_secondary_source", "transliteration"),
            ("v2_cantonese_secondary_priority", "translation_first"),
        ):
            with self.subTest(setting=setting):
                self.settings[setting] = value
                self.assertFalse(self.store.resolve_cached_or_local("Song", "Artist").timed_lyrics)
                self.settings.pop(setting)
        self.assertFalse(self.store.resolve_cached_or_local("Different Song", "Artist").timed_lyrics)
        self.assertFalse(self.store.resolve_cached_or_local("Song", "Different Artist").timed_lyrics)
        self.store.RESOLVED_CACHE_LIMIT = 1
        copied = self.store.resolve_cached_or_local("Song", "Artist")
        self.assertEqual(copied.timed_lyrics[0].units, self.original.units)
        for enabled in (False, True, False, True):
            self.settings["v2_enable_dual_line_lyrics"] = enabled
            self.assertEqual(self.store.resolve_cached_or_local("Song", "Artist").timed_lyrics[0].units, self.original.units)
            self.assertEqual(len(self.store._resolved_cache), 1)
            self.assertLessEqual(len(self.store._resolved_local_paths), 1)

    def test_manual_file_reappearing_during_missing_disk_supplement_still_wins(self):
        self.settings["v2_enable_dual_line_lyrics"] = False
        self.store._persist_match("Song", "Artist", "", self._match(self.original))
        self.store.resolve_cached_or_local("Song", "Artist")
        generated_path = next(self.store.generated_root.glob("*.lrc"))
        generated_path.unlink()
        self.settings["v2_enable_dual_line_lyrics"] = True
        self.store.resolve_cached_or_local("Song", "Artist")
        key = self.store._cache_key("Song", "Artist", "", 0.0)
        manual_path = self.store.root / "artist - song.lrc"
        manual_text = "[00:01.00]My restored handwritten lyric\n"

        def supplement(*_args):
            manual_path.write_text(manual_text, encoding="utf-8")
            return self._match(TimedLyricLine(1.5, "Shine on\n继续闪耀"), "netease")

        with patch.object(self.store, "_resolve_online", side_effect=supplement):
            self.store._enrich_background(key, "Song", "Artist", "", 0.0)
        selected = self.store.resolve_cached_or_local("Song", "Artist")
        self.assertEqual(selected.timed_lyrics[0].text, "My restored handwritten lyric")
        self.assertEqual(manual_path.read_text(encoding="utf-8"), manual_text)
        self.assertFalse(generated_path.exists())

    def test_cantonese_sibling_keeps_raw_candidates_and_timing_without_generated_file(self):
        self.settings["v2_enable_dual_line_lyrics"] = False
        row = TimedLyricLine(1.0, "你好\n粤语音译：nei5 hou2\n粤语译文：问候你", (
            TimedLyricUnit(1.0, 1.5, "你"), TimedLyricUnit(1.5, 2.0, "好"),
        ), "unit")
        known = self.store._finalize_lyrics_match(self._match(row, "netease"), "yue")
        self.store._store_cache(self.store._cache_key("Song", "Artist", "", 0.0), known)
        self.settings["v2_enable_dual_line_lyrics"] = True
        selected = self.store.resolve_cached_or_local("Song", "Artist")
        self.assertEqual(selected.secondary_source_rows, known.secondary_source_rows)
        self.assertEqual(selected.source, known.source)
        self.assertEqual(selected.timed_lyrics[0].text, "你好\n粤语音译：nei5 hou2")
        self.assertEqual(selected.timed_lyrics[0].units, row.units)
        self.assertEqual(selected.timed_lyrics[0].timing_kind, "unit")


if __name__ == "__main__":
    unittest.main()
