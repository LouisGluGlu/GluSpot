from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.cantonese import has_cantonese_evidence, is_jyutping
from api.lyrics import LocalLyricsStore, LyricsMatch
from api.models import TimedLyricLine, TimedLyricUnit


class CantoneseRecoveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.settings = {
            "v2_enable_dual_line_lyrics": True,
            "v2_cantonese_secondary_priority": "phonetic_first",
        }

    def _store(self):
        return LocalLyricsStore(
            self.base / "local",
            generated_root=self.base / "generated",
            settings_getter=lambda key, default=None: self.settings.get(key, default),
        )

    def _unit_match(self, store):
        return store._lyrics_match_from_text(
            "[1000,2000](1000,1000,0)你(2000,1000,0)好", "qq",
        )

    def test_numbered_mandarin_pinyin_is_not_cantonese_evidence(self):
        self.assertFalse(is_jyutping("ni3 hao3"))
        self.assertFalse(has_cantonese_evidence("[00:01.00]ni3 hao3"))
        self.assertFalse(has_cantonese_evidence("音译：ni3 hao3"))
        self.assertFalse(has_cantonese_evidence("romanization: ni3 hao3"))
        self.assertFalse(has_cantonese_evidence("你好 ni3 hao3"))
        self.assertTrue(is_jyutping("nei5 hou2"))
        self.assertTrue(has_cantonese_evidence("你好", "[language:yue]"))

        store = self._store()
        payload = {
            "lrc": {"lyric": "[00:01.00]你好"},
            "romalrc": {"lyric": "[00:01.00]ni3 hao3"},
            "tlyric": {"lyric": "[00:01.00]Hello"},
        }
        match = store._lyrics_match_from_text(
            store._extract_netease_lyric_text(payload, prefer_secondary=True), "netease",
        )
        self.assertNotIn("粤语音译", match.timed_lyrics[0].text)
        self.assertFalse(match.secondary_source_rows)

    def test_generated_only_phonetics_keep_language_evidence_during_unit_upgrade(self):
        store = self._store()
        previous = store._lyrics_match_from_text(
            "[language:yue]\n[00:01.00]你好", "netease",
        )
        self.assertIn("粤语音译：nei5 hou2", previous.timed_lyrics[0].text)
        current = self._unit_match(store)

        retained = store._retain_matching_subrows(current, previous)

        self.assertEqual(retained.timed_lyrics[0].text, "你好\n粤语音译：nei5 hou2")
        self.assertEqual(retained.timed_lyrics[0].units, current.timed_lyrics[0].units)
        self.assertEqual(retained.timed_lyrics[0].time_seconds, 1.0)
        self.assertTrue(retained.secondary_source_rows)
        self.assertIn("[language:yue]", store._render_lrc("Song", "Artist", "", retained))

    def test_disk_reload_enrichment_keeps_both_candidates_for_priority_switch(self):
        store = self._store()
        original = store._lyrics_match_from_text((
            "[language:yue]\n[00:01.00]你好",
            "[00:01.00]粤语音译：nei5 hou2",
            "[00:01.00]粤语译文：问候你",
        ), "netease")
        store._persist_match("Song", "Artist", "", original)
        cache_path = next(store.generated_root.glob("*.lrc"))
        self.assertIn("问候你", cache_path.read_text(encoding="utf-8"))

        reloaded = self._store()
        selected = reloaded.resolve_cached_or_local("Song", "Artist")
        self.assertEqual(selected.timed_lyrics[0].text, "你好\n粤语音译：nei5 hou2")
        current = self._unit_match(reloaded)
        key = reloaded._cache_key("Song", "Artist", "", 0.0)
        # Run the real background worker synchronously so the test exercises
        # its cache publication and disk overwrite without thread timing.
        with patch.object(reloaded, "_resolve_online", return_value=current) as resolve_online:
            reloaded._enrich_background(key, "Song", "Artist", "", 0.0, True)
            self.assertEqual(reloaded._resolved_cache[key].timed_lyrics[0].units, current.timed_lyrics[0].units)
            persisted = cache_path.read_text(encoding="utf-8")
            self.assertIn("粤语音译：nei5 hou2", persisted)
            self.assertIn("粤语译文：问候你", persisted)

            self.settings["v2_cantonese_secondary_priority"] = "translation_first"
            translated = reloaded.resolve_cached_or_local("Song", "Artist")
            self.assertEqual(translated.timed_lyrics[0].text, "你好\n粤语译文：问候你")
            self.assertEqual(translated.timed_lyrics[0].units, current.timed_lyrics[0].units)
            resolve_online.assert_called_once()

        self.settings["v2_cantonese_secondary_priority"] = "phonetic_first"
        final_reload = self._store().resolve_cached_or_local("Song", "Artist")
        self.assertEqual(final_reload.timed_lyrics[0].text, "你好\n粤语音译：nei5 hou2")
        self.assertEqual(final_reload.timed_lyrics[0].units, current.timed_lyrics[0].units)

    def test_same_time_singers_keep_their_own_candidates_during_unit_upgrade(self):
        self.settings["v2_cantonese_secondary_priority"] = "translation_first"
        store = self._store()
        first_units = (
            TimedLyricUnit(1.0, 1.5, "男："),
            TimedLyricUnit(1.5, 2.0, "你"),
            TimedLyricUnit(2.0, 2.5, "好"),
        )
        second_units = (
            TimedLyricUnit(1.0, 1.6, "女："),
            TimedLyricUnit(1.6, 2.3, "再"),
            TimedLyricUnit(2.3, 3.0, "见"),
        )
        old_rows = (
            TimedLyricLine(1.0, "男：你好\n粤语译文：Hello", first_units, "unit"),
            TimedLyricLine(1.0, "女：再见\n粤语译文：Bye", second_units, "unit"),
        )
        previous = store._finalize_lyrics_match(
            LyricsMatch(old_rows, tuple(row.text for row in old_rows), "netease"), "yue",
        )
        new_rows = (
            TimedLyricLine(1.0, "男：你好", first_units, "unit"),
            TimedLyricLine(1.0, "女：再见", second_units, "unit"),
        )
        current = LyricsMatch(new_rows, tuple(row.text for row in new_rows), "qq")

        retained = store._retain_matching_subrows(current, previous)

        self.assertEqual(tuple(row.text for row in retained.timed_lyrics), (
            "男：你好\n粤语译文：Hello", "女：再见\n粤语译文：Bye",
        ))
        self.assertEqual(tuple(row.units for row in retained.timed_lyrics), (first_units, second_units))
        self.assertEqual(tuple(row.time_seconds for row in retained.timed_lyrics), (1.0, 1.0))

    def test_plain_explicit_language_metadata_generates_phonetics(self):
        store = self._store()
        for text in ("[language:yue]\n你好", ("[language:yue]\n你好",)):
            with self.subTest(source_type=type(text).__name__):
                match = store._lyrics_match_from_text(text, "lrclib")
                self.assertEqual(match.timed_lyrics[0].text, "你好\n粤语音译：nei5 hou2")
                self.assertEqual(match.timed_lyrics[0].timing_kind, "synthetic")
                self.assertFalse(match.timed_lyrics[0].units)
                self.assertTrue(match.secondary_source_rows)

    def test_plain_candidate_cache_reload_does_not_create_secondary_original_rows(self):
        store = self._store()
        raw_rows = (
            TimedLyricLine(
                0.0, "你好\n粤语音译：nei5 hou2\n粤语译文：问候你", timing_kind="synthetic",
            ),
            TimedLyricLine(
                3.0, "再见\n粤语音译：zoi3 gin3\n粤语译文：道别", timing_kind="synthetic",
            ),
        )
        match = store._finalize_lyrics_match(
            LyricsMatch(raw_rows, tuple(row.text for row in raw_rows), "netease"), "yue",
        )
        store._persist_match("Song", "Artist", "", match)
        persisted = next(store.generated_root.glob("*.lrc")).read_text(encoding="utf-8")
        self.assertNotRegex(persisted, r"\[\d{2}:\d{2}")

        reloaded = self._store()
        phonetics = reloaded.resolve_cached_or_local("Song", "Artist")
        self.assertEqual(tuple(row.text for row in phonetics.timed_lyrics), (
            "你好\n粤语音译：nei5 hou2", "再见\n粤语音译：zoi3 gin3",
        ))
        self.assertEqual(tuple(row.time_seconds for row in phonetics.timed_lyrics), (0.0, 3.0))
        self.assertTrue(all(row.timing_kind == "synthetic" and not row.units for row in phonetics.timed_lyrics))

        self.settings["v2_cantonese_secondary_priority"] = "translation_first"
        translations = reloaded.resolve_cached_or_local("Song", "Artist")
        self.assertEqual(tuple(row.text for row in translations.timed_lyrics), (
            "你好\n粤语译文：问候你", "再见\n粤语译文：道别",
        ))
        self.assertEqual(tuple(row.time_seconds for row in translations.timed_lyrics), (0.0, 3.0))
        self.assertTrue(all(row.timing_kind == "synthetic" and not row.units for row in translations.timed_lyrics))

    def test_disabled_translation_enrichment_retains_metadata_and_candidates_when_reenabled(self):
        sources = (
            ("Generated", "[language:yue]\n[00:01.00]你好", "你好\n粤语音译：nei5 hou2"),
            ("Supplied", (
                "[language:yue]\n[00:01.00]你好",
                "[00:01.00]粤语音译：nei5 hou2",
                "[00:01.00]粤语译文：问候你",
            ), "你好\n粤语译文：问候你"),
        )
        for title, source, expected_translation_first in sources:
            with self.subTest(source=title):
                self.settings["v2_enable_dual_line_lyrics"] = False
                self.settings["v2_cantonese_secondary_priority"] = "phonetic_first"
                store = self._store()
                previous = store._lyrics_match_from_text(source, "netease")
                store._persist_match(title, "Artist", "", previous)
                reloaded = self._store()
                disabled = reloaded.resolve_cached_or_local(title, "Artist")
                self.assertTrue(disabled.secondary_source_rows)
                if title == "Generated":
                    self.assertEqual(disabled.timed_lyrics[0].text, "你好")
                current = self._unit_match(reloaded)
                key = reloaded._cache_key(title, "Artist", "", 0.0)
                with patch.object(reloaded, "_resolve_online", return_value=current) as resolve_online:
                    reloaded._enrich_background(key, title, "Artist", "", 0.0, True)
                    self.settings["v2_enable_dual_line_lyrics"] = True
                    self.settings["v2_cantonese_secondary_priority"] = "translation_first"
                    translated = reloaded.resolve_cached_or_local(title, "Artist")
                    self.assertEqual(translated.timed_lyrics[0].text, expected_translation_first)
                    self.assertEqual(translated.timed_lyrics[0].units, current.timed_lyrics[0].units)
                    self.settings["v2_cantonese_secondary_priority"] = "phonetic_first"
                    phonetics = reloaded.resolve_cached_or_local(title, "Artist")
                    self.assertEqual(phonetics.timed_lyrics[0].text, "你好\n粤语音译：nei5 hou2")
                    self.assertEqual(phonetics.timed_lyrics[0].units, current.timed_lyrics[0].units)
                    resolve_online.assert_called_once()


if __name__ == "__main__":
    unittest.main()
