from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from api.lyrics import LocalLyricsStore, LyricsMatch
from api.models import TimedLyricLine, TimedLyricUnit
from local.music_lyric_presentation import (
    analyze_duet_lyric_rows,
    present_lyric_text,
    present_timed_lyric_rows,
    split_display_layers,
)


class LyricStaffCreditsTests(unittest.TestCase):
    def present(self, rows, **kwargs):
        return present_timed_lyric_rows(rows, intro_countdown=False, **kwargs)

    def test_single_explicit_credit_still_cleans_without_a_block(self):
        for credit in ("作词：A", "Composer: B", "指挥 Conductor：D", "乐队 Orchestra：E"):
            with self.subTest(credit=credit):
                source = [(0., credit), (1., "真正歌词")]
                self.assertEqual(self.present(source), [source[1]])
                self.assertEqual(present_lyric_text(credit), "")

    def test_long_bilingual_fields_and_unknown_roles_follow_credit_evidence(self):
        source = [
            (0., "作词 Lyricist：A"), (1., "作曲 Composer：B"),
            (2., "斯瓦希里语顾问 Swahili Language Consultant：C"),
            (3., "绞弦琴 Hurdy Gurdy：E"),
            (4., "指挥 Conductor：D"), (5., "乐队 Orchestra：E"),
            (9., "第一句真正歌词"),
        ]
        self.assertEqual(self.present(source), [source[-1]])

    def test_uncommon_roles_allow_long_combined_instruments_and_credit_separators(self):
        source = [
            (0., "作词：A"), (1., "作曲：B"), (1.5, "---"),
            (2., "印第安笛 Native American Flute / 排箫 Pan Flute / 盖那笛 Quena：C"),
            (3., "绞弦琴 Hurdy Gurdy：D"), (3.5, "#"),
            (4., "第一句真正歌词"),
        ]
        self.assertEqual(self.present(source), [source[-1]])

    def test_multiple_explicit_roles_allow_additional_field_delimiters(self):
        for credit in ("混音, 母带：A", "混音，母带：A", "吉他・贝斯：A", "作词·作曲：A"):
            with self.subTest(credit=credit):
                self.assertEqual(self.present([(0., credit), (4., "真正歌词")]), [(4., "真正歌词")])

    def test_unknown_colon_fields_alone_do_not_establish_a_staff_block(self):
        source = [(0., "罕见职位：A"), (1., "另个职位：B"), (4., "真正歌词")]
        self.assertEqual(self.present(source), source)

    def test_one_explicit_credit_does_not_license_unknown_trailing_fields(self):
        source = [(0., "作曲：A"), (1., "未知职位：B"), (2., "另个职位：C"), (4., "真正歌词")]
        self.assertEqual(self.present(source), source[1:])

    def test_isolated_unknown_trailer_is_kept_after_two_explicit_credits(self):
        source = [(0., "作词：A"), (1., "作曲：B"), (2., "未知角色：唱下去"), (5., "真正歌词")]
        self.assertEqual(self.present(source), source[2:])

    def test_colon_prose_is_preserved_at_both_edges_and_after_credits(self):
        prose = ["鼓起勇气：向前走", "他说：我爱你", "心中答案：永远都在", "I remember: you were there"]
        for text in prose:
            with self.subTest(text=text):
                source = [(0., "作词：A"), (1., "作曲：B"), (2., text), (4., "Next line"), (6., text)]
                self.assertEqual(self.present(source), source[2:])
                self.assertEqual(present_lyric_text(text), text)

    def test_two_prose_colons_cannot_be_absorbed_as_unknown_positions(self):
        source = [(0., "作词：A"), (1., "作曲：B"),
                  (2., "鼓起勇气：向前走"), (3., "他说：我爱你"), (6., "一起唱")]
        self.assertEqual(self.present(source), source[2:])

    def test_interior_unknown_fields_do_not_join_removed_explicit_credits(self):
        source = [(0., "真正歌词"), (1., "作词：A"), (2., "作曲：B"),
                  (3., "未知职位：C"), (4., "另个职位：D"), (6., "继续歌词")]
        self.assertEqual(self.present(source), [source[0], *source[3:]])

    def test_verified_singers_and_duet_roles_terminate_unknown_extension(self):
        source = [(0., "作词：A"), (1., "作曲：B"),
                  (2., "陈甲：先唱"), (2.5, "李乙：接着唱"), (4., "陈甲+李乙：合唱")]
        result = self.present(source, singer_names="陈甲、李乙")
        self.assertEqual(result, source[2:])
        duet = analyze_duet_lyric_rows(result, singer_names="陈甲、李乙")
        self.assertEqual(duet.rows[0].body_text, "先唱")
        self.assertEqual(duet.rows[1].body_text, "接着唱")
        self.assertEqual(len(duet.groups), 1)

    def test_verified_singer_with_credit_word_name_is_not_removed(self):
        source = [(0., "Music：sing with me"), (3., "下一句")]
        self.assertEqual(self.present(source, singer_names="Music"), source)

    def test_translation_romanization_and_parenthetical_vocals_keep_their_layers(self):
        source = [(0., "作词 Lyricist：A"), (1., "作曲 Composer：B"),
                  (6., "Shine on\n翻译：继续闪耀"), (7., "（和声：oh）"),
                  (12., "君を想う\nromanization: kimi o omou"), (16., "下一句")]
        visible = self.present(source, show_translation=True)
        hidden = self.present(source, show_translation=False)
        layers = split_display_layers(visible[0][1], source_text=source[2][1])
        self.assertEqual(layers.primary_text, "Shine on")
        self.assertEqual(layers.translation_text, "继续闪耀")
        self.assertEqual(layers.parenthetical_text, "和声：oh")
        self.assertEqual(split_display_layers(hidden[0][1], show_translation=False).parenthetical_text, "和声：oh")
        self.assertIn("kimi o omou", visible[1][1])
        self.assertNotIn("kimi o omou", hidden[1][1])

    def test_json_only_and_old_wrapped_headers_never_become_synthetic_lyrics(self):
        header = '{"t":-1,"c":[{"tx":"作词: "},{"tx":"A"}]}'
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            for raw in (header, "[00:00.000]" + header, "[100,400]" + header):
                with self.subTest(raw=raw):
                    self.assertEqual(store._parse_lrc_text(raw), ((), ()))
                    self.assertIsNone(store.parse_online_lyrics(raw_lrc=raw, source="netease-account"))
            old_cache = self.present([(0., header), (2., header)])
            self.assertEqual(old_cache, [])

    def test_ordinary_braces_and_unrelated_json_survive_whole_song_cleanup(self):
        for text in ("Sing {with me}", '{"message":"Sing with me"}', '{"t":0,"c":[{"text":"Sing"}]}'):
            with self.subTest(text=text):
                self.assertEqual(self.present([(0., text), (4., "下一句")]), [(0., text), (4., "下一句")])

    def test_display_filter_does_not_rewrite_raw_units_or_persisted_cache(self):
        credit = TimedLyricLine(0., "作词 Lyricist：A")
        units = (TimedLyricUnit(8., 9., "你"), TimedLyricUnit(9., 10., "好"))
        vocal = TimedLyricLine(8., "你好\n翻译：Hello there", units, "unit")
        raw = LyricsMatch((credit, vocal), (credit.text, vocal.text), "netease-account")
        with tempfile.TemporaryDirectory() as root:
            store = LocalLyricsStore(Path(root))
            store._persist_match("Fixture Song", "Fixture Artist", "", raw)
            cache = next(Path(root).glob("*.lrc"))
            before = cache.read_bytes()
            shown = self.present([(line.time_seconds, line.text) for line in raw.timed_lyrics])
            self.assertEqual(shown, [(8., "你好\nHello there")])
            self.assertEqual(cache.read_bytes(), before)
            self.assertIn("作词 Lyricist：A", before.decode("utf-8"))
        self.assertIs(raw.timed_lyrics[1].units, units)
        self.assertEqual(raw.timed_lyrics[0], credit)
        self.assertEqual(raw.source, "netease-account")


if __name__ == "__main__":
    unittest.main()
