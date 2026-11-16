"""Unchanged lyric geometry reuses work while retaining exact target widths."""
import unittest
from unittest.mock import patch

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from api.providers import EmptyIslandDataProvider
from local.island_stack_visual import IslandStackVisual
from local.music_lyric_preparation_store import LyricPresentationStore
from tests.test_music_lyric_preparation import ControlledTasks


class IslandIdleWorkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.provider = EmptyIslandDataProvider()
        self.owner = IslandStackVisual(self.provider)
        self.owner.timer.stop()
        self.owner.location_mode = 'free'
        self.owner.lyrics = ['Shine on tonight\n继续闪耀', 'Keep the melody moving']
        self.owner.artist = 'GluSpot'
        self.owner.enable_dual_line_lyrics = True
        self.owner._lyric_presentation_store.close()
        self.owner._lyric_presentation_store = None
        self.addCleanup(self.dispose)

    def dispose(self):
        self.owner.close()
        self.owner.deleteLater()
        self.app.processEvents()

    def measure(self, expanded=False):
        return self.owner._music_lyric_measurement_lines(self.owner.lyrics, expanded)

    def test_unchanged_song_reuses_measurements_without_exposing_mutable_cache(self):
        expected = self.measure()
        with patch.object(self.owner, '_music_lyric_measurement_text',
                          wraps=self.owner._music_lyric_measurement_text) as measure:
            actual = self.measure()
            actual.append('caller mutation')
            self.assertEqual(self.measure(), expected)
        measure.assert_not_called()

    def test_in_place_source_edit_translation_and_artist_invalidate_measurement(self):
        original = self.measure()
        self.owner.lyrics[0] = 'A replacement lyric\n另一句译文'
        edited = self.measure()
        self.assertNotEqual(edited, original)
        self.assertIn('A replacement lyric', edited)
        self.owner.enable_dual_line_lyrics = False
        self.assertNotIn('另一句译文', self.measure())
        with patch.object(self.owner, '_music_lyric_measurement_text',
                          wraps=self.owner._music_lyric_measurement_text) as measure:
            self.owner.artist = 'Different singer'
            self.measure()
        self.assertGreater(measure.call_count, 0)

    def test_pose_chunk_limit_and_taskbar_translation_rules_invalidate(self):
        self.owner.lyrics = ['春' * 45 + '\n' + '译' * 45]
        compact = self.measure(False)
        expanded = self.measure(True)
        self.assertEqual([len(x) for x in compact], [30, 30])
        self.assertEqual([len(x) for x in expanded], [36, 36])
        self.owner.location_mode = 'tasker'
        self.assertEqual(len(self.measure(False)), 1)
        self.owner.location_mode = 'free'
        self.assertEqual(self.measure(False), compact)

    def test_completed_preparation_replaces_cached_pending_or_failed_rows(self):
        tasks = ControlledTasks()
        store = LyricPresentationStore(self.owner, tasks=tasks)
        self.owner._lyric_presentation_store = store
        pending = self.measure()
        self.assertTrue(all(line == '加载歌词中' for line in pending))
        tasks.finish()
        ready = self.measure()
        self.assertIn('Shine on tonight', ready)
        self.assertNotEqual(ready, pending)
        self.owner.lyrics[0] = 'New source after failure'
        pending = self.measure()
        token, _action = tasks.actions.pop(0)
        tasks.completed.emit(token, None, 'fixture failure')
        failed = self.measure()
        self.assertNotEqual(failed, pending)
        self.assertTrue(all(line == self.owner.no_lyrics_text for line in failed))

    def test_cached_target_widths_match_original_algorithm_exactly_across_settings(self):
        def original_lines(lyrics, expanded):
            result = []
            for lyric in lyrics or []:
                cleaned = self.owner._clean_lyric_for_width(str(lyric or ''))
                if not cleaned:
                    continue
                for line in self.owner._lyric_display_lines(cleaned) or [cleaned]:
                    measured = self.owner._music_lyric_measurement_text(line, expanded)
                    if measured:
                        result.append(measured)
            return result

        self.owner.track_title = 'A song across the quiet sea'
        self.owner.lyrics += ['春' * 45 + '\n' + '译' * 45, '作词：Someone', '(oh oh)']
        for mode, style, dual, size, scale in (
                ('free', 'wave_stream', True, 18, 1.),
                ('top', 'gelv_songscatter', False, 24, .9),
                ('tasker', 'wave_stream', True, 16, 1.25)):
            self.owner.location_mode = mode
            self.owner.lyric_flow_style = style
            self.owner.enable_dual_line_lyrics = dual
            self.owner.lyric_font_size = size
            self.owner.font_shrink_scale = scale
            for expanded in (False, True):
                with self.subTest(mode=mode, expanded=expanded):
                    with patch.object(self.owner, '_music_lyric_measurement_lines', side_effect=original_lines):
                        reference = self.owner._base_music_target_width(expanded)
                    self.assertEqual(self.owner._base_music_target_width(expanded).hex(), reference.hex())
                    self.assertEqual(self.owner._base_music_target_width(expanded).hex(), reference.hex())

    def test_warm_target_width_does_not_clean_each_song_row_again(self):
        self.owner.lyrics = [f'Keep a little light along the water {i}' for i in range(100)]
        for expanded in (False, True):
            self.owner._base_music_target_width(expanded)
        with patch.object(self.owner, '_island_cleaned_lyric_rows',
                          wraps=self.owner._island_cleaned_lyric_rows) as clean:
            for expanded in (False, True, False, True):
                self.owner._base_music_target_width(expanded)
        self.assertLess(clean.call_count, 25)

    def test_width_cache_keys_actual_font_metrics_and_dpr(self):
        lines = self.measure()
        self.owner._representative_lyric_width(lines, 18, True)
        with patch.object(self.owner, '_lyric_layout', wraps=self.owner._lyric_layout) as layout:
            self.owner._representative_lyric_width(lines, 18, True)
            layout.assert_not_called()
            changed_font = QFont('monospace', 29)
            with patch.object(self.owner, '_wave_stream_font', return_value=changed_font):
                self.owner._representative_lyric_width(lines, 18, True)
            self.assertGreater(layout.call_count, 0)
            layout.reset_mock()
            old_layout = self.owner._lyric_layout(lines[0], 18, True)
            layout.reset_mock()
            with patch.object(self.owner, 'devicePixelRatioF', return_value=2.):
                self.owner._representative_lyric_width(lines, 18, True)
                self.assertIsNot(self.owner._lyric_layout(lines[0], 18, True), old_layout)
            self.assertGreater(layout.call_count, 0)

    def test_measurement_cache_remains_bounded_across_song_changes(self):
        for i in range(12):
            self.owner.lyrics = [f'Different song {i}']
            self.measure()
        self.assertLessEqual(len(self.owner._music_lyric_measurement_cache), 4)
