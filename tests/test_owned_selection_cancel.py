"""Cancelling a library lookup retains only its genuine previous decoder."""
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import OnlineSong, StreamInfo
from local.owned_music_player import OwnedMusicPlayer
from tests.test_owned_playback import Tasks, ThreadRecordingPlayer


class OwnedSelectionCancelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.tasks, self.artwork, self.prefetch = (Tasks() for _ in range(3))
        self.qt = ThreadRecordingPlayer()
        self.songs = tuple(OnlineSong("netease", str(index), f"Song {index}", duration_seconds=120)
                           for index in range(2))
        self.service = SimpleNamespace(stream_url=lambda source, song: StreamInfo(source, song,
            "https://fixture.invalid/music", True), lyrics=lambda *args: None,
            daily_recommendations=lambda source: self.songs)
        self.owned = OwnedMusicPlayer(self.service, LocalLyricsStore(Path(self.folder.name)),
            player=self.qt, dj_player=ThreadRecordingPlayer(), audio=object(), dj_audio=object(),
            tasks=self.tasks, artwork_tasks=self.artwork, prefetch_tasks=self.prefetch, stream_cache=None)
        self.workers = []

    def tearDown(self):
        self.owned.close()
        deadline = time.monotonic() + 2.5
        while any(worker.is_alive() for worker in self.workers) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        for worker in self.workers:
            worker.join(.1)
        self.folder.cleanup()

    def authorize(self):
        token, action = next(job for job in reversed(self.tasks.jobs) if job[0][0] == "stream")
        self.tasks.completed.emit(token, action(), None)

    def open(self):
        self.owned.play_queue(self.songs)
        self.authorize()

    def pending(self):
        self.assertTrue(self.owned.play_daily())
        return self.tasks.jobs[-1]

    def test_cancellation_preserves_queue_clock_lyrics_and_paused_decoder_without_reopening(self):
        self.open()
        self.qt.setPosition(23000)
        self.qt.positionChanged.emit(23000)
        self.qt.durationChanged.emit(119000)
        self.owned.track = replace(self.owned.track, lyrics=("Retained lyrics",))
        queue, source, epoch = self.owned.queue, self.qt.source(), self.owned._epoch
        token, action = self.pending()
        self.owned.send_playback_command("toggle_play_pause")
        with patch.object(self.qt, "stop", wraps=self.qt.stop) as stop, \
                patch.object(self.qt, "play", wraps=self.qt.play) as play, \
                patch.object(self.qt, "setSource", wraps=self.qt.setSource) as set_source:
            self.assertTrue(self.owned.cancel_pending_selection())
        stop.assert_not_called(); play.assert_not_called(); set_source.assert_not_called()
        self.assertIs(self.owned.queue, queue)
        self.assertEqual((self.qt.source(), self.owned._stream_epoch), (source, epoch))
        self.assertEqual(self.owned.status, "playing")
        self.assertFalse(self.owned.track.is_playing)
        self.assertEqual(self.owned.track.position_seconds, 23)
        self.assertEqual(self.owned.track.duration_seconds, 119)
        self.assertEqual(self.owned.track.lyrics, ("Retained lyrics",))
        self.assertEqual(action(), ())
        self.tasks.completed.emit(token, (OnlineSong("qq", "late", "Late reply"),), None)
        self.tasks.completed.emit(token, None, "auth_required")
        self.assertIs(self.owned.queue, queue)
        self.assertEqual(self.owned.status, "playing")
        self.assertEqual(self.owned.failure_reason, "")

    def test_initial_selection_cancels_without_recoverable_audio(self):
        token, action = self.pending()
        self.assertTrue(self.owned.cancel_pending_selection())
        self.assertIsNone(self.owned.track)
        self.assertEqual(self.owned.status, "idle")
        self.assertEqual(action(), ())
        self.tasks.completed.emit(token, self.songs, None)
        self.assertIsNone(self.owned.track)

    def test_after_catalog_handoff_cancellation_stops_pending_new_audio(self):
        self.open()
        token, action = self.pending()
        self.tasks.completed.emit(token, action(), None)
        self.assertEqual(self.owned.status, "loading_stream")
        stream, resolve = self.tasks.jobs[-1]
        self.assertTrue(self.owned.cancel_pending_selection())
        self.assertEqual(self.owned.status, "idle")
        self.assertIsNone(self.owned.track)
        self.assertIsNone(resolve())
        self.tasks.completed.emit(stream, StreamInfo("netease", "0", "https://fixture.invalid/late", True), None)
        self.assertTrue(self.qt.source().isEmpty())

    def test_cancellation_keeps_observed_original_access_error_visible(self):
        self.open(); self.pending()
        self.qt.errorOccurred.emit(QMediaPlayer.Error.AccessDeniedError, "private detail")
        self.assertTrue(self.owned.cancel_pending_selection())
        self.assertEqual(self.owned.status, "playback_failed")
        self.assertEqual(self.owned.failure_reason, "access_denied")
        self.assertFalse(self.owned.track.is_playing)

    def test_original_eof_during_lookup_continues_old_queue_when_cancelled(self):
        self.open(); self.pending()
        self.qt.stop()
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertTrue(self.owned.cancel_pending_selection())
        self.assertEqual(self.owned.index, 1)
        self.assertEqual(self.owned.status, "loading_stream")
        self.authorize()
        self.assertTrue(self.owned.track.is_playing)

    def test_late_cancel_leaves_healthy_playback_and_reported_catalog_failure_alone(self):
        self.open()
        self.assertFalse(self.owned.cancel_pending_selection())
        token, _ = self.pending()
        self.tasks.completed.emit(token, None, "auth_required")
        self.assertFalse(self.owned.cancel_pending_selection())
        self.assertEqual(self.owned.status, "catalog_unavailable")
        self.assertEqual(self.owned.failure_reason, "auth_required")
        self.assertTrue(self.owned.track.is_playing)

    def begin_worker_cancel(self):
        observed, result, emitted = [], [], threading.Event()
        def observe(request):
            observed.append(request)
            emitted.set()
        self.owned._control_requested.connect(observe, Qt.ConnectionType.DirectConnection)
        worker = threading.Thread(target=lambda: result.append(self.owned.cancel_pending_selection()))
        self.workers.append(worker); worker.start()
        self.assertTrue(emitted.wait(1))
        self.owned._control_requested.disconnect(observe)
        return worker, result, observed[0]

    def finish_worker(self, worker):
        deadline = time.monotonic() + 2.5
        while worker.is_alive() and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(.001)
        worker.join(.1)
        self.assertFalse(worker.is_alive())

    def test_worker_cancellation_supports_idle_lookup_and_touches_qt_only_on_owner_thread(self):
        self.pending()
        self.qt.calls.clear()
        worker, result, _ = self.begin_worker_cancel()
        self.assertEqual(self.qt.calls, [])
        self.finish_worker(worker)
        self.assertEqual(result, [True])
        self.assertTrue(self.qt.calls)
        self.assertTrue(all(thread == threading.get_ident() for _, thread in self.qt.calls))
        self.assertEqual(self.owned.status, "idle")

    def test_worker_cancellation_cannot_apply_to_newer_same_track_selection(self):
        self.open(); self.pending()
        worker, result, _ = self.begin_worker_cancel()
        newest, _ = self.pending()
        self.qt.calls.clear()
        self.finish_worker(worker)
        self.assertEqual(result, [False])
        self.assertEqual(self.qt.calls, [])
        self.assertEqual(self.owned._catalog_pending_epoch, newest[1])
        self.assertEqual(self.owned.status, "loading_catalog")


if __name__ == "__main__":
    unittest.main()
