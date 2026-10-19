from __future__ import annotations

import base64
import json
from pathlib import Path
from dataclasses import replace
import tempfile
import threading
import unittest
from unittest.mock import patch

from api.lyrics import LocalLyricsStore, LyricsMatch
from api.models import TimedLyricLine, TimedLyricUnit
from tests.test_lyric_source_formats import QRC_CIPHER, QRC_TEXT, document


TTML = document('<p begin="1"><span begin="1" end="1.4">Hel</span>'
    '<span begin="1.4" end="2">lo</span>'
    '<span ttm:role="x-translation">你好</span><span ttm:role="x-roman">ni hao</span></p>'
    '<p begin="6"><span begin="6" end="7">there</span></p>', extra='dur="7"')


class LyricSourceExpansionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.preferences = {"v2_enable_dual_line_lyrics": False}
        self.store = LocalLyricsStore(self.root / "manual", generated_root=self.root / "cache",
            settings_getter=lambda key, default=None: self.preferences.get(key, default))

    def payload(self, **changes):
        return {"status": 200, "data": {"format": "ttml", "ncmMusicIds": ["123"],
            "qqMusicIds": ["mid123"], "musicNames": ["Hello"], "artistNames": ["Singer"],
            "lyrics": TTML, **changes}}

    def trusted(self, source="netease", identity="123"):
        return self.store.resolve_trusted_lyrics(source, identity, "Hello", "Singer", "Album", 180.0)

    def test_exact_id_and_metadata_preserve_true_timing_and_short_document_extent(self):
        with patch.object(self.store, "_fetch_public_json", return_value=self.payload()) as fetch:
            result = self.trusted()
        self.assertEqual(result.source, "amll:netease:123")
        self.assertEqual(result.timed_lyrics[0].units[1].start_seconds, 1.4)
        self.assertEqual(dict(result.raw_sources)["raw_ttml"], TTML)
        self.assertEqual(fetch.call_count, 1)
        self.assertIn("ncmMusicId=123", fetch.call_args.args[0])

    def test_qq_mid_uses_exact_id_mapping_without_title_search(self):
        with patch.object(self.store, "_fetch_public_json", return_value=self.payload()) as fetch:
            self.assertIsNotNone(self.trusted("qq", "mid123"))
        self.assertIn("qqMusicId=mid123", fetch.call_args.args[0])

    def test_cross_song_version_artist_id_and_duration_conflicts_are_rejected(self):
        for changes in ({"musicNames": ["Different"]}, {"artistNames": ["Another performer"]},
                {"ncmMusicIds": ["456"]}, {"musicNames": ["Hello (Live)"]},
                {"musicNames": ["Hello Cover"]}, {"musicNames": ["Hello Acoustic"]},
                {"musicNames": ["Hello Remastered"]}, {"musicNames": ["Hello Sped Up"]},
                {"musicNames": ["Hello Slowed"]},
                {"duration": 220}, {"format": "lrc"}, {"lyrics": "unrelated JSON"},
                {"lyrics": document('<p begin="200">late</p>')}):
            with self.subTest(changes=changes), patch.object(self.store, "_fetch_public_json", return_value=self.payload(**changes)):
                self.store._trusted_results.clear()
                self.assertIsNone(self.trusted())

    def test_trusted_id_cannot_bypass_a_conflicting_specific_recording_version(self):
        title = "An Authored Song With A Long Name"
        for requested, supplied in (
            (title, title + " (Radio Edit)"),
            (title, title + " (Extended Mix)"),
            (title + " (A Remix)", title + " (B Remix)"),
        ):
            with self.subTest(requested=requested, supplied=supplied), patch.object(
                self.store, "_fetch_public_json", return_value=self.payload(musicNames=[supplied]),
            ):
                self.store._trusted_results.clear()
                self.assertIsNone(self.store.resolve_trusted_lyrics(
                    "netease", "123", requested, "Singer", "Album", 180.,
                ))

    def test_miss_rate_limit_timeout_and_invalid_payload_are_bounded_and_negatively_cached(self):
        for payload in (None, {}, {"status": 404}, {"status": 429}, {"status": 200, "data": []}):
            with self.subTest(payload=payload), patch.object(self.store, "_fetch_public_json", return_value=payload) as fetch:
                self.store._trusted_results.clear()
                self.assertIsNone(self.trusted())
                self.assertIsNone(self.trusted())
                self.assertEqual(fetch.call_count, 1)

    def test_translation_and_romanization_preferences_reparse_retained_raw_without_network(self):
        with patch.object(self.store, "_fetch_public_json", return_value=self.payload()) as fetch:
            result = self.trusted()
            self.assertNotIn("你好", result.lyrics[0])
            self.preferences["v2_enable_dual_line_lyrics"] = True
            translated = self.trusted()
            self.assertIn("你好", translated.lyrics[0])
            self.preferences["v2_dual_line_secondary_source"] = "transliteration"
            romanized = self.store.reparse_source_match(result)
            self.assertIn("ni hao", romanized.lyrics[0])
            self.assertNotIn("你好", romanized.lyrics[0])
            self.assertEqual(fetch.call_count, 1)

    def test_raw_payload_and_provenance_survive_generated_cache_without_display_markers(self):
        match = self.store.parse_online_lyrics(raw_ttml=TTML, source="amll:netease:123")
        self.store._persist_match("Hello", "Singer", "Album", match)
        path = next((self.root / "cache").glob("*.lrc"))
        content = path.read_text(encoding="utf-8")
        self.assertNotIn("· · ·", content)
        self.preferences["v2_enable_dual_line_lyrics"] = True
        restored = self.store._read_lrc_match(path)
        self.assertEqual(restored.source, match.source)
        self.assertEqual(restored.raw_sources, match.raw_sources)
        self.assertIn("你好", restored.lyrics[0])
        self.assertEqual(restored.timed_lyrics[0].units, match.timed_lyrics[0].units)
        self.assertTrue(self.store._is_generated_online_cache(path))

    def test_ttml_timed_companions_cannot_replace_or_invent_original_vocals(self):
        self.preferences["v2_enable_dual_line_lyrics"] = True
        raw = document('<p begin="1">original</p>'
            '<p begin="1" ttm:role="x-translation"><span begin="1" end="8">译文</span></p>'
            '<p begin="9" ttm:role="x-translation"><span begin="9" end="12">无对应译文</span></p>')
        match = self.store.parse_online_lyrics(raw_ttml=raw, source="amll:qq:mid123")
        self.assertEqual(match.lyrics, ("original\n译文",))
        self.assertFalse(match.timed_lyrics[0].units)
        self.assertEqual(match.timed_lyrics[0].timing_kind, "line")
        self.assertEqual(dict(match.raw_sources)["raw_ttml"], raw)

    def test_netease_raw_cache_retains_both_companion_clocks_and_preference_choices(self):
        search = {"result": {"songs": [{"id": 123, "name": "Hello", "ar": [{"name": "Singer"}],
            "al": {"name": "Album"}, "dt": 180000}]}}
        payload = {"yrc": {"lyric": "[1500,1000](1500,1000,0)Hello"},
            "lrc": {"lyric": "[00:01]Hello"}, "tlyric": {"lyric": "[00:01]旧时钟译文"},
            "ytlrc": {"lyric": "[00:01.5]准确译文"}, "romalrc": {"lyric": "[00:01]old roman"},
            "yromalrc": {"lyric": "[00:01.5]accurate roman"}}
        self.preferences["v2_enable_dual_line_lyrics"] = True
        with patch.object(self.store, "_fetch_json", side_effect=[search, payload]):
            match = self.store._resolve_netease("Hello", "Singer", "Album", 180)
        self.assertEqual(match.lyrics, ("Hello\n准确译文",))
        raw = json.loads(dict(match.raw_sources)["raw_netease"])
        self.assertEqual(raw["tlyric"], payload["tlyric"])
        self.assertEqual(raw["ytlrc"], payload["ytlrc"])
        self.store._persist_match("Hello", "Singer", "Album", match)
        path = next((self.root / "cache").glob("*.lrc"))
        restored = self.store._read_lrc_match(path)
        self.assertEqual(restored.lyrics, match.lyrics)
        self.assertEqual(restored.raw_sources, match.raw_sources)
        self.preferences["v2_dual_line_secondary_source"] = "transliteration"
        romanized = self.store._read_lrc_match(path)
        self.assertEqual(romanized.lyrics, ("Hello\naccurate roman",))
        self.assertEqual(romanized.timed_lyrics[0].units, match.timed_lyrics[0].units)

    def test_generated_account_sources_keep_cache_ownership(self):
        for source in ("netease-account", "qq-account"):
            with self.subTest(source=source):
                match = self.store.parse_online_lyrics(raw_lrc="[00:01]original", source=source)
                self.store._persist_match(source, "Singer", "", match)
                path = next((self.root / "cache").glob(f"*{source}*.lrc"))
                self.assertTrue(self.store._is_generated_online_cache(path))

    def test_account_raw_variants_reparse_through_existing_netease_extractor(self):
        raw = json.dumps({"lrc": {"lyric": "[00:01]Hello"},
            "yrc": {"lyric": "[1500,1000](1500,1000,0)Hello"},
            "tlyric": {"lyric": "[00:01]旧译文"}, "ytlrc": {"lyric": "[00:01.5]准确译文"},
            "romalrc": {"lyric": "[00:01]old roman"}, "yromalrc": {"lyric": "[00:01.5]accurate roman"}})
        self.preferences["v2_enable_dual_line_lyrics"] = True
        translated = self.store.parse_online_lyrics(raw_netease=raw, source="netease-account")
        self.assertEqual(translated.lyrics, ("Hello\n准确译文",))
        self.preferences["v2_dual_line_secondary_source"] = "transliteration"
        romanized = self.store.reparse_source_match(translated)
        self.assertEqual(romanized.lyrics, ("Hello\naccurate roman",))
        self.assertEqual(romanized.timed_lyrics[0].units, translated.timed_lyrics[0].units)
        self.assertEqual(dict(romanized.raw_sources)["raw_netease"], raw)

    def test_manual_file_does_not_decode_a_similar_raw_header_or_lose_priority(self):
        fake = base64.b64encode(json.dumps({"version": 1, "fields": {"raw_ttml": TTML}}).encode()).decode()
        manual = self.root / "manual" / "Singer - Hello.lrc"
        manual.write_text(f"[gluspot_raw:{fake}]\n[00:01]hand authored\n", encoding="utf-8")
        result = self.store.resolve_cached_or_local("Hello", "Singer", "Album", 180)
        self.assertEqual(result.lyrics, ("hand authored",))
        self.assertFalse(result.raw_sources)
        with patch.object(self.store, "_fetch_public_json") as fetch:
            self.assertFalse(self.store.enrich_in_background("Hello", "Singer", "Album", 180))
            fetch.assert_not_called()

    def test_cache_clear_fences_pending_trusted_result(self):
        def clear_during_request(*_args):
            self.store.clear_generated_cache()
            return self.payload()
        with patch.object(self.store, "_fetch_public_json", side_effect=clear_during_request):
            self.assertIsNone(self.trusted())
        self.assertFalse(self.store._trusted_results)

    def test_compatible_upgrade_requires_same_words_and_close_clock(self):
        base = LyricsMatch((TimedLyricLine(1, "Hello"), TimedLyricLine(6, "there")), ("Hello", "there"), "qq")
        candidate = self.store.parse_online_lyrics(raw_ttml=TTML, source="amll:qq:mid123")
        self.assertTrue(self.store.is_compatible_lyric_upgrade(base, candidate))
        self.assertTrue(self.store.is_compatible_lyric_upgrade(None, candidate))
        self.assertFalse(self.store.is_compatible_lyric_upgrade(candidate, candidate))
        wrong_clock = LyricsMatch((TimedLyricLine(5, "Hello"), TimedLyricLine(10, "there")), (), "qq")
        self.assertFalse(self.store.is_compatible_lyric_upgrade(wrong_clock, candidate))
        self.assertFalse(self.store.is_compatible_lyric_upgrade(LyricsMatch((TimedLyricLine(1, "Hello live"),), (), "qq"), candidate))

    def test_multisource_failure_keeps_early_line_fallback(self):
        base = LyricsMatch((TimedLyricLine(1, "Hello"),), ("Hello",), "netease")
        self.store._resolve_netease = lambda *_args: base
        self.store._resolve_qq = lambda *_args: None
        self.store._resolve_lrclib = lambda *_args: None
        self.store._resolve_amll = lambda *_args: None
        early = []
        result = self.store._resolve_online("Hello", "Singer", "Album", 180, early.append)
        self.assertEqual(result, base)
        self.assertEqual(early, [base])

    def test_incompatible_amll_never_replaces_usable_lrc(self):
        base = LyricsMatch((TimedLyricLine(30, "Other recording"),), ("Other recording",), "netease")
        self.store._resolve_netease = lambda *_args: base
        self.store._resolve_qq = lambda *_args: None
        self.store._resolve_lrclib = lambda *_args: None
        self.store._resolve_amll = lambda *_args: self.store.parse_online_lyrics(raw_ttml=TTML, source="amll:netease:123")
        self.assertEqual(self.store._resolve_online("Hello", "Singer", "Album", 180), base)

    def test_cross_source_companions_survive_reparse_and_disk_without_version_mixing(self):
        self.preferences["v2_enable_dual_line_lyrics"] = True
        raw = document('<p begin="1"><span begin="1" end="2">Hello</span></p>'
            '<p begin="6"><span begin="6" end="7">there</span></p>')
        candidate = self.store.parse_online_lyrics(raw_ttml=raw, source="amll:netease:123")
        official = self.store.parse_online_lyrics(raw_lrc="[00:01]Hello\n[00:06]there",
            translation="[00:01]你好\n[00:06]那里", romanization="[00:01]ni hao\n[00:06]na li", source="qq")
        merged = self.store._retain_matching_subrows(candidate, official)
        self.assertIn("compatible_previous", dict(merged.raw_sources))
        self.assertEqual(merged.lyrics, ("Hello\n你好", "there\n那里"))
        self.store._persist_match("Hello", "Singer", "", merged)
        path = next((self.root / "cache").glob("*.lrc"))
        self.assertEqual(self.store._read_lrc_match(path).lyrics, merged.lyrics)
        self.preferences["v2_enable_dual_line_lyrics"] = False
        self.assertEqual(self.store.reparse_source_match(merged).lyrics, ("Hello", "there"))
        self.preferences["v2_enable_dual_line_lyrics"] = True
        self.preferences["v2_dual_line_secondary_source"] = "transliteration"
        romanized = self.store.reparse_source_match(merged)
        self.assertEqual(romanized.lyrics, ("Hello\nni hao", "there\nna li"))
        incompatible = replace(official, timed_lyrics=tuple(
            replace(row, time_seconds=row.time_seconds + 20) for row in official.timed_lyrics))
        # A source with retained raw text must be rechecked on that raw clock.
        wrong = self.store._serialize_compatible_previous(replace(incompatible, raw_sources=()))
        forged = replace(candidate, raw_sources=candidate.raw_sources + (("compatible_previous", wrong),))
        self.assertEqual(self.store.reparse_source_match(forged).lyrics, candidate.lyrics)

    def test_all_cross_source_companion_merges_require_compatible_clocks(self):
        old = LyricsMatch((TimedLyricLine(1, "Hello\n你好"), TimedLyricLine(4, "there\n那里")), (), "netease")
        new = self.store.parse_online_lyrics(raw_yrc="[21000,1000](21000,1000,0)Hello\n[24000,1000](24000,1000,0)there", source="qq")
        self.assertIs(self.store._retain_matching_subrows(new, old), new)

    def test_companion_raw_history_rejects_nested_or_oversized_fields(self):
        for fields in ({"source": "qq", "compatible_previous": "{}"}, {"unknown": "text"}):
            self.assertIsNone(self.store._read_compatible_previous(json.dumps({"version": 1, "fields": fields})))
        self.assertIsNone(self.store._read_compatible_previous(" " * (2 * 1024 * 1024 + 1)))

    def test_multistage_upgrade_flattens_the_actual_imported_companion_provider(self):
        self.preferences["v2_enable_dual_line_lyrics"] = True
        raw_lrc = "[00:01]Hello\n[00:06]there"
        initial = self.store.parse_online_lyrics(raw_lrc=raw_lrc, source="netease")
        translated = self.store.parse_online_lyrics(raw_lrc=raw_lrc,
            translation="[00:01]你好\n[00:06]那里", romanization="[00:01]ni hao\n[00:06]na li", source="qq")
        combined = self.store._retain_matching_subrows(initial, translated)
        rich = self.store.parse_online_lyrics(raw_yrc="[1200,1000](1200,1000,0)Hello\n[6200,1000](6200,1000,0)there", source="netease")
        upgraded = self.store._retain_matching_subrows(rich, combined)
        self.assertEqual(upgraded.lyrics, ("Hello\n你好", "there\n那里"))
        envelope = json.loads(dict(upgraded.raw_sources)["compatible_previous"])
        self.assertEqual(envelope["fields"]["source"], "qq")
        self.assertNotIn("compatible_previous", envelope["fields"])
        self.assertEqual(self.store.reparse_source_match(upgraded).lyrics, upgraded.lyrics)
        self.store._persist_match("Hello", "Singer", "", upgraded)
        path = next((self.root / "cache").glob("*.lrc"))
        self.assertEqual(self.store._read_lrc_match(path).lyrics, upgraded.lyrics)
        self.preferences["v2_dual_line_secondary_source"] = "transliteration"
        self.assertEqual(self.store._read_lrc_match(path).lyrics, ("Hello\nni hao", "there\nna li"))

    def test_imported_raw_source_row_bound_is_rechecked_after_parsing(self):
        raw = "\n".join(f"[{index // 60:02}:{index % 60:02}]word {index}" for index in range(2001))
        value = json.dumps({"version": 1, "fields": {"raw_lrc": raw, "source": "qq"}})
        self.assertLess(len(value), 2 * 1024 * 1024)
        self.assertIsNone(self.store._read_compatible_previous(value))

    def test_qrc_parse_capability_is_used_by_real_fetch_path_and_falls_back_on_corruption(self):
        search = {"data": {"song": {"list": [{"songid": 9, "songmid": "mid9", "songname": "Hello",
            "singer": [{"name": "Singer"}], "interval": 180}]}}}
        fallback = {"lyric": "[00:01]fallback", "trans": "[00:01]译文"}
        for encrypted, expected in ((QRC_CIPHER, "你好"), ("corrupt", "fallback")):
            with self.subTest(encrypted=encrypted[:10]), patch.object(self.store, "_fetch_json", side_effect=[search, fallback]), patch.object(
                    self.store, "_fetch_public_json", return_value={"code": 0, "request": {"code": 0, "data": {"lyric": encrypted}}}) as fetch:
                result = self.store._resolve_qq("Hello", "Singer", "", 180)
                self.assertEqual(result.timed_lyrics[0].text, expected)
                self.assertEqual(fetch.call_args.args[1]["request"]["param"]["qrc"], 1)
                self.assertEqual(fetch.call_args.args[1]["request"]["param"]["songID"], 9)
        result = self.store.parse_online_lyrics(raw_qrc=QRC_TEXT, source="qq")
        self.assertEqual(result.timed_lyrics[0].units[-1].end_seconds, 4.0)


if __name__ == "__main__":
    unittest.main()
