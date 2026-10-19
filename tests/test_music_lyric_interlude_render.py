from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from api.models import TimedLyricUnit
from local.music_lyric_presentation import INTRO_COUNTDOWN_TEXT
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_lyric_visual_layers import _LayerOwner


class MusicLyricInterludeRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _LayerOwner()
        self.owner.album_art = QImage(300, 300, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor('#425766'))
        self.owner.media_position_seconds = 16.
        self.owner.media_position_source = 'local-seek'
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.owner.timed_lyrics = [(0., '作词 Lyricist：A'), (8., 'Hold\n翻译：停留'),
            (20., 'Next'), (25., 'Again'), (30., 'Last')]
        self.owner.timed_lyric_units = [(), (TimedLyricUnit(8., 12., 'Hold'),),
            (TimedLyricUnit(20., 24., 'Next'),), (TimedLyricUnit(25., 29., 'Again'),),
            (TimedLyricUnit(30., 33., 'Last'),)]
        self.owner.timed_lyric_timing_kinds = ['line', 'unit', 'unit', 'unit', 'unit']
        self.owner._music_windowed_preference = True
        self.window = MusicSurfaceWindow(self.owner)
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()
        self.window.resize(960, 540)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def render(self):
        dpr = self.window.devicePixelRatioF()
        image = QImage(round(self.window.width() * dpr), round(self.window.height() * dpr), QImage.Format.Format_RGB32)
        image.setDevicePixelRatio(dpr)
        self.window.render(image)
        return image

    def test_all_player_presentations_share_intervals_and_source_mapping(self):
        source = tuple(self.owner.timed_lyrics)
        units = tuple(self.owner.timed_lyric_units)
        for translated in (False, True):
            self.owner.enable_dual_line_lyrics = translated
            for windowed in (False, True):
                self.window._fullscreen = not windowed
                for layout in ('lyrics', 'cover_lyrics'):
                    with self.subTest(translated=translated, windowed=windowed, layout=layout):
                        self.window.set_content_layout(layout, animate=False)
                        _lines, timed = self.window._display_lyrics()
                        self.assertEqual(timed[:4], [(0., INTRO_COUNTDOWN_TEXT),
                            (8., 'Hold\n停留' if translated else 'Hold'),
                            (12., INTRO_COUNTDOWN_TEXT), (20., 'Next')])
                        self.assertEqual(self.window._display_timed_units[:4], ((), units[1], (), units[2]))
                        self.assertEqual(self.window._timing_quality_badge(_lines, timed), '真实逐字')
                        self.render()
        self.assertEqual(tuple(self.owner.timed_lyrics), source)
        self.assertEqual(tuple(self.owner.timed_lyric_units), units)

    def test_middle_dots_render_from_actual_position_through_pause_and_seeks(self):
        self.owner.media_is_playing = False
        self.window.set_content_layout('lyrics', animate=False)
        for sample, (position, expected) in enumerate(((12., 0.), (16., .5), (19.999, .999875), (14., .25)), 1):
            self.owner.media_position_seconds = position
            self.owner.media_position_sampled_at = float(sample)
            self.owner.media_position_observed_seconds = position
            with self.subTest(position=position), patch.object(
                self.window, '_paint_intro_countdown', wraps=self.window._paint_intro_countdown,
            ) as dots:
                self.render()
                progresses = [call.args[3] for call in dots.call_args_list]
                self.assertTrue(any(abs(value - expected) < .001 for value in progresses), progresses)
        with patch.object(self.window, '_paint_intro_countdown', wraps=self.window._paint_intro_countdown) as dots:
            self.render()
            first = [call.args[3] for call in dots.call_args_list]
            self.render()
            second = [call.args[3] for call in dots.call_args_list][len(first):]
            self.assertEqual(first, second)

    def test_reload_is_idempotent_and_track_change_clears_old_intervals(self):
        for _ in range(3):
            self.window._display_lyrics_cache_key = None
            timed = self.window._display_lyrics()[1]
            self.assertEqual(sum(text == INTRO_COUNTDOWN_TEXT for _, text in timed), 2)
        self.owner.media_track_id = 'next-track'
        self.owner.timed_lyrics = [(0., 'Plain'), (20., 'LRC')]
        self.owner.timed_lyric_units = [(), ()]
        self.owner.timed_lyric_timing_kinds = ['line', 'line']
        self.assertEqual(self.window._display_lyrics()[1], self.owner.timed_lyrics)


if __name__ == '__main__':
    unittest.main()
