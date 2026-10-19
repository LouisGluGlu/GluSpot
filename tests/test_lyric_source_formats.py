from __future__ import annotations

import unittest
from unittest.mock import patch

from api.qrc import _des_block, _schedule, decrypt_qrc
from api.ttml import parse_ttml, parse_ttml_time


# Authored synthetic text, encrypted independently with the upstream QRC
# reference during protocol verification. No copyrighted song text is stored.
QRC_TEXT = '<QrcInfos><Lyric_1 LyricType="1" LyricContent="[1000,3000]你(1000,500)好(1500,2500)"/></QrcInfos>'
QRC_CIPHER = (
    "EE7BEFB746F93831D42DCDF2E21F0D5D833C044042289012E0C11ABF870C7DFB9"
    "C809065C5235FF1CF6F94CEF240C69AC5A7DA382B84A28BF364CA73C139F7B6B"
    "AB4503B2E106C76D631C5EBC9E0E4B8FCEC06FBD596A66F"
)


class QrcProtocolTests(unittest.TestCase):
    def test_fips_des_vector_and_reverse_schedule(self):
        key = bytes.fromhex("133457799BBCDFF1")
        plain = bytes.fromhex("0123456789ABCDEF")
        expected = bytes.fromhex("85E813540F0AB405")
        self.assertEqual(_des_block(plain, _schedule(key)), expected)
        self.assertEqual(_des_block(expected, tuple(reversed(_schedule(key)))), plain)
        for octet, expected_hex in (("00", "8CA64DE9C1B123A7"), ("FF", "7359B2163E4EDC58")):
            value = bytes.fromhex(octet * 8)
            self.assertEqual(_des_block(value, _schedule(value)).hex().upper(), expected_hex)

    def test_independent_qrc_vector_preserves_exact_xml_and_units(self):
        self.assertEqual(decrypt_qrc(QRC_CIPHER), QRC_TEXT)
        self.assertEqual(decrypt_qrc("  " + QRC_CIPHER.lower() + "\n"), QRC_TEXT)

    def test_corrupt_hex_and_incomplete_blocks_are_not_plain_lyrics(self):
        for text in ("", "not QRC", "00", "g0" * 8, QRC_CIPHER[:-2], "00" * 16, QRC_CIPHER[:16] + "00" * 8):
            with self.subTest(text=text[:20]), self.assertRaises(ValueError):
                decrypt_qrc(text)

    def test_encrypted_and_inflated_limits_are_bounded(self):
        with patch("api.qrc.MAX_ENCRYPTED_BYTES", 8), self.assertRaises(ValueError):
            decrypt_qrc(QRC_CIPHER)
        with patch("api.qrc.MAX_DECOMPRESSED_BYTES", 16), self.assertRaises(ValueError):
            decrypt_qrc(QRC_CIPHER)


def document(body: str, *, extra: str = "", head: str = "") -> str:
    return ('<tt xmlns="http://www.w3.org/ns/ttml" '
            'xmlns:ttm="http://www.w3.org/ns/ttml#metadata" '
            'xmlns:amll="http://www.example.com/ns/amll">'
            f'<head><metadata>{head}</metadata></head><body {extra}>{body}</body></tt>')


