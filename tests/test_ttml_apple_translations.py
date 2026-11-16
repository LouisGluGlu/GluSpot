import unittest

from api.ttml import parse_ttml

APPLE = """<tt xmlns="http://www.w3.org/ns/ttml" xmlns:itunes="http://music.apple.com/lyric-ttml-internal"
 xmlns:ttm="http://www.w3.org/ns/ttml#metadata" itunes:timing="Word" xml:lang="en">
<head><metadata><iTunesMetadata xmlns="http://music.apple.com/lyric-ttml-internal">
<translations>
<translation type="subtitle" xml:lang="ja"><text for="L1">こんにちは</text></translation>
<translation type="subtitle" xml:lang="zh-Hans">
<text for="L1">你好世界</text>
<text for="L2">再见<span ttm:role="x-bg">（和声）</span></text>
</translation>
</translations></iTunesMetadata></metadata></head>
<body dur="10.0"><div>
<p begin="1.0" end="3.0" itunes:key="L1"><span begin="1.0" end="2.0">Hello </span><span begin="2.0" end="3.0">world</span></p>
<p begin="4.0" end="6.0" itunes:key="L2"><span begin="4.0" end="6.0">Goodbye</span></p>
<p begin="7.0" end="9.0" itunes:key="L3"><span begin="7.0" end="9.0">Untranslated</span></p>
</div></body></tt>"""


class AppleTtmlTranslationTests(unittest.TestCase):
    def test_head_translations_pair_with_keyed_lines_in_chinese(self):
        document = parse_ttml(APPLE)
        self.assertIsNotNone(document)
        self.assertEqual([row.text for row in document.originals], ["Hello world", "Goodbye", "Untranslated"])
        self.assertEqual([(row.time_seconds, row.text) for row in document.translations],
                         [(1.0, "你好世界"), (4.0, "再见")])

    def test_inline_translations_still_win(self):
        inline = APPLE.replace(
            '<span begin="4.0" end="6.0">Goodbye</span>',
            '<span begin="4.0" end="6.0">Goodbye</span><span ttm:role="x-translation">行内</span>')
        document = parse_ttml(inline)
        self.assertEqual([row.text for row in document.translations], ["行内"])

    def test_without_head_translations_nothing_changes(self):
        start = APPLE.index("<head>")
        end = APPLE.index("</head>") + len("</head>")
        document = parse_ttml(APPLE[:start] + APPLE[end:])
        self.assertEqual(document.translations, ())


if __name__ == "__main__":
    unittest.main()
