from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.models import TimedLyricUnit
from local import music_lyric_reading as reading
from local.lyric_unit_timing import resolve_display_row_timing
from local.music_lyric_reading import LyricReadingRenderer, parenthetical_display_ranges


class IslandParentheticalHighlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.renderer = LyricReadingRenderer()
        self.font = QFont('Arial')
        self.font.setPixelSize(24)

    def paint(self, text, progress=.5, *, enabled=True, dpr=1., **options):
        image = QImage(round(900 * dpr), round(120 * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(0)
        with QPainter(image) as painter:
            self.renderer.paint(painter, QRectF(0, 15, 900, 90), 25., text, self.font,
                                progress, QColor(95, 95, 95), QColor('white'),
                                highlight_parentheticals=enabled, **options)
        return image

    def local_middle(self, text):
        first, last = parenthetical_display_ranges(text)[0]
        run = self.renderer.run(text, self.font)
        return (run.char_boundaries[first] + run.char_boundaries[last]) * .5 / run.width

    def test_only_shared_valid_non_nested_vocals_become_marks(self):
        text = 'Main (echo) （和声） (feat. Guest) ((nested)) （outer(inner)） (Verse 1:) ( ) (bad） (next)'
        ranges = parenthetical_display_ranges(text)
        self.assertEqual([text[first:last] for first, last in ranges], ['echo', '和声', 'next'])
        self.assertEqual(text, 'Main (echo) （和声） (feat. Guest) ((nested)) （outer(inner)） (Verse 1:) ( ) (bad） (next)')

    def test_every_bracket_grapheme_gets_bloom_without_sparse_duration_gate(self):
        text = 'A (echo) Z'
        progress = self.local_middle(text)
        with patch.object(reading, 'sparse_glyph_frame', wraps=reading.sparse_glyph_frame) as state:
            self.paint(text, progress)
        self.assertEqual([call.args[1] for call in state.call_args_list], list(range(4)))
        self.assertTrue(all(call.args[2] == 4 for call in state.call_args_list))
        # No seconds-per-unit threshold, source duration or independent clock
        # enters this display mark; fast and slow rows share the same API.
        self.assertTrue(self.renderer.halos)

    def test_main_lift_and_real_character_front_remain_identical(self):
        text = 'Main word (echo) tail'
        first, last = parenthetical_display_ranges(text)[0]
        character_front = (first + last) * .5
        calls, fronts = [], []
        original_font = self.font.toString()
        for enabled in (False, True):
            with patch.object(reading, 'continuous_read_lift', wraps=reading.continuous_read_lift) as lift, \
                    patch.object(reading, 'read_wipe_image_at_front', wraps=reading.read_wipe_image_at_front) as wipe:
                self.paint(text, .9, enabled=enabled, timed_character_front=character_front)
                calls.append(lift.call_args_list)
                fronts.append({call.kwargs['front'] for call in wipe.call_args_list})
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(fronts[0], fronts[1])
        self.assertEqual(self.font.toString(), original_font)

    def test_metadata_unmarked_text_and_finished_glow_keep_exact_ordinary_pixels(self):
        for text in ('Ordinary lyric', 'Main (feat. Guest) ((nested))', 'A (echo) tail'):
            for progress in (0., 1.):
                self.assertEqual(self.paint(text, progress, enabled=False), self.paint(text, progress))
        text = 'Main (feat. Guest) ((nested))'
        self.assertEqual(self.paint(text, .5, enabled=False), self.paint(text, .5))

    def test_combining_and_supplementary_graphemes_are_not_split(self):
        text = 'A (e\u0301 😀国) Z'
        self.paint(text, self.local_middle(text))
        _ranges, glyphs = next(iter(self.renderer.parentheticals.values()))[0]
        self.assertEqual(len(glyphs), 3)
        self.assertEqual([glyph[-2] for glyph in glyphs], [0, 1, 2])
        self.assertTrue(all(glyph[-1] == 3 for glyph in glyphs))

    def test_italic_glyph_is_shaped_with_ink_bleed_and_warm_pause_seek_reuse(self):
        self.font.setItalic(True)
        text = 'Before (fí) after'
        progress = self.local_middle(text)
        middle = self.paint(text, progress)
        key = next(iter(self.renderer.parentheticals))
        _ranges, glyphs = self.renderer.parentheticals[key][0]
        run = self.renderer.run(text, self.font)
        self.assertEqual(len(glyphs), 2)
        first, _last = parenthetical_display_ranges(text)[0]
        image, left, *_rest = glyphs[0]
        self.assertLess(left, run.char_boundaries[first])
        self.assertGreater(image.width() / image.devicePixelRatio(),
                           run.char_boundaries[first + 1] - left)
        self.assertEqual(self.paint(text, progress), middle)  # paused source time
        self.paint(text, max(.01, progress - .12))  # seek backward
        self.assertEqual(self.paint(text, progress), middle)
        self.assertEqual(len(self.renderer.parentheticals), 1)

    def test_existing_whole_row_mode_has_no_new_motion_or_clock(self):
        for progress in (.1, .8):
            self.assertEqual(self.paint('A (echo) tail', progress, enabled=False, whole_row=True),
                             self.paint('A (echo) tail', progress, whole_row=True))

    def test_bidirectional_row_keeps_ordinary_renderer_until_front_is_supported(self):
        text = 'Left (echo) שלום Right'
        self.assertEqual(self.paint(text, .5, enabled=False), self.paint(text, .5))

    def test_dpr_caches_are_bounded_by_entries_and_actual_bytes(self):
        self.renderer.PARENTHETICAL_CACHE_MAX_ENTRIES = 2
        self.renderer.PARENTHETICAL_CACHE_MAX_BYTES = 100000
        self.renderer.HALO_CACHE_MAX_ENTRIES = 2
        self.renderer.HALO_CACHE_MAX_BYTES = 100000
        for dpr in (1., 2.):
            for index in range(6):
                text = f'Main {index} (echo{index}) tail'
                self.paint(text, self.local_middle(text), dpr=dpr)
                self.assertLessEqual(len(self.renderer.parentheticals), 2)
                self.assertLessEqual(len(self.renderer.halos), 2)
                self.assertLessEqual(self.renderer.parenthetical_bytes, 100000)
                self.assertLessEqual(self.renderer.halo_bytes, 100000)
                self.assertEqual(self.renderer.parenthetical_bytes,
                                 sum(size for _result, size in self.renderer.parentheticals.values()))
                self.assertEqual(self.renderer.halo_bytes,
                                 sum(image.sizeInBytes() for image, _padding in self.renderer.halos.values()))

    def test_island_glue_enables_marks_without_rewriting_the_source_row(self):
        from tests import test_music_lyric_reading as fixtures
        visual = fixtures.MusicLyricReadingTests._visual()
        text = 'Main (echo) tail'
        layout = visual._lyric_layout(text, 18, True)
        image = QImage(900, 120, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        with patch.object(visual._wave_reading_renderer, 'paint') as paint:
            with QPainter(image) as painter:
                visual._paint_wave_stream_lyric_chunks(painter, QRectF(0, 0, 900, 120), 25., layout,
                                                       18, True, QColor('gray'), QColor('white'), .4, 1.)
        self.assertEqual(paint.call_args.args[3], text)
        self.assertTrue(paint.call_args.kwargs['highlight_parentheticals'])

    def test_whole_block_glue_preserves_shared_progress_and_both_source_rows(self):
        from tests import test_music_lyric_reading as fixtures

        visual = fixtures.MusicLyricReadingTests._visual()
        rows = (
            (QRectF(0, 0, 600, 45), 'Lead (echo)', 18, True,
             QColor('gray'), QColor('white'), None),
            (QRectF(0, 45, 600, 45), '翻译（和声）', 12, False,
             QColor('gray'), QColor('white'), 100),
        )
        phase = {'whole_block_progress': .42}
        image = QImage(600, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        with patch.object(visual._wave_reading_renderer, 'paint_whole_block') as block:
            with QPainter(image) as painter:
                visual._paint_wave_stream_whole_block(painter, rows, phase)
        self.assertEqual(block.call_args.args[2], .42)
        self.assertEqual([row[2] for row in block.call_args.args[1]],
                         ['Lead (echo)', '翻译（和声）'])
        self.assertTrue(block.call_args.kwargs['highlight_parentheticals'])
        self.assertEqual(phase, {'whole_block_progress': .42})

    def test_parenthetical_source_anchors_pause_and_seek_match_every_island_style(self):
        from tests import test_music_lyric_reading as fixtures

        text = 'Hello (echo) world'
        source_units = (TimedLyricUnit(1., 3., 'Hello '),
                        TimedLyricUnit(3., 5., '(echo) '),
                        TimedLyricUnit(5., 7., 'world'))
        expected = resolve_display_row_timing(text, source_units, 'unit', start=1., next_start=9.)
        for style in ('wave_stream', 'gelv_songscatter', 'ember_fade'):
            for strategy in ('gold_reading', 'low_perception'):
                with self.subTest(style=style, strategy=strategy):
                    visual = fixtures.MusicLyricReadingTests._visual()
                    visual.lyric_flow_style = style
                    visual.music_surface_reading_strategy = strategy
                    visual.media_track_id = 'parenthetical-source-anchors'
                    visual.timed_lyrics = [(1., text), (9., 'Next')]
                    visual.timed_lyric_units = [source_units, ()]
                    visual.timed_lyric_timing_kinds = ['unit', 'line']
                    visual.lyric_index = 0
                    visual.lyric_changed_at = 100.
                    visual.media_is_playing = True
                    visual._effective_lyric_offset_seconds = lambda: 0.
                    position = [4.]
                    visual._stable_lyric_source_position = lambda _now: position[0]
                    phase = visual._lyric_phase(103.)
                    self.assertEqual(phase['timed_units'], expected.display_units)
                    self.assertEqual(phase['progress'], expected.reading_progress(4.))
                    self.assertNotIn('whole_block_progress', phase)
                    visual.media_is_playing = False
                    self.assertEqual(visual._lyric_phase(120.)['progress'], phase['progress'])
                    position[0] = 2.
                    visual.media_position_source = 'local-seek'
                    self.assertEqual(visual._lyric_phase(121.)['progress'], expected.reading_progress(2.))
                    self.assertIs(visual.timed_lyric_units[0], source_units)


if __name__ == '__main__':
    unittest.main()
