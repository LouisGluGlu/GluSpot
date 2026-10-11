"""Complete-file ownership, asynchronous handoff and next-song cancellation."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import OnlineSong, StreamInfo
from api.owned_stream_cache import PreparedAudio, StreamDownloadError
from local.owned_music_player import OwnedMusicPlayer
from tests.test_owned_playback import Player, Tasks


class FixtureCache:
    def __init__(self, root):
        self.root = root
        self.leases, self.preparations, self.released = {}, [], []
        self.closed = False

    def prepare(self, stream, cancelled=lambda: False):
        if self.closed or cancelled():
            raise StreamDownloadError("cancelled")
        lease = str(len(self.preparations) + 1)
        path = self.root / (lease + ".audio")
        path.write_bytes(b"complete-local-fixture")
        prepared = PreparedAudio(lease, path, stream, path.stat().st_size, 1)
        self.preparations.append(prepared)
        self.leases[lease] = prepared
        return prepared

    def release(self, prepared):
        self.released.append(prepared.lease)
        self.leases.pop(prepared.lease, None)

    def close(self):
        self.closed = True
        self.leases.clear()


class OwnedBufferedPlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.tasks, self.downloads = Tasks(), Tasks()
        self.qt = Player()
        self.cache = FixtureCache(self.root)
        self.service = SimpleNamespace(stream_url=Mock(side_effect=lambda source, song:
            StreamInfo(source, song, "https://fixture.invalid/audio", True)), lyrics=Mock(return_value=None))
        self.owned = OwnedMusicPlayer(self.service, LocalLyricsStore(self.root / "lyrics"),
            player=self.qt, audio=object(), tasks=self.tasks, artwork_tasks=Tasks(), prefetch_tasks=Tasks(),
            stream_cache=self.cache, download_tasks=self.downloads)
        self.songs = tuple(OnlineSong("netease", str(index), "Generated song", duration_seconds=120)
            for index in range(3))

    def tearDown(self):
        self.owned.close()
        self.folder.cleanup()

    def perform(self, tasks, kind):
        token, action = next(job for job in reversed(tasks.jobs) if job[0][0] == kind)
        result = action()
        tasks.completed.emit(token, result, None)
        return token, result

    def start(self, songs=None):
        self.assertTrue(self.owned.play_queue(self.songs if songs is None else songs))
        self.perform(self.tasks, "stream")
        return self.perform(self.downloads, "audio")

    def test_authorization_never_sends_remote_url_or_partial_file_to_qt(self):
        self.owned.play_queue(self.songs)
        self.perform(self.tasks, "stream")
        self.assertEqual(self.owned.status, "loading_stream")
        self.assertTrue(self.qt.url.isEmpty())
        self.assertFalse(self.cache.preparations)
        self.assertNotEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        _, prepared = self.perform(self.downloads, "audio")
        self.assertTrue(self.qt.url.isLocalFile())
        self.assertEqual(Path(self.qt.url.toLocalFile()), prepared.path)
        self.assertEqual(prepared.path.read_bytes(), b"complete-local-fixture")
        self.assertEqual(self.owned.status, "playing")

    def test_one_next_file_is_consumed_without_reauthorization_or_download(self):
        _, active = self.start()
        _, upcoming = self.perform(self.downloads, "audio_prefetch")
        stream_jobs = sum(job[0][0] == "stream" for job in self.tasks.jobs)
        downloads = len(self.cache.preparations)
        self.assertTrue(self.owned.send_playback_command("next"))
        self.assertEqual(self.owned._prepared_audio, upcoming)
        self.assertEqual(Path(self.qt.url.toLocalFile()), upcoming.path)
        self.assertEqual(sum(job[0][0] == "stream" for job in self.tasks.jobs), stream_jobs)
        self.assertEqual(len(self.cache.preparations), downloads)
        self.assertIn(active.lease, self.cache.released)
        self.assertIn(upcoming.lease, self.cache.leases)

    def test_cancelled_current_result_releases_only_unused_lease(self):
        self.owned.play_queue(self.songs)
        self.perform(self.tasks, "stream")
        old_token, action = self.downloads.jobs[-1]
        old = action()
        self.owned.send_playback_command("next")
        self.perform(self.tasks, "stream")
        _, current = self.perform(self.downloads, "audio")
        self.downloads.completed.emit(old_token, old, None)
        self.assertIn(old.lease, self.cache.released)
        self.assertIn(current.lease, self.cache.leases)
        self.assertEqual(self.owned._prepared_audio, current)

    def test_repeated_and_stale_delivery_cannot_unpin_or_reopen_active_file(self):
        token, active = self.start()
        extra = self.cache.prepare(active.stream)
        self.downloads.completed.emit(token, extra, None)
        self.downloads.completed.emit(token, active, None)
        self.downloads.completed.emit(("audio", token[1] - 1, token[2]), active, None)
        self.downloads.completed.emit(token, None, "incomplete")
        self.assertIn(extra.lease, self.cache.released)
        self.assertNotIn(active.lease, self.cache.released)
        self.assertIn(active.lease, self.cache.leases)
        self.assertEqual(self.owned.status, "playing")
        self.assertEqual(self.owned._prepared_audio, active)

    def test_same_next_song_after_cancellation_has_a_distinct_generation(self):
        self.start()
        old_token, action = self.downloads.jobs[-1]
        old = action()
        self.owned._clear_audio_prefetch()
        self.owned._prefetch_audio()
        new_token, new_action = self.downloads.jobs[-1]
        self.assertNotEqual(old_token, new_token)
        self.assertEqual(old_token[2:], new_token[2:])
        self.downloads.completed.emit(old_token, old, None)
        self.assertIsNone(self.owned._prefetched_audio)
        self.assertEqual(self.owned._audio_prefetch_request, new_token)
        self.assertIn(old.lease, self.cache.released)
        fresh = new_action()
        self.downloads.completed.emit(new_token, fresh, None)
        self.assertEqual(self.owned._prefetched_audio, fresh)
        self.downloads.completed.emit(old_token, fresh, None)
        self.assertIn(fresh.lease, self.cache.leases)

    def test_permanent_incomplete_audio_never_plays_or_skips_the_song(self):
        self.owned.play_queue(self.songs)
        self.perform(self.tasks, "stream")
        token = self.downloads.jobs[-1][0]
        self.downloads.completed.emit(token, None, "incomplete")
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.owned.index, 0)
        self.assertEqual(self.owned.status, "stream_unavailable")
        self.assertEqual(self.owned.failure_reason, "incomplete")
        self.assertTrue(self.qt.url.isEmpty())
        self.assertFalse(self.owned.track.is_playing)

    def test_trial_identity_and_status_survive_local_file_handoff(self):
        self.service.stream_url.side_effect = lambda source, song: StreamInfo(source, song,
            "https://fixture.invalid/trial", True, trial=True)
        _, prepared = self.start((self.songs[0],))
        self.assertTrue(prepared.stream.trial)
        self.assertEqual(self.owned.status, "trial")
        self.assertTrue(self.qt.url.isLocalFile())
        self.assertFalse(any(token[0] == "audio_prefetch" for token, _ in self.downloads.jobs))

    def test_close_invalidates_pending_io_and_releases_late_artifact(self):
        self.owned.play_queue(self.songs)
        self.perform(self.tasks, "stream")
        token, action = self.downloads.jobs[-1]
        prepared = action()
        self.owned.close()
        self.downloads.completed.emit(token, prepared, None)
        self.assertIn(prepared.lease, self.cache.released)
        self.assertTrue(self.qt.url.isEmpty())
        self.assertIsNone(self.owned.track)
        self.assertTrue(self.cache.closed)


if __name__ == "__main__":
    unittest.main()
