"""Offscreen fixture checks for the generic live surface (not live playback)."""
import ast
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(Path(__file__).parent)]
from PySide6.QtCore import QEvent, QObject, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from api.models import ColorToken, IslandVisualData, TimedLyricLine, TimedLyricUnit, TrackData
from api.providers import EmptyIslandDataProvider
from api.online_music import OnlineSong
from local.island_stack_visual import IslandStackVisual
from narrow_window import NarrowStudyWindow
from layout import NarrowLayout
from local.song_collection_controller import SongCollectionState

LINES = (TimedLyricLine(1.25, 'first words', (TimedLyricUnit(1.25, 1.8, 'first '), TimedLyricUnit(1.8, 2.4, 'words')), 'word'),
         TimedLyricLine(4.7, 'second line', (TimedLyricUnit(4.7, 5.1, 'second '), TimedLyricUnit(5.1, 6.3, 'line')), 'word'),
         TimedLyricLine(11.3, 'third', (), 'line'))

def track(**changes):
    base = TrackData('Fixture title', 'Fixture artist', (), ColorToken(40, 60, 80), ColorToken(20, 30, 40), (),
                     source_app='fixture-player', is_playing=False, duration_seconds=120,
                     position_seconds=2, timed_lyrics=LINES, track_id='fixture-1', position_source='windows-media')
    return replace(base, **changes)

def visual(item):
    return IslandVisualData(item, (), ())

class LiveChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.provider = EmptyIslandDataProvider(visual(track()))
        self.owner = IslandStackVisual(data_provider=self.provider)
        self.owner.timer.stop()
        self.window = NarrowStudyWindow(self.owner, 'B', 'left', live=True)
        self.window._timer.stop()
        self.window.study_timer.stop()
        self.window._audio_output_status_timer.stop()

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.owner.close()
        self.app.processEvents()

    def apply(self, item):
        self.provider.visual_data = visual(item)
        self.owner._apply_visual_data(self.provider.get_visual_data())

    def wait_lines(self):
        for _ in range(100):
            lines = self.window.source_lines()
            if lines:
                return lines
            QTest.qWait(10)
        self.fail("lyric preparation did not complete")

    def test_provider_lyrics_and_track_source_seek(self):
        self.apply(track())
        self.assertEqual(self.wait_lines(), LINES)
        self.apply(track(position_seconds=19, position_interactive=True))
        self.assertAlmostEqual(self.owner.media_position_seconds, 19, delta=.5)
        self.apply(track(track_id='fixture-2', source_app='other-player', position_seconds=7))
        self.assertEqual(self.owner.media_track_id, 'fixture-2')
        self.assertEqual(self.owner.media_source_app, 'other-player')
        self.assertAlmostEqual(self.owner.media_position_seconds, 7, delta=.5)
        self.assertEqual(self.wait_lines(), LINES)

    def test_tick_does_not_advance_owner_clock_synthetically(self):
        self.apply(track())
        self.owner.last_media_refresh_at = time.monotonic()
        before = self.owner.media_position_seconds
        self.window.last_tick -= 20
        self.window.tick()
        self.assertEqual(self.owner.media_position_seconds, before)

    def test_unavailable_position_keeps_transport_playing_without_lyric_advancement(self):
        self.apply(track(position_seconds=5, position_source='capture-model', is_playing=True))
        self.wait_lines()
        self.apply(track(position_seconds=5, position_source='capture-unavailable',
                         position_confidence=0, position_observed_seconds=None,
                         is_playing=True))
        frozen = self.owner.media_position_seconds
        now = time.monotonic()
        positions, indices = [], []
        with patch.object(self.owner, '_refresh_media_data', return_value=False):
            for seconds in (0, .65, 3, 30):
                with patch('narrow_window.time.monotonic', return_value=now + seconds):
                    self.window.tick()
                    positions.append(self.window.playback_position())
                    indices.append(self.window.lyric_focus()[0])
                    self.assertEqual(self.owner._estimated_media_position(now + seconds), frozen)
                    self.assertEqual(self.owner._stable_lyric_source_position(now + seconds), frozen)
        self.assertTrue(self.owner.media_is_playing)
        self.assertEqual(positions, [frozen] * 4)
        self.assertEqual(len(set(indices)), 1)

    def test_async_partial_update_bursts_stay_within_the_visible_frame_cadence(self):
        class PaintCounter(QObject):
            paints = 0

            def eventFilter(self, watched, event):
                if event.type() == QEvent.Type.Paint:
                    self.paints += 1
                return False

        self.apply(track())
        self.wait_lines()
        counter = PaintCounter(self.window)
        self.window.installEventFilter(counter)
        self.window.show()
        QTest.qWait(400)
        counter.paints = 0
        started = time.monotonic()
        for _ in range(100):
            self.window.update(QRect(0, 0, 40, 40))
            self.app.processEvents()
        QTest.qWait(500)
        elapsed = time.monotonic() - started
        self.assertGreater(self.window.diagnostic_snapshot()['noop_paints'], 3,
                           'paused duplicate samples were not coalesced')
        self.assertLessEqual(counter.paints, elapsed * 36 + 2,
                             'partial updates escaped the live full-frame cadence')

    def test_absent_queue_and_generation_guard(self):
        self.assertIsNone(self.window.queue_snapshot())
        self.assertEqual(self.window.queue_rows(), ())
        selected = []
        self.provider.select_owned_queue_index = lambda index, generation: selected.append((index, generation))
        self.window._queue_snapshot = (8, 0, ('fixture',))
        self.window._select_playback_queue(0, 7)
        self.assertEqual(selected, [])
        self.window._select_playback_queue(0, 8)
        self.assertEqual(selected, [(0, 8)])

    def test_transport_and_stale_press(self):
        self.apply(track())
        sent = []
        # Exercise the production surface dispatcher and its owner-to-provider route.
        self.provider.send_playback_command = lambda command: sent.append(command) or True
        fade = self.window.visibility.footer
        fade.start = fade.target = 1
        fade.duration = 0
        self.window.visibility.advance(time.monotonic(), True, True, False)
        point = next(group['media:next'].center() for group in self.window.glass_groups() if 'media:next' in group)
        self.window._begin_live_press(point)
        self.window._finish_live_press(point)
        for _ in range(100):
            if sent:
                break
            QTest.qWait(10)
        self.assertEqual(sent, ['next'])
        for change in ({'track_id': 'fixture-2'}, {'source_app': 'other-player'}):
            self.apply(track())
            self.window._begin_live_press(point)
            self.apply(track(**change))
            self.window._finish_live_press(point)
        QTest.qWait(50)
        self.assertEqual(sent, ['next'])
        self.assertEqual(self.window.song_index, 0)
        self.assertFalse(self.window.dj_enabled)

    def test_pointer_stops_inside_and_hides(self):
        point = QPointF(180, 300)
        event = QMouseEvent(QEvent.Type.MouseMove, point, point, Qt.MouseButton.NoButton,
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        with patch('narrow_window.time.monotonic', return_value=100):
            self.window.mouseMoveEvent(event)
            self.window.tick()
        self.assertTrue(self.window.shelf)
        with patch('narrow_window.time.monotonic', return_value=100.21):
            self.window.tick()
        self.assertFalse(self.window.shelf)
        with patch('narrow_window.time.monotonic', return_value=101.42):
            self.window.tick()
        self.assertEqual(self.window.visibility.opacity.target, 0)

    def test_show_and_close_lifecycle(self):
        for via_escape in (False, True):
            window = NarrowStudyWindow(self.owner, 'B', 'left', live=True)
            window.show()
            self.app.processEvents()
            self.assertTrue(window.isVisible())
            self.assertTrue(window._timer.isActive())
            self.assertTrue(window._audio_output_status_timer.isActive())
            self.assertTrue(window.study_timer.isActive())
            if via_escape:
                QTest.keyClick(window, Qt.Key.Key_Escape)
            else:
                self.assertTrue(window.close())
            self.app.processEvents()
            self.assertFalse(window.isVisible())
            self.assertFalse(window.study_timer.isActive())

    def test_view_timer_never_polls_owner_or_stops_shared_clock(self):
        # The owner keeps its normal clock. The view timer only coalesces paint.
        self.owner.timer.start()
        owner_timer = self.owner.timer
        with patch.object(self.owner, "_refresh_media_data") as refresh:
            self.window.tick()
            refresh.assert_not_called()
        for action, active in ((self.window.show, True), (self.window.hide, False),
                               (self.window.show, True), (self.window.showMinimized, False),
                               (self.window.showNormal, True)):
            action()
            self.app.processEvents()
            self.assertEqual(self.window.study_timer.isActive(), active)
            if active:
                self.assertTrue(self.window._timer.isActive())
                self.assertTrue(self.window._audio_output_status_timer.isActive())
            self.assertIs(self.owner.timer, owner_timer)
            self.assertTrue(owner_timer.isActive())
        self.window.close()
        self.app.processEvents()
        self.assertTrue(owner_timer.isActive())

    def test_live_focus_and_position_follow_owner_identity(self):
        self.apply(track())
        self.wait_lines()
        self.owner.lyric_last_sync_position = 2.0
        self.owner.lyric_index = 2
        self.owner.prev_lyric_index = 1
        self.owner.lyric_changed_at = time.monotonic() - 1
        self.assertEqual(self.window.playback_position(), 2.0)
        self.assertEqual(self.window.lyric_focus(), (2, 2.0))
        self.owner.lyric_index = 1
        self.owner.prev_lyric_index = 1
        self.assertEqual(self.window.lyric_focus(), (1, 1.0))

    def test_live_lyric_frame_never_advances_private_clock(self):
        self.owner.lyric_last_sync_position = 7.0
        self.owner.lyric_index = 1
        self.owner.prev_lyric_index = 0
        self.owner.lyric_changed_at = 99.0
        timed = [(0.0, 'fixture'), (100.0, 'different timing'), (200.0, 'later')]
        with patch.object(self.window._lyric_motion, 'frame') as private_frame, \
             patch.object(self.owner, '_stable_lyric_source_position') as source_clock:
            frame = self.window._lyric_frame(timed, 100.0)
            self.window._advance_lyric_animation(100.0)
            private_frame.assert_not_called()
            source_clock.assert_not_called()
        self.assertEqual(frame.active_index, self.owner.lyric_index)
        self.assertEqual(self.window._lyric_motion.last_position, 7.0)

    def test_live_seek_uses_shared_surface_implementation(self):
        from local.music_surface_window import MusicSurfaceWindow
        self.assertIs(type(self.window)._seek_to_lyric, MusicSurfaceWindow._seek_to_lyric)
        self.assertIs(self.window.owner, self.owner)
        self.assertIs(self.window.owner.data_provider, self.provider)

    def test_measured_word_seek_offset_and_gesture_fences(self):
        self.apply(track())
        self.wait_lines()
        self.owner.lyric_offset_seconds = .5
        jobs, seeks = [], []
        self.owner._run_background = lambda name, job: jobs.append(job)
        self.provider.seek_playback_position = lambda seconds, expected_track_id: seeks.append((seconds, expected_track_id)) or True
        layout = NarrowLayout(self.window.width(), self.window.height())
        self.window.show()
        self.app.processEvents()
        self.window.grab()
        self.window.study_timer.stop()
        hits = [(rect.center(), seconds) for (line, unit, fragment), (rect, seconds)
                in self.window._lyric_word_hit_rects.items() if line == 0 and unit == 1]
        self.assertTrue(hits, 'second source-timed word has no measured hit rectangle')
        point, seconds = hits[0]
        self.assertEqual(seconds, 1.8)
        self.assertEqual(self.window.live_lyric_action_at(point)[:3], ('lyric', 0, 1.8))
        self.window._begin_live_press(point)
        self.window._finish_live_press(point)
        self.assertEqual(len(jobs), 1)
        jobs.pop()()
        self.assertEqual(seeks, [(1.3, 'fixture-1')])
        self.app.processEvents()
        for change in ({'track_id': 'fixture-2'}, {'source_app': 'other-player'}):
            self.apply(track())
            self.window._begin_live_press(point)
            self.apply(track(**change))
            self.window._finish_live_press(point)
        self.assertEqual(len(jobs), 0)
        self.assertEqual(len(seeks), 1)

    def test_wrapped_primary_and_companion_increase_block_height(self):
        text = 'A long primary line with enough words to wrap across the narrow window multiple times'
        lines = (TimedLyricLine(1.25, text + '\n翻译：译文伴随歌词', (), 'line'),
                 TimedLyricLine(4.7, 'next line', (), 'line'))
        self.owner.enable_dual_line_lyrics = True
        self.apply(track(timed_lyrics=lines))
        self.wait_lines()
        layout = NarrowLayout(self.window.width(), self.window.height())
        first, second = self.window.real_lyric_blocks(layout)
        runs = first[3]
        self.assertGreater(runs[0][2].lineCount(), 1)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[1][0], '译文伴随歌词')
        self.assertGreaterEqual(first[1], runs[0][4] + runs[1][4])
        self.assertGreaterEqual(second[0], first[0] + first[1])

    def test_variable_height_scroll_residual_fits_atlas_guard(self):
        lines = tuple(TimedLyricLine(index * 5.0,
                      "A long line that wraps many times in the narrow window " * 4)
                      for index in range(4))
        self.apply(track(timed_lyrics=lines))
        self.wait_lines()
        for visual in (.25, .49, 1.5, 2.51):
            with patch.object(self.window, "lyric_focus", return_value=(int(visual), visual)):
                self.assertLessEqual(abs(self.window.veil_scroll_delta), 16.0001)

    def test_final_row_scroll_residual_uses_clamped_bucket(self):
        lines = tuple(TimedLyricLine(index * 5.0,
                      "A long line that wraps many times in the narrow window " * 4)
                      for index in range(4))
        self.apply(track(timed_lyrics=lines))
        self.wait_lines()
        blocks = self.window.real_lyric_blocks(NarrowLayout(self.window.width(), self.window.height()))
        final_offset = blocks[-1][0]
        self.assertGreater(final_offset % 32.0, 16.0)  # Rounded bucket exceeds the final row.
        final_visual = float(len(blocks) - 1)
        with patch.object(self.window, "lyric_focus", return_value=(len(blocks) - 1, final_visual)):
            self.assertAlmostEqual(self.window.veil_scroll_delta, 0.0)
        for distance in (1.0, 8.0, 16.0, 24.0, 31.0):
            visual = self.window.real_visual_for_offset(final_offset - distance)
            with patch.object(self.window, "lyric_focus", return_value=(int(visual), visual)):
                self.assertLessEqual(abs(self.window.veil_scroll_delta), 16.0001)

    def test_same_track_enrichment_cannot_seek_unpainted_words(self):
        self.apply(track())
        self.wait_lines()
        self.window.show()
        self.app.processEvents()
        self.window.repaint()
        self.window.study_timer.stop()
        point = next(rect.center() for (row, _unit, _fragment), (rect, _seconds)
                     in self.window._lyric_word_hit_rects.items() if row == 0)
        self.window._begin_live_press(point)
        changed = (replace(LINES[0], units=(TimedLyricUnit(9, 10, "first "),
                                          TimedLyricUnit(10, 11, "words"))), *LINES[1:])
        with patch.object(self.window, "source_lines", return_value=changed):
            self.assertIsNone(self.window.live_lyric_action_at(point))
            with patch.object(self.window, "_seek_to_lyric") as seek:
                self.window._finish_live_press(point)
                seek.assert_not_called()

    def test_stationary_painted_hits_reuse_and_scroll_or_reset_rebuild(self):
        self.apply(track())
        self.wait_lines()
        self.window.show()
        self.app.processEvents()
        self.window.study_timer.stop()
        self.window.repaint()
        self.assertTrue(self.window._lyric_word_hit_rects)
        with patch.object(self.window, '_populate_lyric_word_hit_rects',
                          wraps=self.window._populate_lyric_word_hit_rects) as populate:
            self.window.repaint()
            populate.assert_not_called()
            with patch.object(self.window, 'lyric_focus', return_value=(1, 1.0)):
                self.window.repaint()
                self.assertGreater(populate.call_count, 0)
            populate.reset_mock()
            self.window._lyric_word_hit_track_key = None
            self.window.repaint()
            self.assertGreater(populate.call_count, 0)

    def test_collection_state_invalidates_controls(self):
        class Controller(QObject):
            changed = Signal()
            liked = False
            def context(self, track_id):
                return SongCollectionState(track_id=track_id, available=True, liked=self.liked)
        controller = Controller()
        self.owner._song_collection_controller = controller
        self.apply(track(track_id='owned:fixture-1'))
        self.window.refresh_controls(100.0)
        before = self.window._controls_key
        self.assertFalse(self.window._song_collection_state().liked)
        controller.liked = True
        controller.changed.emit()
        self.window.refresh_controls(100.01)
        self.assertTrue(self.window._song_collection_state().liked)
        self.assertNotEqual(self.window._controls_key, before)

    def test_queue_scroll_invalidates_veil_key(self):
        self.provider.get_owned_playback_queue = lambda: (1, 0, tuple(
            OnlineSong('netease', str(i), 'Song ' + str(i)) for i in range(30)))
        self.window.mode = 'playlist'
        self.window._queue_scroll = 0
        self.window.refresh_veil()
        before = self.window.veil_key
        self.window._queue_scroll = 42
        self.window.refresh_veil()
        self.assertNotEqual(self.window.veil_key, before)

    def start_live_fade(self):
        # Warm the shown window's actual background before preparing endpoints.
        self.window.show()
        self.app.processEvents()
        self.window.repaint()
        self.owner.timer.stop()
        self.window._timer.stop()
        self.window.study_timer.stop()
        self.window._audio_output_status_timer.stop()
        now = time.monotonic()
        fade = self.window.visibility.opacity
        fade.start = fade.target = 0.0
        fade.duration = 0.0
        self.window._pointer_active_until = now + 10.0
        with patch('narrow_window.time.monotonic', return_value=now):
            self.window.tick()
        self.assertEqual(fade.duration, .320)
        self.assertEqual(fade.started, now)
        self.assertIsNotNone(self.window._fade_background)
        self.assertIsNotNone(self.window._fade_content_frames)
        return now

    def assert_prepared_live_paint(self, now):
        self.assertIsNotNone(self.window._fade_content_key)
        self.assertEqual(len(self.window._fade_content_frames), 2)
        before = len(self.window.frame_times)
        with patch('narrow_window.time.monotonic', return_value=now + .15):
            with patch.object(self.window.veil, 'paint', side_effect=AssertionError(
                    'prepared endpoints must avoid the ordinary compositor')) as paint:
                self.window.repaint()
                paint.assert_not_called()
        self.assertGreater(len(self.window.frame_times), before)

    def test_paused_live_lyrics_fade_reuse_and_invalidation(self):
        self.apply(track())
        self.assertEqual(self.wait_lines(), LINES)
        now = self.start_live_fade()
        self.assert_prepared_live_paint(now)
        old_key = self.window._fade_content_key

        # A changed real lyric source must not authorize the old endpoints.
        changed = (replace(LINES[0], text='replacement words'), *LINES[1:])
        with patch.object(self.window, 'source_lines', return_value=changed):
            with patch('narrow_window.time.monotonic', return_value=now + .16):
                self.window.refresh_veil()
                with patch.object(self.window.veil, 'paint', wraps=self.window.veil.paint) as paint:
                    self.window.repaint()
                    self.assertGreater(paint.call_count, 0)
                self.window.refresh_fade_content(now + .16)
                self.assertNotEqual(self.window._fade_content_key, old_key)
            self.assert_prepared_live_paint(now)

        # A real playing source must clear even an existing paused endpoint.
        self.apply(track(is_playing=True))
        with patch('narrow_window.time.monotonic', return_value=now + .17):
            self.window.tick()
            self.assertTrue(self.owner.media_is_playing)
            self.assertIsNone(self.window._fade_content_key)
            self.assertIsNone(self.window._fade_content_frames)
            with patch.object(self.window.veil, 'paint', wraps=self.window.veil.paint) as paint:
                self.window.repaint()
                self.assertGreater(paint.call_count, 0)

    def test_live_playlist_fade_reuse_and_queue_replacement(self):
        self.apply(track(is_playing=True))
        songs = [OnlineSong('netease', '1', 'First song')]
        self.provider.get_owned_playback_queue = lambda: (1, 0, tuple(songs))
        self.window.mode = 'playlist'
        now = self.start_live_fade()
        self.assertTrue(self.owner.media_is_playing)
        self.assert_prepared_live_paint(now)
        old_key = self.window._fade_content_key
        songs[:] = [OnlineSong('netease', '2', 'Replacement song')]
        with patch('narrow_window.time.monotonic', return_value=now + .16):
            self.window.refresh_veil()
            with patch.object(self.window.veil, 'paint', wraps=self.window.veil.paint) as paint:
                self.window.repaint()
                self.assertGreater(paint.call_count, 0)
            self.window.refresh_fade_content(now + .16)
            self.assertNotEqual(self.window._fade_content_key, old_key)
        self.assert_prepared_live_paint(now)

    def test_no_progress_control(self):
        keys = {key for group in self.window.glass_groups() for key in group}
        self.assertFalse(any('progress' in key or 'seek' in key for key in keys))