class TtmlSourceFormatTests(unittest.TestCase):
    def test_preserves_raw_units_translation_romanization_and_metadata(self):
        raw = document('<p begin="1.25" end="5" ttm:agent="v2">'
            '<span begin="1.25" end="2.125">Hello</span> '
            '<span begin="2.5" end="5">world</span>'
            '<span ttm:role="x-translation">你好 世界</span>'
            '<span ttm:role="x-roman">ni hao shi jie</span></p>',
            extra='dur="3:20"', head='<amll:meta key="ncmMusicId" value="123"/>')
        result = parse_ttml(raw)
        self.assertEqual(result.raw_text, raw)
        self.assertEqual(result.originals[0].text, "Hello world")
        self.assertEqual(result.originals[0].timing_kind, "unit")
        self.assertEqual(tuple((u.start_seconds, u.end_seconds) for u in result.originals[0].units), ((1.25, 2.125), (2.5, 5.0)))
        self.assertEqual(result.translations[0].text, "你好 世界")
        self.assertEqual(result.romanizations[0].text, "ni hao shi jie")
        self.assertEqual(result.agents, ("v2",))
        self.assertEqual(result.metadata, (("ncmMusicId", "123"),))
        self.assertEqual(result.duration_seconds, 200.0)

    def test_background_vocals_have_their_own_true_end_and_companion_anchor(self):
        result = parse_ttml(document('<p begin="1" end="12">'
            '<span begin="1" end="2">Main</span>'
            '<span ttm:role="x-bg" begin="1.5" end="12">'
            '<span begin="1.5" end="12">oh</span>'
            '<span ttm:role="x-translation">噢</span></span></p>'))
        self.assertEqual(tuple(r.text for r in result.originals), ("Main", "(oh)"))
        self.assertEqual(result.originals[1].units[0].end_seconds, 12.0)
        self.assertEqual(result.originals[1].units[0].text, "(oh)")
        self.assertEqual(result.translations[0].time_seconds, 1.5)

    def test_existing_background_parentheses_are_not_duplicated(self):
        result = parse_ttml(document('<p begin="1">word<span ttm:role="x-bg" begin="2">(ah)</span></p>'))
        self.assertEqual(result.originals[1].text, "(ah)")

    def test_nested_syllables_keep_exact_timing_and_spaces(self):
        result = parse_ttml(document('<p begin="1">'
            '<span begin="1" end="3"><span begin="1" end="2">ne</span>'
            '<span begin="2" end="3">ver</span></span> '
            '<span begin="4" end="5">again</span></p>'))
        self.assertEqual(result.originals[0].text, "never again")
        self.assertEqual(tuple(u.start_seconds for u in result.originals[0].units), (1.0, 2.0, 4.0))
        self.assertEqual(result.originals[0].timing_kind, "unit")

    def test_plain_rows_and_missing_unit_ends_are_not_estimated(self):
        result = parse_ttml(document('<p begin="1" end="5">plain</p>'
            '<p begin="10"><span begin="10">one</span> <span begin="11">two</span></p>'))
        self.assertEqual(result.originals[0].units, ())
        self.assertEqual(result.originals[0].timing_kind, "line")
        self.assertTrue(all(u.end_seconds is None for u in result.originals[1].units))

    def test_partial_word_timing_keeps_data_without_claiming_complete_unit_timing(self):
        result = parse_ttml(document('<p begin="1">before <span begin="2" end="3">after</span></p>'))
        self.assertEqual(result.originals[0].text, "before after")
        self.assertEqual(result.originals[0].timing_kind, "line")
        self.assertEqual(len(result.originals[0].units), 1)

    def test_source_duration_is_not_synthesized_from_last_vocal(self):
        result = parse_ttml(document('<p begin="1" end="5">plain</p>'))
        self.assertIsNone(result.duration_seconds)

    def test_paragraph_level_secondary_is_not_reclassified_as_sung_original(self):
        result = parse_ttml(document('<p begin="1">original</p>'
            '<p begin="1" ttm:role="x-translation">译文</p>'
            '<p begin="1" ttm:role="x-roman">roma</p>'))
        self.assertEqual(tuple(row.text for row in result.originals), ("original",))
        self.assertEqual(result.translations[0].text, "译文")
        self.assertEqual(result.romanizations[0].text, "roma")

    def test_overlap_duet_order_and_separate_agents_are_preserved(self):
        result = parse_ttml(document('<p begin="2" ttm:agent="v2">second voice</p>'
            '<p begin="1" ttm:agent="v1"><span begin="1" end="10">held</span></p>'))
        self.assertEqual(result.agents, ("v1", "v2"))
        self.assertEqual(result.originals[0].units[0].end_seconds, 10.0)
        self.assertEqual(result.originals[1].time_seconds, 2.0)

    def test_invalid_or_unbounded_xml_does_not_produce_synthetic_lyrics(self):
        cases = ('not XML', '{"unrelated": 1}', '<html/>', '<tt/>',
            document('<p>untimed</p>'), document('<p begin="NaN">invalid</p>'),
            '<!DOCTYPE tt [<!ENTITY a "word">]><tt><body><p begin="1">&a;</p></body></tt>',
            document('<div timeContainer="seq"><p begin="1">word</p></div>'))
        for raw in cases:
            with self.subTest(raw=raw[:30]):
                self.assertIsNone(parse_ttml(raw))
        with patch("api.ttml.MAX_TTML_BYTES", 8):
            self.assertIsNone(parse_ttml(document('<p begin="1">word</p>')))
        with patch("api.ttml.MAX_TTML_ELEMENTS", 1):
            self.assertIsNone(parse_ttml(document('<p begin="1">word</p>')))
        with patch("api.ttml.MAX_TTML_ROWS", 1):
            self.assertIsNone(parse_ttml(document('<p begin="1">one</p><p begin="2">two</p>')))

    def test_clock_and_offset_formats_reject_guessed_frame_times(self):
        for raw, expected in (("1:02.345", 62.345), ("01:02:03.5", 3723.5),
                              ("1200ms", 1.2), ("2m", 120), ("1h", 3600)):
            self.assertAlmostEqual(parse_ttml_time(raw), expected)
        for raw in ("NaN", "-1", "1:70", "1:00:00:10", "120f", "15t", "1e100"):
            self.assertIsNone(parse_ttml_time(raw))


if __name__ == "__main__":
    unittest.main()
