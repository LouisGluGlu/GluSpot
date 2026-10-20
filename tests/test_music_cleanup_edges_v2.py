from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QWidget

from api.models import TimedLyricUnit
from local.island_stack_visual import IslandStackVisual
from local.lyric_unit_timing import timed_unit_reading_front
from local.music_lyric_presentation import (
    INTRO_COUNTDOWN_TEXT,
    attached_parenthetical_read_end,
    present_timed_lyric_rows,
    split_display_layers,
)


class MusicCleanupEdgesV2Tests(unittest.TestCase):
    def present(self, rows, **kwargs):
        return present_timed_lyric_rows(rows, intro_countdown=False, **kwargs)

    def test_edge_notes_and_interior_production_credits_are_removed(self):
        source = [
            (0.0, "节目：开场"), (1.0, "Producer: Someone"),
            (8.0, "First sung line"), (12.0, "作词：Someone"),
            (14.0, "注记：正文中的注记"),
            (16.0, "Last sung line"), (20.0, "制作人：Someone"),
            (21.0, "备注：结束"),
        ]
        original = list(source)
        self.assertEqual(self.present(source), [source[2], source[4], source[5]])
        self.assertEqual(source, original)

    def test_numeric_colon_is_preserved_with_ascii_fullwidth_and_unicode_digits(self):
        for text in ("凌晨3:30我还醒着", "10：30", "３ ： ３０", "时刻：10:30"):
            with self.subTest(text=text):
                rows = [(0.0, text), (4.0, "Next line"), (8.0, text)]
                self.assertEqual(self.present(rows), rows)

    def test_exact_repeated_colon_row_survives_even_with_a_credit_label(self):
        for label, body in (("Music", "is my life"), ("Master", "of my heart"),
                            ("Strings", "are pulling me")):
            rows = [(0.0, f"{label}: {body}"), (4.0, "Sing again"),
                    (8.0, f"{label}： {body}")]
            with self.subTest(label=label):
                self.assertEqual(self.present(rows), rows)

    def test_repeated_speaker_label_and_known_one_off_singer_survive_at_edges(self):
        rows = [(0.0, "成龙：第一句"), (4.0, "成龙: 第二句"), (8.0, "金善喜：第三句")]
        self.assertEqual(self.present(rows, singer_names="成龙、金善喜"), rows)
        self.assertEqual(self.present(rows[:2]), rows[:2])

    def test_phrase_internal_colons_survive_even_at_song_edges(self):
        for text in ("你说：明天再见", "I remember: you were there", "所有故事最后都写着：再见"):
            with self.subTest(text=text):
                rows = [(0.0, text), (4.0, "Another line"), (8.0, text + "！")]
                self.assertEqual(self.present(rows), rows)

    def test_edge_scan_stops_at_protected_row_without_deleting_beyond_it(self):
        rows = [(0.0, "10:30"), (2.0, "备注：正文中的注记"), (4.0, "Final lyric")]
        self.assertEqual(self.present(rows), rows)

    def test_actual_colon_translation_is_kept_at_the_final_edge(self):
        rows = [(0.0, "First lyric"), (4.0, "Hello\n翻译：Love: forever")]
        presented = self.present(rows)
        self.assertEqual(presented[-1], (4.0, "Hello\nLove: forever"))
        layers = split_display_layers(presented[-1][1], source_text=rows[-1][1])
        self.assertEqual(layers.translation_text, "Love: forever")

    def test_credit_shaped_secondary_is_not_mistaken_for_a_translation(self):
        rows = [(0.0, "Shine on\n发行：Label"), (4.0, "Final line")]
        layers = split_display_layers(self.present(rows)[0][1])
        self.assertIsNone(layers.translation_text)
        self.assertEqual(layers.primary_text, "Shine on")

    def test_cleanup_precedes_countdown_and_sparse_song_classification(self):
        rows = [(0.0, "节目：开始"), (8.0, "Sung line"), (14.0, "备注：结束")]
        self.assertEqual(present_timed_lyric_rows(rows), [(0.0, INTRO_COUNTDOWN_TEXT), (8.0, "Sung line")])

    def test_deferred_credit_does_not_swap_same_time_original_and_translation(self):
        for source in ("作词：甲\nShine\n翻译：闪耀", "翻译：闪耀\n作词：甲\nShine"):
            with self.subTest(source=source):
                rows = self.present([(5.0, source), (10.0, "Next line")])
                self.assertEqual(rows[0], (5.0, "Shine\n闪耀"))
                layers = split_display_layers(rows[0][1], source_text=source)
                self.assertEqual(layers.primary_text, "Shine")
                self.assertEqual(layers.translation_text, "闪耀")

    def test_standalone_bracket_joins_previous_line_and_its_original_start(self):
        source = [(10.0, "Shine on"), (12.0, "(echo)"), (20.0, "Next line")]
        original = list(source)
        rows = self.present(source)
        self.assertEqual([start for start, _text in rows], [10.0, 20.0])
        layers = split_display_layers(rows[0][1])
        self.assertEqual(layers.primary_text, "Shine on")
        self.assertEqual(layers.original_primary_text, "Shine on")
        self.assertEqual(layers.parenthetical_text, "echo")
        self.assertEqual(source, original)

    def test_standalone_bracket_translation_stays_with_bracket_and_main_translation(self):
        source = [(10.0, "Shine on\n继续闪耀"), (12.0, "(echo)\n翻译：回声"), (20.0, "Next line")]
        rows = self.present(source)
        layers = split_display_layers(rows[0][1], source_text=source[0][1])
        self.assertEqual(layers.primary_text, "Shine on")
        self.assertEqual(layers.translation_text, "继续闪耀")
        self.assertEqual(layers.parenthetical_text, "echo")
        self.assertEqual(layers.parenthetical_translation_text, "回声")

    def test_standalone_bracket_translation_cannot_steal_absent_main_translation(self):
        for translated in ("回声", "response"):
            with self.subTest(translated=translated):
                source = [(10.0, "Shine on"), (12.0, "(echo)\n翻译：" + translated), (20.0, "Next line")]
                layers = split_display_layers(self.present(source)[0][1], source_text=source[0][1])
                self.assertIsNone(layers.translation_text)
                self.assertEqual(layers.parenthetical_text, "echo")
                self.assertEqual(layers.parenthetical_translation_text, translated)

    def test_hidden_translation_does_not_leak_into_attached_bracket(self):
        source = [(10.0, "Shine on\n继续闪耀"), (12.0, "(echo)\n翻译：回声"), (20.0, "Next line")]
        layers = split_display_layers(self.present(source, show_translation=False)[0][1], show_translation=False)
        self.assertEqual(layers.primary_text, "Shine on")
        self.assertEqual(layers.parenthetical_text, "echo")
        self.assertIsNone(layers.translation_text)
        self.assertIsNone(layers.parenthetical_translation_text)

    def test_consecutive_bracket_rows_attach_without_new_timeline_entries(self):
        rows = self.present([(0.0, "I sing"), (1.0, "(echo)"), (2.0, "（轻声）"), (4.0, "Next")])
        self.assertEqual(len(rows), 2)
        layers = split_display_layers(rows[0][1])
        self.assertEqual(layers.primary_text, "I sing")
        self.assertEqual(set(layers.parenthetical_text.split()), {"echo", "轻声"})

    def test_orphan_bracket_without_previous_original_is_not_dropped(self):
        rows = [(0.0, "(opening)"), (4.0, "First sung line")]
        self.assertEqual(self.present(rows), rows)

    def test_nested_or_metadata_parentheses_are_not_reassigned_as_vocals(self):
        rows = [(0.0, "First sung line"), (4.0, "((nested))"), (8.0, "(feat. Guest)")]
        self.assertEqual(self.present(rows), rows)

    def test_appending_bracket_preserves_primary_source_offsets_and_real_unit_clock(self):
        source = [(10.0, "I (love) love"), (12.0, "(echo)\n翻译：回声"), (20.0, "Next line")]
        units = tuple(SimpleNamespace(start_seconds=start, end_seconds=end, text=word) for start, end, word in (
            (10.0, 10.5, "I "), (10.5, 11.0, "(love) "), (11.0, 12.0, "love"),
        ))
        before = split_display_layers(source[0][1])
        after = split_display_layers(self.present(source)[0][1], source_text=source[0][1])
        self.assertEqual(after.original_primary_text, before.original_primary_text)
        self.assertEqual(after.primary_source_offsets, before.primary_source_offsets)
        self.assertEqual(after.primary_text, before.primary_text)
        for position in (10.2, 10.7, 11.3, 12.0):
            with self.subTest(position=position):
                self.assertEqual(
                    timed_unit_reading_front(after.original_primary_text, units, position, range(len(after.original_primary_text) + 1)),
                    timed_unit_reading_front(before.original_primary_text, units, position, range(len(before.original_primary_text) + 1)),
                )

    def test_representing_attached_annotations_keeps_provenance_without_visible_markers(self):
        rows = self.present([(0.0, "I sing"), (1.0, "(echo)\n翻译：回声"), (4.0, "Next")])
        again = self.present(rows)
        for candidate in (rows, again):
            layers = split_display_layers(candidate[0][1])
            self.assertEqual(layers.primary_text, "I sing")
            self.assertEqual(layers.parenthetical_text, "echo")
            self.assertEqual(layers.parenthetical_translation_text, "回声")
            self.assertIsNone(layers.translation_text)


