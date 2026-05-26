from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from api.lyrics import LocalLyricsStore


class LyricsStoreTests(unittest.TestCase):
    def test_cache_key_ignores_duration_jitter(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))

            first = store._cache_key("Song", "Artist", "Album", 180.0)
            second = store._cache_key("Song", "Artist", "Album", 0.0)
            third = store._cache_key("Song", "Artist", "Album", 181.2)

        self.assertEqual(first, second)
        self.assertEqual(second, third)

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


if __name__ == "__main__":
    unittest.main()
