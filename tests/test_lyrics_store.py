from __future__ import annotations

import base64
import tempfile
import unittest
import os
import threading
import time
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch

from api.lyrics import LocalLyricsStore, LyricsMatch
from api.models import TimedLyricLine, TimedLyricUnit


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
        self.assertEqual(timed[0].timing_kind, "unit")
        self.assertEqual(timed[0].units, (TimedLyricUnit(3.2, 3.8, "Hello "), TimedLyricUnit(3.8, None, "world")))

    def test_parse_yrc_preserves_actual_character_start_and_end(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            timed, _plain = store._parse_lrc_text("[1200,2400](1200,500,0)你(1700,600,0)好")

        self.assertEqual(timed[0].text, "你好")
        self.assertAlmostEqual(timed[0].time_seconds, 1.2)
        self.assertEqual(timed[0].timing_kind, "unit")
        self.assertEqual(timed[0].units, (TimedLyricUnit(1.2, 1.7, "你"), TimedLyricUnit(1.7, 2.3, "好")))

    def test_parse_qrc_xml_and_plain_krc_preserve_distinct_clock_conventions(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            qrc, _ = store._parse_lrc_text('<QrcInfos><Lyric_1 LyricType="1" LyricContent="[1200,2400]你(1200,500)好(1700,600)"/></QrcInfos>')
            krc, _ = store._parse_lrc_text("[1200,2400]<0,500,0>你<500,600,0>好")

        for line in (qrc[0], krc[0]):
            self.assertEqual(line.text, "你好")
            self.assertEqual(line.timing_kind, "unit")
            self.assertEqual(line.units, (TimedLyricUnit(1.2, 1.7, "你"), TimedLyricUnit(1.7, 2.3, "好")))

    def test_parse_verbatim_lrc_preserves_character_end_timestamps(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            timed, _ = store._parse_lrc_text("[00:03.00]你[00:03.50]好[00:04.10]")

        self.assertEqual(timed[0].text, "你好")
        self.assertEqual(timed[0].units, (TimedLyricUnit(3.0, 3.5, "你"), TimedLyricUnit(3.5, 4.1, "好")))

    def test_zero_time_and_empty_yrc_markers_do_not_create_phantom_units(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            timed, _ = store._parse_lrc_text("[0,1000](0,1000,0)\n[1000,1000](1000,500,0)你(1500,500,0)好")

        self.assertEqual(len(timed), 1)
        self.assertEqual(timed[0].text, "你好")
        self.assertEqual(timed[0].units[0].start_seconds, 1.0)

    def test_enhanced_lrc_duplicate_marker_downgrades_inconsistent_timing(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            timed, _ = store._parse_lrc_text("[00:00.000]<00:00.000>你<00:00.000>好<00:01.000>")

        self.assertEqual(timed[0].text, "你好")
        self.assertEqual(timed[0].timing_kind, "line")
        self.assertEqual(timed[0].units, ())

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

    def test_merge_promotes_actual_timed_original_above_earlier_translation(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            match = store._lyrics_match_from_text(
                ("[00:01.200]Hello", "[1200,1200](1200,500,0)你(1700,700,0)好"),
                "netease",
            )

        self.assertIsNotNone(match)
        self.assertEqual(match.timed_lyrics[0].text, "你好\nHello")
        self.assertEqual("".join(unit.text for unit in match.timed_lyrics[0].units), "你好")
        self.assertEqual(match.timed_lyrics[0].timing_kind, "unit")

    def test_fast_neighboring_original_rows_keep_their_own_translations(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            match = store._lyrics_match_from_text((
                "[00:01.000]Shine on\n[00:01.120]Keep going",
                "[00:01.000]继续闪耀\n[00:01.120]继续前进",
            ), "netease")

        self.assertIsNotNone(match)
        self.assertEqual(
            [(line.time_seconds, line.text) for line in match.timed_lyrics],
            [(1.0, "Shine on\n继续闪耀"), (1.12, "Keep going\n继续前进")],
        )

    def test_unmatched_translation_does_not_retime_adjacent_original(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            match = store._lyrics_match_from_text((
                "[00:01.000]Shine on\n[00:01.120]Keep going",
                "[00:01.115]继续前进",
            ), "netease")

        self.assertIsNotNone(match)
        self.assertEqual(
            [line.text for line in match.timed_lyrics],
            ["Shine on", "Keep going\n继续前进"],
        )

    def test_shifted_close_translation_rows_pair_in_order(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            match = store._lyrics_match_from_text((
                "[00:01.000]Shine on\n[00:01.120]Keep going",
                "[00:01.070]继续闪耀\n[00:01.080]继续前进",
            ), "netease")

        self.assertIsNotNone(match)
        self.assertEqual(
            [line.text for line in match.timed_lyrics],
            ["Shine on\n继续闪耀", "Keep going\n继续前进"],
        )

    def test_unmatched_genuine_unit_line_from_second_source_is_kept(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            match = store._lyrics_match_from_text((
                "[00:01.000]First line",
                "[3000,1000](3000,500,0)真(3500,500,0)词",
            ), "netease")

        self.assertIsNotNone(match)
        self.assertEqual([line.text for line in match.timed_lyrics], ["First line", "真词"])
        self.assertEqual(match.timed_lyrics[1].timing_kind, "unit")

    def test_merge_replaces_same_text_line_with_richer_unit_timing(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            match = store._lyrics_match_from_text(
                ("[00:01.200]你好", "[1200,1200](1200,500,0)你(1700,700,0)好"),
                "netease",
            )

        self.assertIsNotNone(match)
        self.assertEqual(len(match.timed_lyrics), 1)
        self.assertEqual(match.timed_lyrics[0].text, "你好")
        self.assertEqual(len(match.timed_lyrics[0].units), 2)

    def test_merge_preserves_existing_subrows_and_primary_units(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            units = (TimedLyricUnit(1.0, 2.0, "Shine"),)
            timed = store._merge_timed_lyric_sources([
                (TimedLyricLine(1.0, "Shine\n(echo)", units, "unit"),),
                (TimedLyricLine(1.0, "闪耀\n（回声）"),),
            ])

        self.assertEqual(timed[0].text, "Shine\n(echo)\n闪耀\n（回声）")
        self.assertEqual(timed[0].units, units)
        self.assertEqual(timed[0].timing_kind, "unit")

    def test_unit_source_upgrade_retains_prior_translation_by_original_text(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            old_line = TimedLyricLine(1.0, "Shine on\n继续闪耀")
            new_line = TimedLyricLine(1.5, "Shine on", (
                TimedLyricUnit(1.5, 2.0, "Shine "), TimedLyricUnit(2.0, 2.5, "on"),
            ), "unit")
            store._resolve_netease = lambda *_args: LyricsMatch((old_line,), (old_line.text,), "netease")
            store._resolve_qq = lambda *_args: LyricsMatch((new_line,), (new_line.text,), "qq")
            store._resolve_lrclib = lambda *_args: None
            match = store._resolve_online("Song", "Artist", "", 0.0)

        self.assertEqual(match.source, "qq")
        self.assertEqual(match.timed_lyrics[0].text, "Shine on\n继续闪耀")
        self.assertEqual(match.timed_lyrics[0].time_seconds, 1.5)
        self.assertEqual(match.timed_lyrics[0].units, new_line.units)

    def test_unit_source_upgrade_does_not_copy_translation_from_different_original(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            old_line = TimedLyricLine(1.0, "Shine on\n继续闪耀")
            new_line = TimedLyricLine(1.0, "Keep going", (
                TimedLyricUnit(1.0, 1.5, "Keep "), TimedLyricUnit(1.5, 2.0, "going"),
            ), "unit")
            store._resolve_netease = lambda *_args: LyricsMatch((old_line,), (old_line.text,), "netease")
            store._resolve_qq = lambda *_args: LyricsMatch((new_line,), (new_line.text,), "qq")
            store._resolve_lrclib = lambda *_args: None
            match = store._resolve_online("Song", "Artist", "", 0.0)

        self.assertEqual(match.timed_lyrics, (new_line,))

    def test_unit_source_upgrade_keeps_repeated_original_translations_in_order(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            previous = LyricsMatch((
                TimedLyricLine(1.0, "Shine on\n第一次闪耀"),
                TimedLyricLine(4.0, "Shine on\n第二次闪耀"),
            ), (), "netease")
            current = LyricsMatch((
                TimedLyricLine(1.5, "Shine on", (TimedLyricUnit(1.5, 2.0, "Shine on"),), "unit"),
                TimedLyricLine(4.5, "Shine on", (TimedLyricUnit(4.5, 5.0, "Shine on"),), "unit"),
            ), (), "qq")
            kept = store._retain_matching_subrows(current, previous)

        self.assertEqual([line.text for line in kept.timed_lyrics], [
            "Shine on\n第一次闪耀", "Shine on\n第二次闪耀",
        ])
        self.assertEqual([line.units for line in kept.timed_lyrics], [line.units for line in current.timed_lyrics])

    def test_unit_source_upgrade_preserves_the_new_sources_own_translation(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            previous = LyricsMatch((TimedLyricLine(1.0, "Shine on\n旧译文"),), (), "netease")
            current = LyricsMatch((TimedLyricLine(1.5, "Shine on\n新译文", (
                TimedLyricUnit(1.5, 2.0, "Shine on"),
            ), "unit"),), (), "qq")
            kept = store._retain_matching_subrows(current, previous)

        self.assertIs(kept, current)

    def test_plain_lyrics_are_marked_synthetic_not_actual_unit_timing(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            match = store._lyrics_match_from_text("Plain first line\nPlain second line", "local")

        self.assertIsNotNone(match)
        self.assertEqual(tuple(line.timing_kind for line in match.timed_lyrics), ("synthetic", "synthetic"))
        self.assertFalse(any(line.units for line in match.timed_lyrics))

    def test_plain_lyrics_remain_synthetic_after_generated_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            match = store._lyrics_match_from_text("Plain first line\nPlain second line", "lrclib")
            self.assertIsNotNone(match)
            store._persist_match("Song", "Artist", "", match)
            payload = (Path(root) / "artist - song.lrc").read_text(encoding="utf-8")
            reloaded = LocalLyricsStore(Path(root)).resolve_cached_or_local("Song", "Artist")

        self.assertNotIn("[00:00.000]", payload)
        self.assertEqual(tuple(line.timing_kind for line in reloaded.timed_lyrics), ("synthetic", "synthetic"))

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

    def test_netease_yrc_takes_precedence_over_line_lrc_and_keeps_translation(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            lyric_text = store._extract_netease_lyric_text(
                {
                    "lrc": {"lyric": "[00:01.20]你好"},
                    "yrc": {"lyric": "[1200,1200](1200,500,0)你(1700,700,0)好"},
                    "tlyric": {"lyric": "[00:01.20]Hello"},
                },
                prefer_secondary=True,
            )
            match = store._lyrics_match_from_text(lyric_text, "netease")

        self.assertIsInstance(lyric_text, tuple)
        self.assertTrue(lyric_text[0].startswith("[1200,1200]"))
        self.assertIsNotNone(match)
        self.assertEqual(match.timed_lyrics[0].text, "你好\nHello")
        self.assertEqual(len(match.timed_lyrics[0].units), 2)

    def test_netease_yrc_uses_its_aligned_translation_before_line_lrc_translation(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            lyric_text = store._extract_netease_lyric_text({
                "yrc": {"lyric": "[1500,1000](1500,500,0)Shine (2000,500,0)on"},
                "lrc": {"lyric": "[00:01.000]Shine on"},
                "tlyric": {"lyric": "[00:01.000]继续闪耀"},
                "ytlrc": {"lyric": "[00:01.500]继续闪耀"},
            }, prefer_secondary=True)
            match = store._lyrics_match_from_text(lyric_text, "netease")

        self.assertEqual(match.timed_lyrics[0].text, "Shine on\n继续闪耀")
        self.assertEqual(match.timed_lyrics[0].time_seconds, 1.5)

    def test_netease_yrc_translation_can_follow_matching_line_lrc_original(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            lyric_text = store._extract_netease_lyric_text({
                "yrc": {"lyric": "[1500,1000](1500,500,0)Shine (2000,500,0)on\n[5000,1000](5000,500,0)Keep (5500,500,0)going"},
                "lrc": {"lyric": "[00:01.000]Shine on\n[00:04.500]Keep going"},
                "tlyric": {"lyric": "[00:01.000]继续闪耀\n[00:04.500]继续前进"},
            }, prefer_secondary=True)
            match = store._lyrics_match_from_text(lyric_text, "netease")

        self.assertEqual([line.text for line in match.timed_lyrics], [
            "Shine on\n继续闪耀", "Keep going\n继续前进",
        ])
        self.assertEqual([line.time_seconds for line in match.timed_lyrics], [1.5, 5.0])
        self.assertTrue(all(line.units for line in match.timed_lyrics))

    def test_netease_yrc_does_not_attach_a_missing_reference_rows_translation_to_its_neighbor(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            lyric_text = store._extract_netease_lyric_text({
                "yrc": {"lyric": "[1150,1000](1150,500,0)Keep (1650,500,0)going"},
                "lrc": {"lyric": "[00:01.000]Shine on\n[00:01.120]Keep going"},
                "tlyric": {"lyric": "[00:01.000]继续闪耀\n[00:01.120]继续前进"},
            }, prefer_secondary=True)
            match = store._lyrics_match_from_text(lyric_text, "netease")

        self.assertEqual([line.text for line in match.timed_lyrics], ["Keep going\n继续前进"])

    def test_netease_yrc_drops_translation_of_a_proven_missing_original(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            lyric_text = store._extract_netease_lyric_text({
                "yrc": {"lyric": "[1150,1000](1150,500,0)Keep (1650,500,0)going"},
                "lrc": {"lyric": "[00:01.000]Shine on\n[00:01.120]Keep going"},
                "tlyric": {"lyric": "[00:01.000]继续闪耀"},
            }, prefer_secondary=True)
            match = store._lyrics_match_from_text(lyric_text, "netease")

        self.assertEqual([line.text for line in match.timed_lyrics], ["Keep going"])

    def test_qq_keeps_plain_and_base64_translation_fields(self):
        for encoded in (False, True):
            with self.subTest(encoded=encoded), tempfile.TemporaryDirectory() as root:
                store = LocalLyricsStore(Path(root))
                original = "[00:01.000]Shine on"
                translated = "[00:01.000]继续闪耀"
                if encoded:
                    original = base64.b64encode(original.encode("utf-8")).decode("ascii")
                    translated = base64.b64encode(translated.encode("utf-8")).decode("ascii")
                lyric_text = store._extract_qq_lyric_text({"data": {
                    "lyric": original, "trans": translated,
                }})
                match = store._lyrics_match_from_text(lyric_text, "qq")
            self.assertEqual(match.timed_lyrics[0].text, "Shine on\n继续闪耀")

    def test_netease_malformed_yrc_falls_back_to_valid_lrc(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            chosen = store._extract_netease_lyric_text(
                {
                    "yrc": {"lyric": "[1200,1200](bad-timing)broken"},
                    "lrc": {"lyric": "[00:01.20]你好"},
                },
                prefer_original=True,
            )
            match = store._lyrics_match_from_text(chosen, "netease")

        self.assertEqual(chosen, "[00:01.20]你好")
        self.assertIsNotNone(match)
        self.assertEqual(match.timed_lyrics[0].text, "你好")
        self.assertEqual(match.timed_lyrics[0].timing_kind, "line")

    def test_lyrics_match_can_convert_traditional_chinese_to_simplified(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(
                Path(root),
                settings_getter=lambda key, default=None: "simplified" if key == "v2_lyric_chinese_conversion" else default,
            )

            match = store._lyrics_match_from_text("[00:01.00]\u5f8c\u4f86\u98a8\u88e1\u6709\u59b3", "local")

        self.assertIsNotNone(match)
        self.assertEqual(match.timed_lyrics[0].text, "\u540e\u6765\u98ce\u91cc\u6709\u4f60")

    def test_chinese_conversion_keeps_true_unit_boundaries(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(
                Path(root),
                settings_getter=lambda key, default=None: "simplified" if key == "v2_lyric_chinese_conversion" else default,
            )
            match = store._lyrics_match_from_text("[1200,1400](1200,700,0)誰(1900,700,0)人", "local")

        self.assertIsNotNone(match)
        self.assertEqual(match.timed_lyrics[0].text, "谁人")
        self.assertEqual(match.timed_lyrics[0].units, (TimedLyricUnit(1.2, 1.9, "谁"), TimedLyricUnit(1.9, 2.6, "人")))

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

    def test_resolve_online_keeps_first_line_fallback_while_seeking_actual_units(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            calls = []

            store._resolve_netease = lambda *_args: calls.append("netease") or LyricsMatch((TimedLyricLine(0.0, "line"),), ("line",), "netease")
            store._resolve_qq = lambda *_args: calls.append("qq") or None
            store._resolve_lrclib = lambda *_args: calls.append("lrclib") or None

            match = store._resolve_online("Song", "Artist", "", 0.0)

        self.assertEqual(match.source, "netease")
        self.assertEqual(calls, ["netease", "qq", "lrclib"])

    def test_resolve_online_prefers_later_source_with_actual_units(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            calls = []
            store._resolve_netease = lambda *_args: calls.append("netease") or LyricsMatch((TimedLyricLine(0.0, "line"),), ("line",), "netease")
            unit_line = TimedLyricLine(0.0, "字", (TimedLyricUnit(0.0, 1.0, "字"),), "unit")
            store._resolve_qq = lambda *_args: calls.append("qq") or LyricsMatch((unit_line,), ("字",), "qq")
            store._resolve_lrclib = lambda *_args: calls.append("lrclib") or None

            match = store._resolve_online("Song", "Artist", "", 0.0)

        self.assertEqual(match.source, "qq")
        self.assertEqual(calls, ["netease", "qq"])

    def test_resolve_online_does_not_stop_at_sentence_sized_word_marker(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            calls = []
            coarse_line = TimedLyricLine(
                0.0, "Hello world", (TimedLyricUnit(0.0, None, "Hello world"),), "unit",
            )
            actual_line = TimedLyricLine(
                0.0, "Hello world", (
                    TimedLyricUnit(0.0, 0.5, "Hello "),
                    TimedLyricUnit(0.5, 1.0, "world"),
                ), "unit",
            )
            store._resolve_netease = lambda *_args: calls.append("netease") or LyricsMatch(
                (coarse_line,), (coarse_line.text,), "netease",
            )
            store._resolve_qq = lambda *_args: calls.append("qq") or LyricsMatch(
                (actual_line,), (actual_line.text,), "qq",
            )
            store._resolve_lrclib = lambda *_args: calls.append("lrclib") or None

            match = store._resolve_online("Song", "Artist", "", 0.0)

        self.assertEqual(match.source, "qq")
        self.assertEqual(calls, ["netease", "qq"])

    def test_resolve_online_keeps_earlier_fallback_when_later_source_fails(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            store._resolve_netease = lambda *_args: LyricsMatch((TimedLyricLine(1.0, "safe line"),), ("safe line",), "netease")

            def failed(*_args):
                raise TimeoutError("provider unavailable")

            store._resolve_qq = failed
            store._resolve_lrclib = lambda *_args: None
            match = store._resolve_online("Song", "Artist", "", 0.0)

        self.assertEqual(match.source, "netease")
        self.assertEqual(match.timed_lyrics[0].text, "safe line")

    def test_background_publishes_first_line_while_later_source_is_still_loading_units(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            slow_source_started = threading.Event()
            let_slow_source_finish = threading.Event()
            source_calls = []

            def first_source(*_args):
                source_calls.append("netease")
                return LyricsMatch((TimedLyricLine(1.0, "你好"),), ("你好",), "netease")

            def later_source(*_args):
                source_calls.append("qq")
                slow_source_started.set()
                let_slow_source_finish.wait(2.0)
                line = TimedLyricLine(1.0, "你好", (TimedLyricUnit(1.0, 1.4, "你"), TimedLyricUnit(1.4, 2.0, "好")), "unit")
                return LyricsMatch((line,), (line.text,), "qq")

            store._resolve_netease = first_source
            store._resolve_qq = later_source
            store._resolve_lrclib = lambda *_args: source_calls.append("lrclib") or None
            self.assertTrue(store.resolve_in_background("Song", "Artist"))
            self.assertTrue(slow_source_started.wait(2.0))
            visible = store.resolve_cached_or_local("Song", "Artist")
            self.assertEqual(visible.timed_lyrics[0].text, "你好")
            self.assertFalse(visible.timed_lyrics[0].units)
            self.assertTrue(store.is_lookup_pending("Song", "Artist"))
            self.assertEqual(source_calls, ["netease", "qq"])

            let_slow_source_finish.set()
            for _ in range(200):
                if not store.is_lookup_pending("Song", "Artist"):
                    break
                time.sleep(0.005)
            upgraded = store.resolve_cached_or_local("Song", "Artist")
            reloaded = LocalLyricsStore(Path(root)).resolve_cached_or_local("Song", "Artist")

        self.assertFalse(store.is_lookup_pending("Song", "Artist"))
        self.assertEqual(upgraded.source, "qq")
        self.assertEqual(len(upgraded.timed_lyrics[0].units), 2)
        self.assertEqual(reloaded.timed_lyrics[0].units, upgraded.timed_lyrics[0].units)
        self.assertEqual(source_calls, ["netease", "qq"])

    def test_manual_file_created_during_lookup_wins_over_later_online_unit_result(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            later_source_started = threading.Event()
            let_later_source_finish = threading.Event()

            def first_source(*_args):
                return LyricsMatch((TimedLyricLine(1.0, "online fallback"),), ("online fallback",), "netease")

            def later_source(*_args):
                later_source_started.set()
                let_later_source_finish.wait(2.0)
                line = TimedLyricLine(1.0, "online upgrade", (TimedLyricUnit(1.0, 2.0, "online upgrade"),), "unit")
                return LyricsMatch((line,), (line.text,), "qq")

            store._resolve_netease = first_source
            store._resolve_qq = later_source
            store._resolve_lrclib = lambda *_args: None
            self.assertTrue(store.resolve_in_background("Song", "Artist"))
            self.assertTrue(later_source_started.wait(2.0))
            manual_path = Path(root) / "song.lrc"
            manual_path.write_text("[00:01.00]manual line\n", encoding="utf-8")
            let_later_source_finish.set()
            for _ in range(200):
                if not store.is_lookup_pending("Song", "Artist"):
                    break
                time.sleep(0.005)
            cached = store.resolve_cached_or_local("Song", "Artist")
            manual_text = manual_path.read_text(encoding="utf-8")

        self.assertFalse(store.is_lookup_pending("Song", "Artist"))
        self.assertEqual(cached.timed_lyrics[0].text, "manual line")
        self.assertEqual(manual_text, "[00:01.00]manual line\n")

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

    def test_background_empty_lookup_stays_terminal_after_initial_search(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            started = threading.Event()
            finish = threading.Event()
            calls = []

            def empty_lookup(title, *_args):
                calls.append(title)
                started.set()
                finish.wait(2.0)
                return LyricsMatch((), (), "")

            store._resolve_online = empty_lookup
            self.assertTrue(store.resolve_in_background("Song", "Artist"))
            self.assertTrue(started.wait(2.0))
            self.assertTrue(store.is_lookup_pending("Song", "Artist"))
            self.assertFalse(store.resolve_in_background("Song", "Artist"))

            finish.set()
            deadline = time.monotonic() + 2.0
            while store.is_lookup_pending("Song", "Artist") and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertFalse(store.is_lookup_pending("Song", "Artist"))
            self.assertEqual(store.resolve_cached_or_local("Song", "Artist"), LyricsMatch((), (), ""))

            # The old 45-second miss window caused the same track to start
            # searching again and made the fullscreen cover jump back.
            with patch("api.lyrics.time.monotonic", return_value=time.monotonic() + 3600.0):
                self.assertFalse(store.resolve_in_background("Song", "Artist"))
            self.assertEqual(calls, ["Song"])

            # Another track has its own lookup state.
            with patch("api.lyrics.threading.Thread"):
                self.assertTrue(store.resolve_in_background("Other Song", "Artist"))

    def test_background_transport_failure_does_not_reopen_visible_no_lyrics(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            key = store._cache_key("Song", "Artist", "", 0.0)

            with patch("api.lyrics.urlopen", side_effect=URLError("offline")):
                with patch("api.lyrics.time.monotonic", return_value=100.0):
                    store._resolve_background(key, "Song", "Artist", "", 0.0)
            with patch("api.lyrics.time.monotonic", return_value=3700.0):
                blocked = store.resolve_in_background("Song", "Artist")

        self.assertFalse(blocked)

    def test_background_resolver_exception_does_not_reopen_visible_no_lyrics(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            key = store._cache_key("Song", "Artist", "", 0.0)

            def broken_lookup(*_args):
                raise RuntimeError("resolver failed")

            store._resolve_online = broken_lookup
            with patch("api.lyrics.time.monotonic", return_value=100.0):
                store._resolve_background(key, "Song", "Artist", "", 0.0)
            with patch("api.lyrics.time.monotonic", return_value=3700.0):
                self.assertFalse(store.resolve_in_background("Song", "Artist"))

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

    def test_persisted_actual_unit_times_survive_lrc_round_trip(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            original = store._lyrics_match_from_text("[1200,2400](1200,500,0)你(1800,1000,0)好", "netease")
            self.assertIsNotNone(original)
            store._persist_match("Song", "Artist", "", original)
            reloaded = LocalLyricsStore(Path(root)).resolve_cached_or_local("Song", "Artist")

        self.assertEqual(reloaded.timed_lyrics[0].text, "你好")
        self.assertEqual(reloaded.timed_lyrics[0].units, (TimedLyricUnit(1.2, 1.7, "你"), TimedLyricUnit(1.8, 2.8, "好")))
        self.assertEqual(reloaded.timed_lyrics[0].timing_kind, "unit")

    def test_persisted_unit_times_keep_noncontiguous_explicit_ends(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            line = TimedLyricLine(
                0.0,
                "你好",
                (TimedLyricUnit(0.0, 0.3, "你"), TimedLyricUnit(0.4, 1.0, "好")),
                "unit",
            )
            match = LyricsMatch((line,), (line.text,), "test")
            store._persist_match("Song", "Artist", "", match)
            reloaded = LocalLyricsStore(Path(root)).resolve_cached_or_local("Song", "Artist")

        self.assertEqual(reloaded.timed_lyrics[0].units, line.units)

    def test_old_generated_online_cache_enriches_units_once_without_overwriting_manual_lyrics(self):
        with tempfile.TemporaryDirectory() as root:
            source_root = Path(root) / "manual"
            generated_root = Path(root) / "generated"
            store = LocalLyricsStore(source_root, generated_root=generated_root)
            old_line = TimedLyricLine(1.2, "你好")
            store._persist_match("Song", "Artist", "", LyricsMatch((old_line,), ("你好",), "netease"))
            cached = store.resolve_cached_or_local("Song", "Artist")
            self.assertEqual(cached.timed_lyrics[0].timing_kind, "line")
            enriched_line = TimedLyricLine(1.2, "你好", (TimedLyricUnit(1.2, 1.7, "你"), TimedLyricUnit(1.7, 2.4, "好")), "unit")
            calls = []
            finished = threading.Event()

            def resolve(*_args):
                calls.append(1)
                finished.set()
                return LyricsMatch((enriched_line,), ("你好",), "netease")

            store._resolve_online = resolve
            self.assertTrue(store.enrich_in_background("Song", "Artist", unit_timing_only=True))
            self.assertTrue(finished.wait(2.0))
            # The attempt timestamp prevents another network request even
            # while the first worker is completing its persistence step.
            self.assertFalse(store.enrich_in_background("Song", "Artist", unit_timing_only=True))
            for _ in range(100):
                if not store._enrich_pending_keys:
                    break
                time.sleep(0.005)
            reloaded = LocalLyricsStore(source_root, generated_root=generated_root).resolve_cached_or_local("Song", "Artist")
            self.assertEqual(reloaded.timed_lyrics[0].units, enriched_line.units)
            self.assertEqual(calls, [1])

            manual_path = source_root / "artist - song.lrc"
            manual_path.write_text("[00:01.20]my handwritten lyric\n", encoding="utf-8")
            manual_store = LocalLyricsStore(source_root, generated_root=generated_root)
            manual_store.resolve_cached_or_local("Song", "Artist")
            self.assertFalse(manual_store.enrich_in_background("Song", "Artist", unit_timing_only=True))
            self.assertEqual(manual_path.read_text(encoding="utf-8"), "[00:01.20]my handwritten lyric\n")

    def test_dual_line_enrichment_is_throttled_after_completion(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            completed = threading.Event()
            calls = []

            def resolve(*_args):
                calls.append(1)
                completed.set()
                return LyricsMatch((TimedLyricLine(1.0, "only original"),), ("only original",), "netease")

            store._resolve_online = resolve
            self.assertTrue(store.enrich_in_background("Song", "Artist"))
            self.assertTrue(completed.wait(2.0))
            for _ in range(100):
                if not store._enrich_pending_keys:
                    break
                time.sleep(0.005)
            self.assertFalse(store.enrich_in_background("Song", "Artist"))
        self.assertEqual(calls, [1])

    def test_generated_cache_unit_enrichment_retains_same_time_translation_subrows(self):
        with tempfile.TemporaryDirectory() as root:
            source_root, generated_root = Path(root) / "manual", Path(root) / "generated"
            store = LocalLyricsStore(source_root, generated_root=generated_root)
            old = TimedLyricLine(1.0, "Shine on\n继续闪耀\n（回声）")
            store._persist_match("Song", "Artist", "", LyricsMatch((old,), (old.text,), "netease"))
            cached = store.resolve_cached_or_local("Song", "Artist")
            self.assertEqual(len(cached.timed_lyrics), 3)
            new = TimedLyricLine(1.5, "Shine on", (
                TimedLyricUnit(1.5, 2.0, "Shine "), TimedLyricUnit(2.0, 2.5, "on"),
            ), "unit")
            store._resolve_online = lambda *_args: LyricsMatch((new,), (new.text,), "qq")
            store._enrich_background(store._cache_key("Song", "Artist", "", 0.0), "Song", "Artist", "", 0.0, True)
            enriched = store.resolve_cached_or_local("Song", "Artist")
            reloaded = LocalLyricsStore(source_root, generated_root=generated_root).resolve_cached_or_local("Song", "Artist")

        self.assertEqual(enriched.timed_lyrics[0].text, "Shine on\n继续闪耀\n（回声）")
        self.assertEqual(enriched.timed_lyrics[0].units, new.units)
        self.assertEqual([line.text for line in reloaded.timed_lyrics], ["Shine on", "继续闪耀", "（回声）"])

    def test_manual_file_created_during_unit_enrichment_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as root:
            source_root, generated_root = Path(root) / "manual", Path(root) / "generated"
            store = LocalLyricsStore(source_root, generated_root=generated_root)
            old = TimedLyricLine(1.0, "old online line\n旧译文")
            store._persist_match("Song", "Artist", "", LyricsMatch((old,), (old.text,), "netease"))
            store.resolve_cached_or_local("Song", "Artist")
            manual_path = source_root / "artist - song.lrc"

            def resolve(*_args):
                manual_path.write_text("[00:01.000]manual lyric\n", encoding="utf-8")
                new = TimedLyricLine(1.0, "new online line", (
                    TimedLyricUnit(1.0, 1.4, "new "), TimedLyricUnit(1.4, 1.7, "online "), TimedLyricUnit(1.7, 2.0, "line"),
                ), "unit")
                return LyricsMatch((new,), (new.text,), "qq")

            store._resolve_online = resolve
            store._enrich_background(store._cache_key("Song", "Artist", "", 0.0), "Song", "Artist", "", 0.0, True)
            kept = store.resolve_cached_or_local("Song", "Artist")

        self.assertEqual(kept.timed_lyrics[0].text, "manual lyric")

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