class MusicCleanupSurfaceMappingV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_real_surface_keeps_native_units_and_exact_primary_when_attaching_bracket(self):
        from local.music_surface_window import MusicSurfaceWindow

        owner = QWidget()
        owner._music_windowed_preference = False
        owner.track_title = "Native source mapping"
        owner.artist = "Solo"
        owner.media_track_id = "native-bracket-source-mapping"
        owner.enable_dual_line_lyrics = True
        owner.music_intro_cleanup_enabled = False
        owner.music_surface_reading_strategy = "gold"
        original = "I (love) love"
        units = tuple(SimpleNamespace(start_seconds=start, end_seconds=end, text=word) for start, end, word in (
            (10.0, 10.5, "I "), (10.5, 11.0, "(love) "), (11.0, 12.0, "love"),
        ))
        owner.timed_lyrics = [(10.0, original + "\n翻译：我爱你"), (12.0, "(echo)\n翻译：回声"),
                              (20.0, "Next line"), (25.0, "Last line")]
        original_rows = list(owner.timed_lyrics)
        owner.timed_lyric_units = [units, (), (), ()]
        owner.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]
        window = MusicSurfaceWindow(owner)
        try:
            lines, timed = window._display_lyrics()
            layers = window._visual_lyric_layers_for_rows(lines, timed, outgoing=False)
            self.assertEqual(timed[0], (0.0, INTRO_COUNTDOWN_TEXT))
            self.assertEqual(lines[0], INTRO_COUNTDOWN_TEXT)
            self.assertEqual(window._display_timed_units[0], ())
            self.assertEqual(window._display_timed_kinds[0], "line")
            self.assertEqual([start for start, _text in timed[1:]], [10.0, 20.0, 25.0])
            # Mutable foreign units are detached in the worker snapshot;
            # source coordinates and provider containers remain unchanged.
            self.assertEqual(tuple((u.start_seconds, u.end_seconds, u.text)
                                   for u in window._display_timed_units[1]),
                             tuple((u.start_seconds, u.end_seconds, u.text) for u in units))
            self.assertIs(owner.timed_lyric_units[0], units)
            self.assertEqual(window._display_timed_kinds[1], "unit")
            self.assertEqual(window._display_timed_read_ends, (None, 12.0, None, None))
            self.assertEqual(window._display_raw_source_texts[1], original_rows[0][1])
            self.assertEqual(layers[1].original_primary_text, original)
            self.assertEqual(layers[1].primary_text, "I love")
            self.assertEqual(layers[1].translation_text, "我爱你")
            self.assertEqual(layers[1].parenthetical_text, "love echo")
            self.assertEqual(layers[1].parenthetical_translation_text, "回声")
            self.assertEqual(window._visible_primary_source_offsets(timed[1][1], layers[1]),
                             split_display_layers(original).primary_source_offsets)
            self.assertEqual(owner.timed_lyrics, original_rows)
        finally:
            window.shutdown()
            owner.close()

    def _boundary_owner(self, units, *, mixed=False):
        owner = QWidget()
        owner._music_windowed_preference = False
        owner.track_title = "Bracket read boundary"
        owner.artist = "Solo"
        owner.media_track_id = "bracket-read-boundary"
        owner.enable_dual_line_lyrics = True
        owner.music_intro_cleanup_enabled = False
        owner.music_surface_reading_strategy = "gold"
        owner.music_sparse_highlight_seconds_per_unit = 100.0
        owner.media_is_playing = True
        owner.media_duration_seconds = 18.0
        owner.media_position_seconds = 0.0
        owner.media_position_source = "local-seek"
        owner.timed_lyrics = [(0.0, "春風"), (2.0, "(echo)\n翻译：回声"), (6.0, "Next"),
                              (10.0, "Third"), (14.0, "Last")]
        next_units = (TimedLyricUnit(6.0, 6.5, "Next"),) if mixed else ()
        owner.timed_lyric_units = [units, (), next_units, (), ()]
        owner.timed_lyric_timing_kinds = ["unit" if units else "line", "line",
                                        "unit" if mixed else "line", "line", "line"]
        owner._estimated_media_position = lambda _now: owner.media_position_seconds
        owner._run_background = lambda _name, callback: callback()
        return owner

    def _sample_boundary(self, window, owner, now, position):
        owner.media_position_seconds = position
        owner.media_position_sampled_at = now
        ink = {}

        def capture(_painter, _row, text, _font, _layout, progress, **_kwargs):
            ink[text] = progress

        with patch("local.music_surface_window.time.monotonic", return_value=now), patch.object(
            window, "_paint_wrapped_lifted_lyric", side_effect=capture,
        ), patch("local.music_surface_window.timed_unit_reading_front", wraps=timed_unit_reading_front) as fronts:
            window.render(QImage(960, 540, QImage.Format.Format_RGB32))
        return ink, fronts.call_args_list

    def test_missing_last_unit_end_uses_bracket_start_and_bracket_progress_uses_same_clock(self):
        from local.music_surface_window import MusicSurfaceWindow

        units = (TimedLyricUnit(0.0, 1.0, "春"), TimedLyricUnit(1.0, None, "風"))
        owner = self._boundary_owner(units)
        window = MusicSurfaceWindow(owner)
        window.resize(960, 540)
        try:
            self._sample_boundary(window, owner, 100.0, 1.0)
            ink, fronts = self._sample_boundary(window, owner, 100.7, 1.8)
            used_ends = [call.kwargs["line_end"] for call in fronts
                         if call.args[0] == "春風" and "line_end" in call.kwargs]
            self.assertTrue(used_ends)
            self.assertEqual(set(used_ends), {2.0})
            self.assertAlmostEqual(ink["echo"], 0.5)
            self.assertAlmostEqual(ink["回声"], 0.5)
            self.assertEqual(owner.timed_lyric_units[0], units)
            self.assertIsNone(units[-1].end_seconds)
        finally:
            window.shutdown()
            owner.close()

    def test_estimated_interval_retains_bracket_boundary_in_pure_gold_and_mixed_songs(self):
        from local.music_surface_window import MusicSurfaceWindow
        from local.lyric_unit_timing import resolve_display_row_timing

        # Each line now uses its own display estimate, regardless of whether
        # another row in the song supplies genuine words. The attached raw
        # backing-vocal onset remains the bound before preview is applied.
        shared = resolve_display_row_timing("春風", start=0., next_start=2.)
        expected = shared.reading_progress(.9)
        self.assertAlmostEqual(expected, .9)
        for mixed in (False, True):
            with self.subTest(mixed=mixed):
                owner = self._boundary_owner((), mixed=mixed)
                window = MusicSurfaceWindow(owner)
                window.resize(960, 540)
                try:
                    self._sample_boundary(window, owner, 100.0, 0.0)
                    ink, _fronts = self._sample_boundary(window, owner, 100.7, 0.9)
                    self.assertAlmostEqual(ink["春風"], expected)
                    self.assertAlmostEqual(ink["echo"], 0.5)
                    self.assertEqual(window._display_timed_read_ends[0], 2.0)
                    self.assertEqual(owner.timed_lyrics[1][0], 2.0)
                    self.assertEqual(window._display_row_timings[0].end_seconds, shared.end_seconds)
                finally:
                    window.shutdown()
                    owner.close()

    def test_explicit_final_unit_end_remains_authoritative_after_bracket_attachment(self):
        from local.music_surface_window import MusicSurfaceWindow

        units = (TimedLyricUnit(0.0, 1.0, "春"), TimedLyricUnit(1.0, 2.5, "風"))
        owner = self._boundary_owner(units)
        window = MusicSurfaceWindow(owner)
        window.resize(960, 540)
        try:
            self._sample_boundary(window, owner, 100.0, 1.0)
            _ink, calls = self._sample_boundary(window, owner, 100.7, 1.8)
            source_call = next(call for call in calls if call.args[0] == "春風" and call.kwargs.get("line_end") == 2.0)
            front = timed_unit_reading_front(*source_call.args, **source_call.kwargs)
            self.assertAlmostEqual(front, 1.0 + 0.8 / 1.5)
            self.assertEqual(units[-1].end_seconds, 2.5)
        finally:
            window.shutdown()
            owner.close()

    def test_duet_group_keeps_its_existing_shared_read_boundary(self):
        from local.music_surface_window import MusicSurfaceWindow

        owner = self._boundary_owner(())
        owner.artist = "薛之谦、刘惜君"
        first = (TimedLyricUnit(0.0, 0.0, "薛："), TimedLyricUnit(0.1, 1.0, "春"), TimedLyricUnit(1.0, None, "風"))
        second = (TimedLyricUnit(0.0, 0.0, "刘："), TimedLyricUnit(0.1, 1.0, "秋"), TimedLyricUnit(1.0, None, "雨"))
        owner.timed_lyrics = [(0.0, "薛：春風"), (0.0, "刘：秋雨"), (2.0, "(echo)"), (6.0, "Next"), (10.0, "Last")]
        owner.timed_lyric_units = [first, second, (), (), ()]
        owner.timed_lyric_timing_kinds = ["unit", "unit", "line", "line", "line"]
        window = MusicSurfaceWindow(owner)
        window.resize(960, 540)
        try:
            self._sample_boundary(window, owner, 100.0, 1.0)
            _ink, calls = self._sample_boundary(window, owner, 100.7, 1.8)
            ends = [call.kwargs["line_end"] for call in calls
                    if call.args[0] in {"薛：春風", "刘：秋雨"} and "line_end" in call.kwargs]
            self.assertTrue(ends)
            self.assertEqual(set(ends), {6.0})
            self.assertEqual(window._display_timed_read_ends[:2], (None, 2.0))
            self.assertIsNone(first[-1].end_seconds)
            self.assertIsNone(second[-1].end_seconds)
        finally:
            window.shutdown()
            owner.close()


