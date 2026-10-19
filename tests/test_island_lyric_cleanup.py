from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor

from local.island_stack_visual import IslandStackVisual
from local.music_lyric_presentation import present_timed_lyric_rows


class IslandLyricCleanupTests(unittest.TestCase):
    @staticmethod
    def visual(rows=(), *, translated=True):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyrics = list(rows)
        visual.timed_lyrics = [(index * 4., text) for index, text in enumerate(rows)]
        visual.artist = "陈甲、李乙"
        visual.track_title = "Fixture song"
        visual.no_lyrics_text = "暂无歌词"
        visual.lyric_index = 0
        visual.enable_dual_line_lyrics = translated
        visual.location_mode = "free"
        visual.top_notch_height_mode = "standard"
        visual._empty_music_text_active = lambda: False
        return visual

    def test_width_cleanup_keeps_role_words_inside_real_lyrics(self):
        visual = self.visual()
        for text in ("鼓起勇气：向前走", "他说：我爱你", "听见吉他和鼓的声音", "在和声里想你", "（oh）"):
            with self.subTest(text=text):
                self.assertEqual(visual._clean_lyric_for_width(text), text)
                self.assertEqual(visual._lyric_display_lines(text), [text])

    def test_shared_block_cleanup_retains_raw_source_indexes(self):
        rows = ["作词 Lyricist：A", "作曲 Composer：B", "---",
                "斯瓦希里语顾问 Swahili Language Consultant：C", "绞弦琴 Hurdy Gurdy：D",
                "陈甲：第一句", "李乙：第二句"]
        visual = self.visual(rows)
        before_timed = tuple(visual.timed_lyrics)
        cleaned = visual._island_cleaned_lyric_rows()
        self.assertEqual(cleaned, ("", "", "", "", "", rows[5], rows[6]))
        self.assertEqual(visual._compact_lyric_lines(), list(cleaned))
        self.assertEqual(visual.lyrics, rows)
        self.assertEqual(tuple(visual.timed_lyrics), before_timed)
        self.assertEqual(visual._lyric_display_lines(rows[3]), [])

    def test_cleaned_credits_are_not_revived_by_compact_fallback(self):
        rows = ["作词：A", "First lyric", "制作人：B", "Second lyric"]
        visual = self.visual(rows)
        self.assertEqual(visual._compact_lyric_lines(), ["", "First lyric", "", "Second lyric"])
        for index in (0, 2):
            visual.lyric_index = index
            self.assertEqual(visual._current_lyric_display_lines(), [])

    def test_metadata_only_is_no_lyrics_and_not_instrumental(self):
        header = '{"t":-1,"c":[{"tx":"作词：A"}]}'
        for rows in (["作曲：A"], [header], [header, "作词 Lyricist：A"]):
            with self.subTest(rows=rows):
                visual = self.visual(rows)
                self.assertFalse(visual._is_instrumental_track())
                self.assertEqual(visual._compact_lyric_lines(), ["暂无歌词"] * len(rows))
                self.assertEqual(visual._compact_music_text(), "暂无歌词")
                self.assertEqual(visual._current_lyric_display_lines(), ["暂无歌词"])
                self.assertEqual(visual._gelv_display_lines(), ["暂无歌词"])
                self.assertEqual(visual._gelv_primary_text_for_index(0), "暂无歌词")
                self.assertFalse(visual._gelv_current_dual_line())
                self.assertFalse(visual._gelv_current_mixed_language_line())

    def test_expanded_metadata_only_uses_the_existing_miss_painter(self):
        visual = self.visual(["作词：A", "作曲：B"])
        visual._should_paint_album_island = lambda: False
        visual._paint_island_lyric_gap = lambda *_args: False
        visual._music_lyric_text_color = lambda _alpha: QColor(255, 255, 255)
        painter = Mock()
        rect = QRectF(0., 0., 420., 120.)
        with patch.object(visual, "_paint_scrolling_text") as paint:
            visual._paint_lyric_viewport(painter, rect)
        self.assertEqual(paint.call_args.args[2], "暂无歌词")
        self.assertEqual(visual.lyrics, ["作词：A", "作曲：B"])
        painter.restore.assert_called_once()

    def test_metadata_miss_uses_existing_album_preference_and_custom_text(self):
        visual = self.visual(["作词：A"])
        visual.no_lyrics_text = "No lyrics available"
        visual.no_lyrics_display_mode = "album_island"
        self.assertTrue(visual._should_paint_album_island())
        self.assertEqual(visual._current_lyric_display_lines(), ["No lyrics available"])
        self.assertEqual(visual._gelv_display_lines(), ["No lyrics available"])
        self.assertEqual(visual._compact_music_text(), "No lyrics available")

    def test_actual_instrumental_marker_retains_existing_behavior(self):
        visual = self.visual(["纯音乐，请欣赏"])
        self.assertTrue(visual._is_instrumental_track())
        self.assertEqual(visual._compact_lyric_lines(), ["Fixture song · 陈甲、李乙"])

    def test_no_lyrics_and_loading_are_not_instrumental(self):
        for text in ("暂无歌词", "无歌词", "查找歌词中"):
            with self.subTest(text=text):
                self.assertFalse(self.visual([text])._is_instrumental_track())

    def test_translation_toggle_rebuilds_cache_without_mutating_sources(self):
        source = ["Shine on\n翻译：继续闪耀", "君を想う\nromanization: kimi o omou"]
        visual = self.visual(source)
        visible = visual._island_cleaned_lyric_rows()
        self.assertEqual(visible, ("Shine on\n继续闪耀", "君を想う\nkimi o omou"))
        self.assertIs(visual._island_cleaned_lyric_rows(), visible)
        visual.enable_dual_line_lyrics = False
        self.assertEqual(visual._island_cleaned_lyric_rows(), ("Shine on", "君を想う"))
        self.assertEqual(visual._lyric_display_lines(source[0]), ["Shine on"])
        self.assertEqual(visual.lyrics, source)
        visual.enable_dual_line_lyrics = True
        self.assertEqual(visual._island_cleaned_lyric_rows(), visible)

    def test_ember_own_translation_toggle_does_not_change_shared_preference(self):
        source = ["stay with me tonight\n今晚请留在我身边"]
        visual = self.visual(source, translated=False)
        visual.ember_dual_line_enabled = True
        visual.ember_line_mode = "split"
        self.assertEqual(visual._island_cleaned_lyric_rows(), ("stay with me tonight",))
        self.assertEqual(visual._ember_current_display_lines(),
                         ["stay with me tonight", "今晚请留在我身边"])
        # Switching readers rebuilds the common cache under its own choice.
        self.assertEqual(visual._island_cleaned_lyric_rows(), ("stay with me tonight",))
        self.assertFalse(visual.enable_dual_line_lyrics)
        visual.location_mode = "tasker"
        self.assertEqual(visual._ember_current_display_lines(),
                         ["stay with me tonight  /  今晚请留在我身边"])
        visual.ember_dual_line_enabled = False
        self.assertEqual(visual._ember_current_display_lines(), ["stay with me tonight"])
        self.assertEqual(visual.lyrics, source)

    def test_ember_shared_cleanup_keeps_credits_removed_and_source_indexes(self):
        source = ["作词 Lyricist：A", "作曲 Composer：B", "First lyric\n翻译：第一句"]
        visual = self.visual(source, translated=False)
        visual.ember_dual_line_enabled = True
        visual.ember_line_mode = "scroll"
        self.assertEqual(visual._ember_current_text(visual_index=0), "")
        self.assertEqual(visual._ember_current_text(visual_index=2), "First lyric\n第一句")
        self.assertEqual(visual.lyric_index, 0)
        self.assertEqual(visual.lyrics, source)

    def test_standalone_background_vocal_stays_at_its_own_timed_index(self):
        source = ["Shine on", "（和声：oh）\n翻译：（回声）", "Next line"]
        visual = self.visual(source)
        self.assertEqual(visual._island_cleaned_lyric_rows(),
                         ("Shine on", "（和声：oh）\n（回声）", "Next line"))
        visual.lyric_index = 0
        self.assertEqual(visual._current_lyric_display_lines(), ["Shine on"])
        visual.lyric_index = 1
        self.assertEqual(visual._current_lyric_display_lines(), ["（和声：oh）", "（回声）"])
        self.assertEqual(visual.timed_lyrics[1][0], 4.)

    def test_arbitrary_transition_text_is_not_remapped_to_active_lyric(self):
        visual = self.visual(["Current lyric", "Next lyric"])
        visual.lyric_index = 1
        self.assertEqual(visual._lyric_display_lines("Outgoing line\n旧行译文"), ["Outgoing line", "旧行译文"])
        self.assertEqual(visual._lyric_display_lines("作词：A"), [])

    def test_in_place_source_change_and_singer_change_invalidate_display_cache(self):
        visual = self.visual(["First lyric", "Next lyric"])
        before = visual._island_cleaned_lyric_rows()
        visual.lyrics[0] = "鼓起勇气：向前走"
        self.assertNotEqual(visual._island_cleaned_lyric_rows(), before)
        visual.lyrics = ["Music：I keep singing"]
        visual.artist = "Music"
        self.assertEqual(visual._island_cleaned_lyric_rows(), ("Music：I keep singing",))
        visual.artist = "Other singer"
        self.assertEqual(visual._island_cleaned_lyric_rows(), ("",))

    def test_gelv_pure_text_paths_use_same_cleaning_without_raw_revival(self):
        visual = self.visual(["作词：A", "真正歌词\n翻译：Real lyric", "制作人：B", "Next line"])
        self.assertEqual(visual._gelv_primary_text_for_index(0), "")
        self.assertEqual(visual._gelv_display_lines(), ["真正歌词"])
        visual.lyric_index = 1
        self.assertTrue(visual._gelv_current_dual_line())
        self.assertEqual(visual._gelv_display_lines(), ["真正歌词", "Real lyric"])
        visual.enable_dual_line_lyrics = False
        self.assertFalse(visual._gelv_current_dual_line())
        self.assertEqual(visual._gelv_display_lines(), ["真正歌词"])
        self.assertEqual(visual._gelv_primary_text_for_index(2), "")

    def test_island_and_shared_presenter_choose_the_same_staff_rows(self):
        source = ["作词 Lyricist：A", "作曲 Composer：B", "罕见乐器：C", "罕见职位：D",
                  "鼓起勇气：向前走", "他说：我爱你", "Next lyric"]
        visual = self.visual(source)
        shared = present_timed_lyric_rows(list(enumerate(source)), intro_countdown=False,
                                          singer_names=visual.artist, attach_parentheticals=False)
        island = [(index, text) for index, text in enumerate(visual._island_cleaned_lyric_rows()) if text]
        self.assertEqual(island, shared)

    def test_tasker_dual_line_limit_and_nontranslation_multiline_behavior_remain(self):
        visual = self.visual(["First lyric\nSecond lyric"])
        self.assertEqual(visual._lyric_display_lines(visual.lyrics[0]), ["First lyric", "Second lyric"])
        visual.location_mode = "tasker"
        self.assertEqual(visual._lyric_display_lines(visual.lyrics[0]), ["First lyric"])
        visual.location_mode = "stick"
        self.assertEqual(visual._lyric_display_lines(visual.lyrics[0]), ["First lyric", "Second lyric"])


if __name__ == "__main__":
    unittest.main()