class ImportChecks(unittest.TestCase):
    def test_import_preserves_home_and_appdata(self):
        code = "import os; before={k:os.environ.get(k) for k in ('HOME','APPDATA','LOCALAPPDATA')}; import _demos.narrow3.live; import local.main; import narrow_window; assert before=={k:os.environ.get(k) for k in before}"
        env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(ROOT), str(Path(__file__).parent))))
        subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, check=True)

    def test_live_launch_has_no_settings_or_test_mock_imports(self):
        for path in (ROOT / '_demos/narrow3/live.py', ROOT / 'local/main.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
            names = [node.module or '' if isinstance(node, ast.ImportFrom) else alias.name for node in imports for alias in node.names]
            self.assertFalse(any(name.startswith(('unittest.mock', 'tests', 'pytest')) for name in names))
        live = (ROOT / '_demos/narrow3/live.py').read_text(encoding='utf-8')
        self.assertNotIn('from local.settings import', live)
        self.assertNotIn('SettingsManager(', live)

    def test_launch_log_keeps_recent_files_and_records_errors(self):
        from live import session_log
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs = root / 'logs'
            logs.mkdir()
            for index in range(12):
                path = logs / f'NarrowLive-old-{index}.log'
                path.write_text('old', encoding='utf-8')
                os.utime(path, (index + 1, index + 1))
            with self.assertRaisesRegex(RuntimeError, 'fixture startup error'):
                with session_log(root) as path:
                    print('fixture standard output')
                    print('fixture standard error', file=sys.stderr)
                    raise RuntimeError('fixture startup error')
            text = path.read_text(encoding='utf-8')
            self.assertIn('fixture standard output', text)
            self.assertIn('fixture standard error', text)
            self.assertIn('RuntimeError: fixture startup error', text)
            self.assertEqual(len(tuple(logs.glob('NarrowLive-*.log'))), 10)
            self.assertFalse((logs / 'NarrowLive-old-0.log').exists())

if __name__ == '__main__':
    unittest.main(verbosity=2)