class MusicAnnotationBoundaryAuditV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _island(self, *, units=True, mixed=False):
        island = IslandStackVisual.__new__(IslandStackVisual)
        island.track_title = "Source boundary audit"
        island.media_track_id = "source-boundary-audit"
        island.artist = "Solo"
        island.enable_dual_line_lyrics = True
        island.music_intro_cleanup_enabled = False
        island.music_surface_reading_strategy = "gold"
        island.lyric_flow_style = "wave_stream"
        island.media_is_playing = True
        island.lyric_index = 0
        island.lyric_changed_at = 100.0
        island.lyric_last_phase_index = -1
        island.lyric_last_phase_progress = 0.0
        island.lyric_last_phase_at = 100.0
        island.timed_lyrics = [(0.0, "春風"), (2.0, "(echo)\n翻译：回声"), (6.0, "Next")]
        island.lyrics = [text for _start, text in island.timed_lyrics]
        native = (TimedLyricUnit(0.0, 1.0, "春"), TimedLyricUnit(1.0, None, "風")) if units else ()
        next_units = (TimedLyricUnit(6.0, 6.5, "Next"),) if mixed else ()
        island.timed_lyric_units = [native, (), next_units]
        island.timed_lyric_timing_kinds = ["unit" if units else "line", "line", "unit" if mixed else "line"]
        island._effective_lyric_offset_seconds = lambda: 0.0
        island._estimated_media_position = lambda _now: 1.5
        return island

    def test_island_keeps_raw_successor_boundary_after_presentation_merges_bracket(self):
        island = self._island()
        original = list(island.timed_lyrics)
        self.assertEqual(len(present_timed_lyric_rows(original, intro_countdown=False)), 2)
        phase = island._lyric_phase(101.5)
        self.assertEqual(phase["timed_line_end"], 2.0)
        self.assertAlmostEqual(phase["progress"], 0.75)
        self.assertEqual(island.timed_lyrics, original)
        self.assertIsNone(island.timed_lyric_units[0][-1].end_seconds)

    def test_island_mixed_estimation_uses_original_two_second_interval(self):
        island = self._island(units=False, mixed=True)
        island._estimated_media_position = lambda _now: 0.5
        phase = island._lyric_phase(100.5)
        self.assertAlmostEqual(phase["progress"], 0.5)
        self.assertEqual(island.timed_lyrics[1][0], 2.0)

    def test_protected_credit_shaped_original_retains_island_unit_classification(self):
        island = self._island()
        sung = "Music: is my life"
        units = tuple(TimedLyricUnit(index * 0.2, (index + 1) * 0.2, char)
                      for index, char in enumerate(sung))
        island.timed_lyrics = [(0.0, sung), (4.0, "Another line"), (8.0, sung)]
        island.timed_lyric_units = [units, (), units]
        island.timed_lyric_timing_kinds = ["unit", "line", "unit"]
        self.assertTrue(island._island_song_has_genuine_unit_timing())

    def test_repeated_production_credit_cannot_classify_the_song_as_unit_timed(self):
        island = self._island()
        credit = "作词：Someone"
        units = tuple(TimedLyricUnit(index * 0.2, (index + 1) * 0.2, char)
                      for index, char in enumerate(credit))
        source = [(0.0, credit), (4.0, "Another line"), (8.0, credit)]
        island.timed_lyrics = list(source)
        island.timed_lyric_units = [units, (), units]
        island.timed_lyric_timing_kinds = ["unit", "line", "unit"]
        self.assertFalse(island._island_song_has_genuine_unit_timing())
        self.assertEqual(island.timed_lyrics, source)
        self.assertEqual(island.timed_lyric_units, [units, (), units])

    def test_multiple_attached_brackets_preserve_the_first_source_boundary(self):
        rows = [(0.0, "春風"), (2.0, "(echo)"), (3.0, "(again)"), (6.0, "Next")]
        presented = present_timed_lyric_rows(rows, intro_countdown=False)
        self.assertEqual(attached_parenthetical_read_end(presented[0][1], rows, 0.0), 2.0)

    def test_interior_credit_cleanup_or_inline_parentheses_do_not_acquire_override(self):
        rows = [(0.0, "春風（轻声）"), (2.0, "备注：正文中的注记"), (6.0, "Next")]
        presented = present_timed_lyric_rows(rows, intro_countdown=False)
        self.assertIsNone(attached_parenthetical_read_end(presented[0][1], rows, 0.0))


if __name__ == "__main__":
    unittest.main()
