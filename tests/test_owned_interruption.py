"""Real owned-player intent/GUI boundary with injected decoders and task lanes."""
from __future__ import annotations

import gc
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import OnlineSong, StreamInfo
from local.owned_music_player import OwnedMusicPlayer, _StreamRecovery
from tests.test_owned_playback import Player, Tasks, ThreadRecordingPlayer


class OwnedInterruptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.addCleanup(gc.collect)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tasks = Tasks()
        self.decoder = ThreadRecordingPlayer()
        self.voice = ThreadRecordingPlayer()
        self.player = OwnedMusicPlayer(SimpleNamespace(stream_url=lambda *_a: None),
            LocalLyricsStore(root=Path(self.temp.name)), player=self.decoder, audio=object(),
            dj_player=self.voice, dj_audio=object(), tasks=self.tasks, artwork_tasks=Tasks(),
            prefetch_tasks=Tasks(), stream_cache=None)
        self.addCleanup(self.player.close)
        self.songs = tuple(OnlineSong('netease', str(i), f'song {i}', duration_seconds=120)
            for i in (1, 2))
        self.intents, self.starts = [], []
        self.player.playbackIntent.connect(self.intents.append)
        self.player.playbackStarted.connect(self.starts.append)

    def authorize(self):
        token = next(t for t, _ in reversed(self.tasks.jobs) if t[0] == 'stream')
        song = self.player.queue[self.player.index]
        self.tasks.completed.emit(token,
            StreamInfo(song.source_id, song.song_id, 'https://example.test/music', True), None)

    def start(self):
        self.player.set_interruption_tracking_enabled(True)
        self.assertTrue(self.player.play_queue(self.songs))
        self.authorize()
        self.assertTrue(self.player.interruption_playing())
        self.assertEqual(len(self.starts), 1)

    def test_off_playback_keeps_existing_transport_and_emits_no_notifications(self):
        self.assertTrue(self.player.play_queue(self.songs))
        self.authorize()
        with patch.object(self.player, 'interruption_playing', side_effect=AssertionError('OFF extra read')):
            self.assertTrue(self.player.send_playback_command('toggle_play_pause'))
            self.assertTrue(self.player.send_playback_command('toggle_play_pause'))
        self.assertEqual(self.intents, [])
        self.assertEqual(self.starts, [])
        self.assertFalse(self.player.pause_for_interruption())

    def test_started_requires_actual_matching_stream_not_loading_or_retained_stream(self):
        self.start()
        first_serial = self.starts[0]['serial']
        self.assertTrue(self.player.play_daily())
        self.assertEqual(len(self.intents), 2)
        self.decoder.play()  # Repeated Playing from the retained song.
        self.assertEqual(len(self.starts), 1)
        token = next(t for t, _ in reversed(self.tasks.jobs) if t[0] == 'catalog')
        self.tasks.completed.emit(token, self.songs[::-1], None)
        self.assertEqual(len(self.starts), 1)
        self.authorize()
        self.assertEqual(len(self.starts), 2)
        self.assertGreater(self.starts[-1]['serial'], first_serial)
        self.assertEqual(self.starts[-1]['origin'], self.player.playback_origin_token())

    def test_failed_requests_and_automatic_continuation_never_publish_user_start(self):
        self.start()
        self.player._media_status(QMediaPlayer.MediaStatus.EndOfMedia)
        self.authorize()
        self.assertEqual(len(self.intents), 1)
        self.assertEqual(len(self.starts), 1)
        self.tasks.accepted = False
        self.assertFalse(self.player.play_daily())
        self.decoder.play()
        self.assertEqual(len(self.starts), 1)
        self.assertFalse(self.player.play_queue(()))
        self.assertEqual(len(self.starts), 1)

    def test_explicit_pause_preserves_queue_position_and_has_no_reverse_intent(self):
        self.start()
        self.decoder.positionChanged.emit(23456)
        before = (self.player.queue, self.player.index, self.player.track.position_seconds,
            self.player.playback_origin_token(), self.player.playback_mode)
        self.decoder.calls.clear()
        self.assertTrue(self.player.pause_for_interruption(lambda: True))
        self.assertTrue(self.player.pause_for_interruption(lambda: True))
        self.assertEqual([name for name, _ in self.decoder.calls].count('pause'), 1)
        self.assertEqual((self.player.queue, self.player.index, self.player.track.position_seconds,
            self.player.playback_origin_token(), self.player.playback_mode), before)
        self.assertEqual(len(self.intents), 1)
        self.assertEqual(len(self.starts), 1)
        self.player._play_music()  # A stale recovery/preparation callback.
        self.assertFalse(self.player.interruption_playing())
        self.assertTrue(self.player.send_playback_command('toggle_play_pause'))
        self.assertTrue(self.player.interruption_playing())
        self.assertEqual(len(self.starts), 2)

    def test_pause_cancels_new_catalog_autoplay_while_retaining_old_audio_and_queue(self):
        self.start()
        before = self.player.queue
        self.assertTrue(self.player.play_playlist('netease', 'playlist'))
        token = next(t for t, _ in reversed(self.tasks.jobs) if t[0] == 'catalog')
        self.assertTrue(self.player.interruption_playing())
        self.assertTrue(self.player.pause_for_interruption())
        self.tasks.completed.emit(token, self.songs[::-1], None)
        self.assertEqual(self.player.queue, before)
        self.assertFalse(self.player.interruption_playing())
        self.assertFalse(self.player._autoplay_after_load)
        self.player._end_current_stream()  # Late EOF cannot begin another song.
        self.assertEqual(self.player.index, 0)
        self.assertEqual(len(self.starts), 1)

    def test_pause_disarms_output_preparation_recovery_and_associated_speech(self):
        self.start()
        self.player.set_output_preparing(True)
        self.voice.play()
        self.player._stream_recovery = _StreamRecovery(self.player._epoch, 1200,
            time.monotonic() + 20, 'network', phase='progress')
        self.assertTrue(self.player.pause_for_interruption())
        self.assertFalse(self.player._stream_recovery.resume)
        self.assertFalse(self.player._output_prepare_resume)
        self.assertEqual(self.voice.state, QMediaPlayer.PlaybackState.PausedState)
        self.player.set_output_preparing(False)
        self.player._play_music()
        self.assertFalse(self.player.interruption_playing())

    def test_user_pause_during_loading_invalidates_start_and_automatic_pause_guard(self):
        self.player.set_interruption_tracking_enabled(True)
        self.player.play_queue(self.songs)
        serial = self.player._interruption_serial
        self.assertTrue(self.player.send_playback_command('toggle_play_pause'))
        self.assertGreater(self.player._interruption_serial, serial)
        self.authorize()
        self.assertEqual(self.starts, [])
        self.assertFalse(self.player.interruption_playing())
        self.assertFalse(self.intents[-1]['will_play'])
        self.assertFalse(self.player.pause_for_interruption(lambda: self.player._interruption_serial == serial))

    def test_guard_and_toggle_off_block_queued_gui_pause(self):
        self.start()
        serial = self.player._interruption_serial
        queued = []
        self.player._control_requested.disconnect(self.player._apply_control_request)
        self.player._control_requested.connect(queued.append)
        result = []
        worker = threading.Thread(target=lambda: result.append(self.player.pause_for_interruption(
            lambda: self.player._interruption_serial == serial)))
        worker.start()
        deadline = time.monotonic() + 1
        while not queued and time.monotonic() < deadline:
            self.app.processEvents()
        self.assertTrue(queued)
        self.player.set_interruption_tracking_enabled(False)
        self.player._apply_control_request(queued[0])
        worker.join(1)
        self.assertEqual(result, [False])
        self.assertTrue(self.player.interruption_playing())
        self.player._control_requested.disconnect(queued.append)
        self.player._control_requested.connect(self.player._apply_control_request)

    def test_accepted_worker_pause_operates_decoders_only_on_gui_thread(self):
        self.start()
        self.decoder.calls.clear()
        result = []
        worker = threading.Thread(target=lambda: result.append(self.player.pause_for_interruption()))
        worker.start()
        deadline = time.monotonic() + 1
        while worker.is_alive() and time.monotonic() < deadline:
            self.app.processEvents()
        worker.join(1)
        self.assertEqual(result, [True])
        self.assertFalse(self.player.interruption_playing())
        self.assertEqual({thread_id for _, thread_id in self.decoder.calls}, {threading.get_ident()})

    def test_user_resume_during_pause_ack_prevents_stale_catalog_cancellation(self):
        self.start()
        self.player.play_daily()
        old_serial = self.player._interruption_serial
        resumed = []
        def user_resume(state):
            if state == QMediaPlayer.PlaybackState.PausedState and not resumed:
                resumed.append(True)
                self.player.send_playback_command('toggle_play_pause')
        self.decoder.playbackStateChanged.connect(user_resume)
        self.assertFalse(self.player.pause_for_interruption(
            lambda: self.player._interruption_serial == old_serial))
        self.assertTrue(self.player._catalog_pending_epoch)
        self.assertGreater(self.player._interruption_serial, old_serial)
        self.decoder.playbackStateChanged.disconnect(user_resume)

    def test_synchronous_resume_notifies_intent_before_confirmed_start(self):
        self.start()
        self.player.send_playback_command('toggle_play_pause')
        order = []
        self.player.playbackIntent.connect(lambda e: order.append(('intent', e['serial'])))
        self.player.playbackStarted.connect(lambda e: order.append(('started', e['serial'])))
        self.player.send_playback_command('toggle_play_pause')
        self.assertEqual([kind for kind, _ in order], ['intent', 'started'])
        self.assertEqual(order[0][1], order[1][1])

    def test_explicit_next_and_queue_selection_start_once_each(self):
        self.start()
        self.assertTrue(self.player.send_playback_command('next'))
        self.authorize()
        self.assertTrue(self.player.select_queue_index(0, self.player.playback_queue_token()))
        self.authorize()
        self.assertEqual(len(self.intents), 3)
        self.assertEqual(len(self.starts), 3)
        self.decoder.play()
        self.assertEqual(len(self.starts), 3)


if __name__ == '__main__':
    unittest.main()
