from __future__ import annotations

import unittest
from unittest.mock import patch

import local.music_lyric_presentation as lyric_presentation

from local.music_lyric_presentation import (
    INTRO_COUNTDOWN_TEXT,
    analyze_duet_lyric_rows,
    intro_countdown_progress,
    present_lyric_text,
    present_timed_lyric_rows,
    split_display_layers,
)


class MusicLyricPresentationTests(unittest.TestCase):
    def test_display_layers_separate_inline_bracket_and_bilingual_echo(self):
        text = "Shine (echo) on me\n翻译：照耀（回声）我"
        layers = split_display_layers(text)
        self.assertEqual(layers.original_text, text)
        self.assertEqual(layers.original_primary_text, "Shine (echo) on me")
        self.assertEqual(layers.primary_text, "Shine on me")
        self.assertEqual(layers.translation_text, "照耀我")
        self.assertEqual(layers.parenthetical_text, "echo")
        self.assertEqual(layers.parenthetical_translation_text, "回声")
        self.assertEqual(len(layers.primary_source_offsets), len(layers.primary_text) + 1)
        self.assertEqual(layers.original_primary_text[layers.primary_source_offsets[6]], "o")

    def test_display_layers_accept_visual_separator_and_standalone_parentheses(self):
        layers = split_display_layers("Shine\u2028(echo)\u2028闪耀\u2028（回声）")
        self.assertEqual(layers.primary_text, "Shine")
        self.assertEqual(layers.translation_text, "闪耀")
        self.assertEqual(layers.parenthetical_text, "echo")
        self.assertEqual(layers.parenthetical_translation_text, "回声")

    def test_display_layers_keep_explicit_same_language_translation_provenance(self):
        raw = "Hello\n翻译：Love"
        cleaned = present_lyric_text(raw)
        self.assertEqual(cleaned, "Hello\nLove")
        self.assertEqual(split_display_layers(cleaned).primary_text, "Hello\nLove")
        layers = split_display_layers(cleaned, source_text=raw)
        self.assertEqual(layers.primary_text, "Hello")
        self.assertEqual(layers.translation_text, "Love")
        self.assertEqual(layers.original_text, cleaned)

    def test_display_layers_translation_toggle_suppresses_bilingual_bracket_regression(self):
        raw = "Shine（啊）\n照耀"
        cleaned = present_lyric_text(raw, show_translation=False)
        self.assertEqual(cleaned, "Shine（啊）")
        layers = split_display_layers(cleaned, source_text=raw, show_translation=False)
        self.assertEqual(layers.primary_text, "Shine")
        self.assertEqual(layers.parenthetical_text, "啊")
        self.assertIsNone(layers.translation_text)
        self.assertIsNone(layers.parenthetical_translation_text)

    def test_whole_parenthesized_translation_is_a_translation_not_sung_bracket(self):
        raw = "Shine on\n（继续闪耀）"
        cleaned = present_lyric_text(raw)
        layers = split_display_layers(cleaned, source_text=raw)
        self.assertEqual(layers.primary_text, "Shine on")
        self.assertEqual(layers.translation_text, "继续闪耀")
        self.assertIsNone(layers.parenthetical_text)
        self.assertIsNone(layers.parenthetical_translation_text)
        hidden = split_display_layers(
            present_lyric_text(raw, show_translation=False),
            source_text=raw, show_translation=False,
        )
        self.assertEqual(hidden.primary_text, "Shine on")
        self.assertIsNone(hidden.translation_text)
        self.assertIsNone(hidden.parenthetical_text)

    def test_parenthesized_main_word_does_not_mask_translation_detection(self):
        raw = "Shine（echo）on\n继续闪耀"
        layers = split_display_layers(present_lyric_text(raw), source_text=raw)
        self.assertEqual(layers.primary_text, "Shineon")
        self.assertEqual(layers.translation_text, "继续闪耀")
        self.assertEqual(layers.parenthetical_text, "echo")

    def test_whole_parenthetical_original_keeps_its_translation_under_the_popout(self):
        for raw in ("(Shine on)\n（继续闪耀）", "(Shine on)\n继续闪耀"):
            with self.subTest(raw=raw):
                layers = split_display_layers(present_lyric_text(raw), source_text=raw)
                self.assertEqual(layers.primary_text, "")
                self.assertIsNone(layers.translation_text)
                self.assertEqual(layers.parenthetical_text, "Shine on")
                self.assertEqual(layers.parenthetical_translation_text, "继续闪耀")
                hidden = split_display_layers(
                    present_lyric_text(raw, show_translation=False), source_text=raw, show_translation=False,
                )
                self.assertEqual(hidden.parenthetical_text, "Shine on")
                self.assertIsNone(hidden.parenthetical_translation_text)

    def test_inline_parenthetical_translation_keeps_an_english_name(self):
        raw = "I sing (Shine on)\n我唱歌（继续闪耀Taylor）"
        layers = split_display_layers(present_lyric_text(raw), source_text=raw)
        self.assertEqual(layers.primary_text, "I sing")
        self.assertEqual(layers.translation_text, "我唱歌")
        self.assertEqual(layers.parenthetical_text, "Shine on")
        self.assertEqual(layers.parenthetical_translation_text, "继续闪耀Taylor")

    def test_translation_with_a_latin_proper_name_is_still_a_translation(self):
        raw = "Keep singing\n像Taylor一样继续唱歌"
        layers = split_display_layers(present_lyric_text(raw), source_text=raw)
        self.assertEqual(layers.primary_text, "Keep singing")
        self.assertEqual(layers.translation_text, "像Taylor一样继续唱歌")
        self.assertEqual(present_lyric_text(raw, show_translation=False), "Keep singing")

    def test_leading_parenthetical_vocal_does_not_steal_main_translation(self):
        raw = "(echo)\nShine on\n继续闪耀"
        layers = split_display_layers(present_lyric_text(raw), source_text=raw)
        self.assertEqual(layers.primary_text, "Shine on")
        self.assertEqual(layers.translation_text, "继续闪耀")
        self.assertEqual(layers.parenthetical_text, "echo")
        self.assertIsNone(layers.parenthetical_translation_text)

    def test_display_layers_translation_toggle_does_not_hide_same_language_duet(self):
        raw = "薛：我等你\n刘：我记得你"
        layers = split_display_layers(raw, show_translation=False)
        self.assertEqual(layers.primary_text, raw)
        self.assertIsNone(layers.translation_text)

    def test_display_layers_same_script_duet_is_not_translation(self):
        source = "薛：我等你的雨季（轻声）\n刘：我记得是雨季"
        layers = split_display_layers(source)
        self.assertEqual(layers.primary_text, "薛：我等你的雨季\n刘：我记得是雨季")
        self.assertIsNone(layers.translation_text)
        self.assertEqual(layers.parenthetical_text, "轻声")
        self.assertEqual(layers.original_text, source)

    def test_display_layers_leave_nested_and_metadata_parentheses_untouched(self):
        layers = split_display_layers("I (feat. Friend) sing ((nested)) and (hey)")
        self.assertEqual(layers.primary_text, "I (feat. Friend) sing ((nested)) and")
        self.assertEqual(layers.parenthetical_text, "hey")

    def test_display_layers_offsets_skip_duplicate_parenthetical_words(self):
        layers = split_display_layers("I (love) love")
        self.assertEqual(layers.primary_text, "I love")
        self.assertEqual(layers.primary_source_offsets, (0, 1, 9, 10, 11, 12, 13))

    def test_verified_singer_prefixes_survive_intro_cleanup_without_recurring(self):
        source = [
            (0.0, "节目：开场"),
            (3.0, "薛：我等你的雨季"),
            (3.7, "刘惜君：我记得是雨季"),
            (8.0, "下一句"),
        ]
        cleaned = present_timed_lyric_rows(
            source, singer_names="薛之谦、刘惜君", intro_countdown=False,
        )
        self.assertEqual(cleaned, source[1:])
        self.assertEqual(source[1][1], "薛：我等你的雨季")

    def test_duet_pair_keeps_distinct_times_and_strips_only_known_singer_prefixes(self):
        source = [
            (183.04, "薛 : 我等你的雨季 如此安静"),
            (183.04, "刘 : 我记得是雨季 爱上的你"),
            (191.08, "薛 : 我记得那草坪 一览无遗"),
            (191.08, "刘 : 我等你的草坪 高楼耸起"),
        ]
        result = analyze_duet_lyric_rows(source, singer_names="薛之谦、刘惜君")
        self.assertEqual([(group.first_index, group.second_index) for group in result.groups], [(0, 1), (2, 3)])
        self.assertEqual([(row.source_index, row.start_seconds) for row in result.rows],
                         [(index, time) for index, (time, _text) in enumerate(source)])
        self.assertEqual([row.body_text for row in result.rows], [
            "我等你的雨季 如此安静", "我记得是雨季 爱上的你",
            "我记得那草坪 一览无遗", "我等你的草坪 高楼耸起",
        ])
        self.assertEqual([row.alignment for row in result.rows], ["left", "right", "left", "right"])
        self.assertEqual(source[0][1], "薛 : 我等你的雨季 如此安静")

    def test_duet_requires_adjacent_prefixed_lines_at_most_one_second_apart(self):
        source = [
            (0.0, "薛：一句"), (1.0, "刘：同唱"),
            (4.0, "薛：下一句"), (4.5, "未署名一句"), (4.8, "刘：不会跨行组合"),
            (7.0, "薛：再一句"), (8.001, "刘：差得太远"),
        ]
        result = analyze_duet_lyric_rows(source, singer_names=("薛之谦", "刘惜君"))
        self.assertEqual([(group.first_index, group.second_index) for group in result.groups], [(0, 1)])
        self.assertEqual([row.dual_group_index for row in result.rows], [0, 0, None, None, None, None, None])

    def test_unprefixed_chats_at_2_53_seconds_are_not_duet(self):
        source = [
            (182.36, "我等你的雨季 如此安静"),
            (184.89, "我记得是雨季 爱上的你"),
        ]
        result = analyze_duet_lyric_rows(source, singer_names="薛之谦、刘惜君")
        self.assertFalse(result.groups)
        self.assertEqual([row.body_text for row in result.rows], [text for _time, text in source])

    def test_one_late_singer_moves_following_rows_right_and_ordinary_colons_stay_lyrics(self):
        source = [
            (0.0, "先唱一句"),
            (4.0, "刘惜君：我继续唱"),
            (8.0, "没有前缀也跟随"),
            (12.0, "爱：本就无需解释"),
        ]
        result = analyze_duet_lyric_rows(source, singer_names="薛之谦、刘惜君")
        self.assertEqual([row.alignment for row in result.rows], ["left", "right", "right", "right"])
        self.assertEqual(result.rows[3].body_text, "爱：本就无需解释")
        self.assertIsNone(result.rows[3].singer)
        self.assertFalse(result.groups)

    def test_distinct_roles_alternate_and_combined_role_returns_left(self):
        source = [
            (0.0, "薛之谦：先唱"),
            (4.0, "刘惜君：回应"),
            (8.0, "张三：再唱"),
            (12.0, "薛+刘：合唱"),
            (16.0, "余音"),
        ]
        result = analyze_duet_lyric_rows(source, singer_names=("薛之谦", "刘惜君", "张三"))
        self.assertEqual([row.alignment for row in result.rows], ["left", "right", "left", "left", "left"])
        self.assertEqual(result.rows[3].singer, "薛之谦+刘惜君")
        self.assertEqual(result.rows[3].body_text, "合唱")

    def test_unverified_colon_is_not_automatically_a_singer(self):
        source = [(0.0, "今天：先发光"), (0.5, "明天：再发光")]
        result = analyze_duet_lyric_rows(source)
        self.assertFalse(result.groups)
        self.assertEqual([row.singer for row in result.rows], [None, None])
        self.assertEqual([row.body_text for row in result.rows], [text for _time, text in source])

    def test_labeled_intro_is_replaced_by_countdown_without_changing_song_times(self):
        source = [
            (0.0, "作词：Alice"),
            (1.0, "作曲：Bob\n编曲：Carol"),
            (2.0, "[Verse 1]"),
            (8.25, "[Verse 1] Shine on"),
            (12.0, "作词：this line is actually sung"),
        ]

        self.assertEqual(
            present_timed_lyric_rows(source),
            [
                (0.0, INTRO_COUNTDOWN_TEXT),
                (8.25, "Shine on"),
            ],
        )
        self.assertEqual(source[0], (0.0, "作词：Alice"))

    def test_english_produced_by_credit_with_colon_is_removed_throughout_song(self):
        source = [
            (0.2, "Produced by: JVKE"),
            (5.0, "Shine on"),
            (9.0, "Produced by: fate"),
        ]
        self.assertEqual(
            present_timed_lyric_rows(source),
            [(0.0, INTRO_COUNTDOWN_TEXT), (5.0, "Shine on")],
        )

    def test_disabling_intro_cleanup_preserves_verse_label_but_filters_colon_note(self):
        source = [(0.5, "作词：Alice"), (4.0, "[Verse 2] Shine on")]

        self.assertEqual(
            present_timed_lyric_rows(source, clean_intro=False),
            [(4.0, "[Verse 2] Shine on")],
        )

    def test_colon_notes_are_removed_before_countdown_and_keep_surviving_times(self):
        source = [
            (0.0, "Album: credits"),
            (2.0, "Producer：Someone"),
            (6.0, "Shine on\n发行：Label"),
            (9.0, "翻译：单独说明"),
            (12.0, "下一句"),
        ]
        self.assertEqual(
            present_timed_lyric_rows(source),
            # The latest colon rule removes only edge runs; interior notes
            # cannot be discarded based on a field name alone.
            [(0.0, INTRO_COUNTDOWN_TEXT), (6.0, "Shine on\n发行：Label"),
             (9.0, "翻译：单独说明"), (12.0, "下一句")],
        )
        self.assertEqual(source[2], (6.0, "Shine on\n发行：Label"))

    def test_repeated_singer_label_keeps_last_leading_colon_line_and_later_duet(self):
        source = [
            (0.0, "节目：片头"),
            (1.0, "旁白: 引言"),
            (6.0, "成龙：先唱这一句"),
            (9.0, "没有标记的合唱"),
            (12.0, "成龙: 再唱一句"),
            (15.0, "金善喜：接着唱"),
        ]
        self.assertEqual(
            present_timed_lyric_rows(source, singer_names="成龙、金善喜"),
            [(0.0, INTRO_COUNTDOWN_TEXT), (6.0, "成龙：先唱这一句"),
             (9.0, "没有标记的合唱"), (12.0, "成龙: 再唱一句"),
             (15.0, "金善喜：接着唱")],
        )
        self.assertEqual(source[0], (0.0, "节目：片头"))

    def test_unrepeated_last_intro_label_is_removed_but_later_colon_vocals_remain(self):
        source = [
            (0.0, "节目：片头"), (1.0, "旁白：一次性介绍"),
            (4.0, "歌曲开始"), (7.0, "金善喜：人声歌词"),
        ]
        self.assertEqual(
            present_timed_lyric_rows(source, singer_names="金善喜"),
            [(4.0, "歌曲开始"), (7.0, "金善喜：人声歌词")],
        )

    def test_fully_singer_labeled_duet_is_kept_after_intro_notes(self):
        sung = [(3.0, "成龙：第一句"), (5.0, "金善喜：第二句"),
                (7.0, "成龙：第三句"), (9.0, "金善喜：第四句")]
        source = [(0.0, "节目：介绍"), (1.0, "旁白：开场")] + sung
        self.assertEqual(present_timed_lyric_rows(source, intro_countdown=False), sung)

    def test_same_time_three_subrows_keep_interior_note_translation_and_known_singer(self):
        source = [
            (0.0, "片头：开场"),
            (6.0, "成龙：开唱\n翻译：Sing now\n制作人：A"),
            (10.0, "成龙：再唱\n金善喜：合唱"),
        ]
        self.assertEqual(
            present_timed_lyric_rows(source, intro_countdown=False, singer_names="成龙、金善喜"),
            [(6.0, "成龙：开唱\nSing now\n制作人：A"),
             (10.0, "成龙：再唱\n金善喜：合唱")],
        )
        self.assertEqual(
            present_lyric_text("制作人：A\nShine on\n翻译：闪耀"),
            "Shine on\n闪耀",
        )
        self.assertEqual(
            present_lyric_text("翻译：闪耀\n制作人：A\nShine on"),
            "Shine on\n闪耀",
        )

    def test_labeled_translation_remains_available_without_colon_label(self):
        self.assertEqual(present_lyric_text("Shine\n翻译：闪耀"), "Shine\n闪耀")
        self.assertEqual(present_lyric_text("Shine 翻译:闪耀"), "Shine\n闪耀")
        self.assertEqual(present_lyric_text("Shine 翻译:闪耀", show_translation=False), "Shine")

    def test_countdown_can_be_disabled_independently_for_callers(self):
        source = [(1.0, "制作人：A"), (5.0, "Hello")]

        self.assertEqual(
            present_timed_lyric_rows(source, intro_countdown=False),
            [(5.0, "Hello")],
        )
        self.assertEqual(present_timed_lyric_rows([(0.0, "Hello")]), [(0.0, "Hello")])
        self.assertEqual(present_timed_lyric_rows([(0.0, "作曲：A")]), [])

    def test_countdown_requires_first_sung_line_to_start_at_five_seconds(self):
        for start in (0.1, 4.0, 4.999):
            with self.subTest(start=start):
                source = [(0.0, "作词：Alice"), (start, "Shine on")]
                self.assertEqual(present_timed_lyric_rows(source), [(start, "Shine on")])
        self.assertEqual(
            present_timed_lyric_rows([(0.0, "作词：Alice"), (5.0, "Shine on")]),
            [(0.0, INTRO_COUNTDOWN_TEXT), (5.0, "Shine on")],
        )

    def test_inline_translation_is_split_and_tracks_shared_time(self):
        source = [(12.5, "Shine on me照亮我"), (19.0, "Keep going\n翻译：继续前行")]

        self.assertEqual(
            present_timed_lyric_rows(source, show_translation=True, intro_countdown=False),
            [(12.5, "Shine on me\n照亮我"), (19.0, "Keep going\n继续前行")],
        )
        self.assertEqual(
            present_timed_lyric_rows(source, show_translation=False, intro_countdown=False),
            [(12.5, "Shine on me"), (19.0, "Keep going")],
        )
        self.assertEqual(present_lyric_text("Shine闪耀"), "Shine\n闪耀")
        self.assertEqual(present_lyric_text("Shine闪耀", show_translation=False), "Shine")

    def test_same_script_or_code_switched_vocals_are_not_assumed_to_be_translation(self):
        self.assertEqual(present_lyric_text("你要走\n我不留", show_translation=False), "你要走\n我不留")
        self.assertEqual(present_lyric_text("I love 北京", show_translation=False), "I love 北京")

    def test_latin_only_lines_skip_mixed_script_expression(self):
        text = "Feet up on the dash, drivin' nowhere fast, burnin' through the summer " * 5
        with patch.object(lyric_presentation, "_INLINE_LATIN_HAN_RE") as mixed_script:
            self.assertEqual(present_lyric_text(text), text.strip())
            mixed_script.search.assert_not_called()
        self.assertEqual(present_lyric_text("Shine on me照亮我"), "Shine on me\n照亮我")

    def test_verse_label_is_removed_without_removing_lyric(self):
        self.assertEqual(present_lyric_text("[Verse 3: Singer] Follow me"), "Follow me")
        self.assertEqual(present_lyric_text("[Verse 3]"), "")

    def test_standalone_section_markers_do_not_become_parenthetical_vocals(self):
        markers = (
            "(Pre-chorus:)", "(Chorus:)", "(Verse 1:)", "[Bridge]",
            "（Post chorus 2：）", "【INTRO】", "［Outro］", "(Refrain)",
            "(Hook 2)", "(Interlude)", "Pre-chorus:", "Bridge：",
        )
        for marker in markers:
            with self.subTest(marker=marker):
                self.assertEqual(present_lyric_text(marker), "")
                self.assertEqual(
                    present_timed_lyric_rows([(10.0, marker)], intro_countdown=False), [],
                )
                self.assertEqual(present_lyric_text(marker, clean_intro=False), marker)

    def test_section_cleanup_retains_sung_parentheses_colons_and_plain_words(self):
        vocals = (
            "For so long (long)", "(Bridge over troubled water)",
            "(Chorus: keep singing)", "Bridge", "Chorus", "I sing (again)",
            "Lead: keep singing", "The chorus: it goes on",
        )
        for text in vocals:
            with self.subTest(text=text):
                self.assertEqual(present_lyric_text(text), text)
        self.assertEqual(
            present_lyric_text("(Chorus:)\nShine (again)\n继续闪耀（再一次）"),
            "Shine (again)\n继续闪耀（再一次）",
        )

    def test_rather_pretend_section_marker_leaves_no_empty_annotation_slot(self):
        # Same-time original and translation rows have already been coalesced
        # by the surface; these source times match the reported cache sequence.
        source = [
            (13.733, "For so long (long)\n漫长岁月里 (岁月里)"),
            (16.681, "Now all I want to do is love you\n如今我只愿爱你"),
            (22.441, "But my head is holding up my heart, my heart\n可理智束缚着我的心 我的心"),
            (29.697, "(Pre-chorus:)"),
            (31.614, "And I've seen all the signs\n我眼中所见迹象"),
        ]
        original = list(source)
        rows = present_timed_lyric_rows(source, intro_countdown=False)
        self.assertEqual(rows, source[:3] + source[4:])
        layers = [split_display_layers(text) for _start, text in rows]
        self.assertTrue(all(layer.primary_text for layer in layers))
        self.assertEqual(layers[0].parenthetical_text, "long")
        self.assertEqual(layers[0].parenthetical_translation_text, "岁月里")
        self.assertIsNone(layers[2].parenthetical_text)
        self.assertEqual(source, original)

    def test_countdown_progress_is_bounded_and_invalid_values_are_safe(self):
        self.assertEqual(intro_countdown_progress(-2.0, 10.0), 0.0)
        self.assertEqual(intro_countdown_progress(5.0, 10.0), 0.5)
        self.assertEqual(intro_countdown_progress(15.0, 10.0), 1.0)
        self.assertEqual(intro_countdown_progress(float("nan"), 10.0), 0.0)
        self.assertEqual(intro_countdown_progress(2.0, 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
