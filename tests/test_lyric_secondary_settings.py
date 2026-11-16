"""Secondary-line settings do what the settings page says they do."""
import tempfile
import unittest
from pathlib import Path

from api.lyrics import LocalLyricsStore

CANTONESE = "[00:01.00]我哋一齊去睇海\n[00:05.00]你唔好走\n"
CANTONESE_TR = "[00:01.00]我们一起去看海\n[00:05.00]你不要走\n"
CANTONESE_ROMA = "[00:01.00]ngo5 dei6 jat1 cai4 heoi3 tai2 hoi2\n[00:05.00]nei5 m4 hou2 zau2\n"
MANDARIN = "[00:01.00]汉字的发展\n[00:05.00]我爱你\n"
MANDARIN_TR = "[00:01.00]Development of Chinese\n[00:05.00]I love you\n"


def lines(settings, lrc, translation, romanization=""):
    values = {"v2_enable_dual_line_lyrics": True, **settings}
    with tempfile.TemporaryDirectory() as folder:
        store = LocalLyricsStore(root=Path(folder), settings_getter=lambda key, default=None: values.get(key, default))
        match = store.parse_online_lyrics(raw_lrc=lrc, translation=translation, romanization=romanization,
                                          source="netease")
        return [line.text for line in match.timed_lyrics]


class SecondaryLineSettingsTests(unittest.TestCase):
    def test_transliteration_falls_back_to_translation_when_a_song_has_none(self):
        rows = lines({"v2_dual_line_secondary_source": "transliteration"}, MANDARIN, MANDARIN_TR)
        self.assertEqual(rows[1].split("\n")[1], "I love you")

    def test_traditional_conversion_keeps_ni_and_converts_the_translation_line(self):
        rows = lines({"v2_lyric_chinese_conversion": "traditional"}, MANDARIN, MANDARIN_TR)
        self.assertEqual(rows[1].split("\n")[0], "我愛你")
        self.assertNotIn("妳", "".join(rows))

    def test_cantonese_follow_choice_uses_translation_or_jyutping(self):
        follow = {"v2_cantonese_secondary_priority": "translation_first"}
        translated = lines(follow, CANTONESE, CANTONESE_TR, CANTONESE_ROMA)
        self.assertIn("我们一起去看海", translated[0])
        phonetic = lines({**follow, "v2_dual_line_secondary_source": "transliteration"},
                         CANTONESE, CANTONESE_TR, CANTONESE_ROMA)
        self.assertIn("ngo5", phonetic[0])

    def test_cantonese_jyutping_choice_wins_over_translation(self):
        rows = lines({"v2_cantonese_secondary_priority": "phonetic_first"}, CANTONESE, CANTONESE_TR, CANTONESE_ROMA)
        self.assertIn("ngo5", rows[0])


if __name__ == "__main__":
    unittest.main()
