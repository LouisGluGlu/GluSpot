"""The actual collapsed/expanded painters retain advancing word ink."""
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.models import TimedLyricUnit
from api.providers import EmptyIslandDataProvider
from local.island_stack_visual import IslandStackVisual


class IslandKaraokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        IslandStackVisual.load_embedded_lyric_fonts()

    def setUp(self):
        self.visual = IslandStackVisual(EmptyIslandDataProvider())
        w = self.visual
        w.timer.stop()
        w._lyric_presentation_store.close()
        w._lyric_presentation_store = None
        w.location_mode, w.finder_mode = 'free', False
        w.music_playing = w.media_is_playing = True
        w.music_auto_detect = False
        w.media_track_id = 't254-word-clock'
        w.track_title, w.artist = 'Reading regression', 'GluSpot'
        w.media_duration_seconds = 30.
        w.media_position_seconds = w.media_position_observed_seconds = 0.
        w.media_position_sync_at = w.media_position_sampled_at = 100.
        w.media_position_source = 'local-seek'
        w.lyrics = ['Shine softly tonight', 'Keep the melody moving', 'Following line']
        w.timed_lyrics = [(0., w.lyrics[0]), (8., w.lyrics[1]), (16., w.lyrics[2])]
        w.timed_lyric_units = [(TimedLyricUnit(0., 2., 'Shine '),
                                TimedLyricUnit(2., 4., 'softly '),
                                TimedLyricUnit(4., 6., 'tonight')), (), ()]
        w.timed_lyric_timing_kinds = ['unit', 'line', 'line']
        w.lyric_index = w.prev_lyric_index = 0
        w.lyric_changed_at = 90.
        w.music_surface_transition_started_at = 0.
        w.enable_dual_line_lyrics = False
        w.album_art = QImage(80, 80, QImage.Format.Format_RGB32)
        w.album_art.fill(QColor('#087d86'))
        w.album_color, w.secondary_color = QColor('#087d86'), QColor('#184f72')

    def tearDown(self):
        self.visual.close()
        self.visual.deleteLater()
        self.app.processEvents()

    def render(self, now, dpr, *, suppress_reading=False):
        w = self.visual
        image = QImage(580 * dpr, 200 * dpr, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        rect = QRectF(10., 10., 550., 156. if w.expanded else 48.)
        renderer = w._wave_reading_renderer
        with patch('local.island_stack_visual.time.monotonic', return_value=now), \
                patch('local.island_stack_visual.system_dark_mode', return_value=False), \
                patch.object(renderer, 'paint', wraps=None if suppress_reading else renderer.paint) as reading:
            w._island_lyric_frame_at, w._island_lyric_frame_phase = now, None
            w._sync_timed_lyrics(now)
            with QPainter(image) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                w._paint_music(painter, rect, w.expanded)
            w._island_lyric_frame_at, w._island_lyric_frame_phase = None, None
        return image, reading.call_args_list

    @staticmethod
    def pixels(image):
        return np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine()).copy()

    def test_real_word_front_paints_and_advances_in_every_background_and_pose(self):
        for style in ('sea_level', 'solid_album', 'system'):
            for lyric_style in ('wave_stream', 'gelv_songscatter'):
                for expanded in (False, True):
                    for dpr in (1, 2):
                        with self.subTest(style=style, lyric_style=lyric_style, expanded=expanded, dpr=dpr):
                            w = self.visual
                            w.music_ambience_style, w.lyric_flow_style = style, lyric_style
                            w.expanded, w.expansion = expanded, float(expanded)
                            fronts, ink = [], []
                            for now in (100.5, 103.):
                                image, calls = self.render(now, dpr)
                                active = [c for c in calls if c.args[3] == w.lyrics[0]]
                                self.assertTrue(active, 'active lyric bypassed word renderer')
                                front = active[0].kwargs['timed_character_front']
                                self.assertGreater(front, 0.)
                                self.assertLess(front, len(w.lyrics[0]))
                                self.assertNotEqual(active[0].args[6], active[0].args[7])
                                fronts.append(front)
                                without, _ = self.render(now, dpr, suppress_reading=True)
                                difference = self.pixels(image).astype(np.int16) - self.pixels(without)
                                self.assertGreater(np.count_nonzero(difference), 100)
                                ink.append(difference)
                            self.assertGreater(fronts[1], fronts[0])
                            self.assertGreater(np.count_nonzero(ink[0] != ink[1]), 100)

    def test_compact_sea_holds_the_lyric_phase_with_its_text_during_shell_growth(self):
        w = self.visual
        w.music_ambience_style, w.expanded = 'sea_level', False
        phase = {'progress': .35, 'timed_primary_text': 'Previous lyric',
                 'timed_units': (TimedLyricUnit(0., 2., 'Previous lyric'),),
                 'timed_position': .7, 'timed_character_front': 4.9}
        w._compact_held_lyric = (('Previous lyric',), phase)
        w._compact_shell_waiting = True
        _, calls = self.render(103., 1)
        self.assertEqual(calls[0].args[3], 'Previous lyric')
        self.assertEqual(calls[0].kwargs['timed_character_front'], 4.9)


if __name__ == '__main__':
    unittest.main()
