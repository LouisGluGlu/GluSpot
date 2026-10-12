"""Complete-file ownership, asynchronous handoff and next-song cancellation."""
from pathlib import Path
from dataclasses import replace
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import LyricPayload, OnlineSong, StreamInfo
from api.owned_stream_cache import PreparedAudio, StreamDownloadError
from local.owned_music_player import OwnedMusicPlayer
from tests.test_personal_dj_playback import segments
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
        self.dj_qt = Player()
        self.cache = FixtureCache(self.root)
        self.service = SimpleNamespace(stream_url=Mock(side_effect=lambda source, song:
            StreamInfo(source, song, "https://fixture.invalid/audio", True)), lyrics=Mock(return_value=None))
        self.owned = OwnedMusicPlayer(self.service, LocalLyricsStore(self.root / "lyrics"),
            player=self.qt, audio=object(), dj_player=self.dj_qt, dj_audio=object(), tasks=self.tasks, artwork_tasks=Tasks(), prefetch_tasks=Tasks(),
            stream_cache=self.cache, download_tasks=self.downloads)
        self.songs = tuple(OnlineSong("netease", str(index), "Generated song", duration_seconds=120)
            for index in range(3))

    def tearDown(self):
        self.owned.close()
        self.folder.cleanup()

    def perform(self, tasks, kind):
        if kind == "audio_prefetch" and self.owned._fm_kind == "dj":
            kind = "dj_prefetch"
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

    def test_music_prefetches_real_intro_then_both_complete_files_play_independently(self):
        songs = (segments(1)[1],) + segments(2)
        self.owned.personal_dj_enabled = True
        self.service.personal_dj = lambda source: ()
        self.owned._replace_queue(songs, fm_source="netease", fm_kind="dj")
        self.perform(self.tasks, "stream")
        self.perform(self.downloads, "audio")
        self.qt.positionChanged.emit(65000)
        previous = self.owned.track
        self.assertTrue(previous.is_playing)
        self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "loading")
        self.assertEqual(self.owned.get_personal_dj_presentation()["next_title"], "Song 2")
        _, voice = self.perform(self.downloads, "audio_prefetch")
        self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "ready")
        self.assertTrue(self.owned.track.is_playing)
        self.owned.send_playback_command("next")
        self.assertFalse(self.owned.is_personal_dj_speaking())
        self.assertEqual(self.owned._dj_prepared_audio, voice)
        self.assertEqual(Path(self.dj_qt.source().toLocalFile()), voice.path)
        self.assertFalse(self.owned.track.is_playing)
        self.assertEqual((self.owned.track.track_id, self.owned.track.position_seconds),
            ("owned:netease:song-2", 0.0))
        self.perform(self.tasks, "stream")
        _, following = self.perform(self.downloads, "audio")
        self.assertEqual(self.owned._prepared_audio, following)
        self.assertEqual(Path(self.qt.source().toLocalFile()), following.path)
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.qt.positionChanged.emit(7500)
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertIs(self.owned._prepared_audio, following)
        self.assertEqual(self.owned.track.position_seconds, 7.5)
        self.assertIn(voice.lease, self.cache.released)
        self.assertIn(following.lease, self.cache.leases)
        self.assertEqual(self.owned.track.title, "Song 2")
        self.assertTrue(self.owned.track.is_playing)

    def test_translation_changed_before_complete_voice_file_reparses_target_without_starting_audio(self):
        settings = {"v2_enable_dual_line_lyrics": False}
        self.owned.lyrics_store.settings_getter = lambda key, default=None: settings.get(key, default)
        self.service.lyrics = Mock(side_effect=lambda source, identity: LyricPayload(source, identity,
            raw_lrc="[00:01]Shine on", translation="[00:01]继续闪耀"))
        self.owned.personal_dj_enabled = True
        self.service.personal_dj = lambda source: ()
        self.owned._replace_queue(segments(1), fm_source="netease", fm_kind="dj")
        token, action = next(job for job in self.tasks.jobs if job[0][0] == "dj_lyrics")
        old_result = action()
        self.perform(self.tasks, "stream")
        settings["v2_enable_dual_line_lyrics"] = True
        self.owned.refresh_lyrics()
        self.tasks.completed.emit(token, old_result, None)
        self.perform(self.tasks, "lyrics_refresh")
        self.assertEqual(self.owned._stream_epoch, 0)
        self.assertTrue(self.qt.source().isEmpty())
        self.assertIn("继续闪耀", self.owned.track.lyrics[0])
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-1")
        self.assertFalse(self.owned.track.is_playing)
        self.perform(self.downloads, "audio")
        self.assertFalse(self.owned.is_personal_dj_speaking())
        self.perform(self.tasks, "dj_stream")
        self.perform(self.downloads, "dj_audio")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertIn("继续闪耀", self.owned.track.lyrics[0])
        self.assertEqual(self.service.lyrics.call_count, 1)

    def test_failed_intro_prefetch_and_actual_intro_failure_keep_healthy_music(self):
        songs = (segments(1)[1],) + segments(2)
        self.owned.personal_dj_enabled = True
        self.service.personal_dj = lambda source: ()
        self.owned._replace_queue(songs, fm_source="netease", fm_kind="dj")
        self.perform(self.tasks, "stream")
        self.perform(self.downloads, "audio")
        failed = next(token for token, _ in reversed(self.downloads.jobs) if token[0] == "dj_prefetch")
        source = self.qt.source()
        self.downloads.completed.emit(failed, None, "network")
        self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "failed")
        from unittest.mock import patch
        with patch("local.owned_music_player.time.monotonic", return_value=self.owned._dj_failed_until + 1):
            self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "idle")
        self.assertTrue(self.owned.track.is_playing)
        self.assertEqual(self.qt.source(), source)
        self.owned.send_playback_command("next")
        self.perform(self.tasks, "stream")
        self.perform(self.tasks, "dj_stream")
        token = next(token for token, _ in reversed(self.downloads.jobs) if token[0] == "dj_audio")
        self.downloads.completed.emit(token, None, "network")
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-2")
        self.perform(self.downloads, "audio")
        self.assertTrue(self.owned.track.is_playing)
        self.assertEqual(self.owned.track.title, "Song 2")

    def test_cancelled_voice_and_following_song_downloads_never_replace_new_queue_and_release_leases(self):
        self.owned.personal_dj_enabled = True
        self.service.personal_dj = lambda source: ()
        self.owned._replace_queue(segments(1), fm_source="netease", fm_kind="dj")
        self.perform(self.tasks, "stream")
        self.perform(self.downloads, "audio")
        self.perform(self.tasks, "dj_stream")
        stale_token, action = next(job for job in reversed(self.downloads.jobs) if job[0][0] == "dj_audio")
        stale = action()
        self.owned.play_queue((OnlineSong("qq", "chosen", "chosen music"),))
        self.downloads.completed.emit(stale_token, stale, None)
        self.assertIn(stale.lease, self.cache.released)
        self.assertEqual(self.owned.track.track_id, "owned:qq:chosen")
        self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "idle")

    def test_multiple_real_intro_segments_prefetch_without_replacing_current_presentation(self):
        voice, song = segments(2)
        second_stream = replace(voice.direct_stream, song_id="dj:second-part")
        second_voice = replace(voice, song_id=second_stream.song_id, direct_stream=second_stream)
        self.owned.personal_dj_enabled = True
        self.service.personal_dj = lambda source: ()
        self.owned._replace_queue((segments(1)[1], voice, second_voice, song), fm_source="netease", fm_kind="dj")
        self.perform(self.tasks, "stream")
        self.perform(self.downloads, "audio")
        self.perform(self.downloads, "audio_prefetch")
        self.owned.send_playback_command("next")
        self.perform(self.tasks, "stream")
        _, music = self.perform(self.downloads, "audio")
        speaking = self.owned.get_personal_dj_presentation()
        self.assertEqual(speaking["state"], "speaking")
        self.assertEqual(speaking["next_title"], "Song 2")
        _, prepared_second = self.perform(self.downloads, "dj_prefetch")
        self.qt.positionChanged.emit(6000)
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        current = self.owned.get_personal_dj_presentation()
        self.assertEqual(current["state"], "speaking")
        self.assertEqual(current["next_title"], "Song 2")
        self.assertGreater(current["token"], speaking["token"])
        self.assertEqual(self.owned._prepared_audio, music)
        self.assertEqual(self.owned._dj_prepared_audio, prepared_second)
        self.assertEqual(self.owned.track.title, "Song 2")
        self.assertTrue(self.owned.track.is_playing)
        self.assertEqual(self.owned.track.position_seconds, 6)

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

    def test_enabling_dj_retains_current_complete_file_lease_and_revokes_only_future_download(self):
        from tests.test_personal_dj_playback import segments
        self.service.personal_dj = Mock(return_value=segments())
        self.service.personal_fm = Mock(return_value=(self.songs[2],))
        self.owned._replace_queue(self.songs, fm_source="netease")
        self.perform(self.tasks, "stream")
        _, active = self.perform(self.downloads, "audio")
        old_token, old_action = next(job for job in self.downloads.jobs if job[0][0] == "audio_prefetch")
        old_prepared = old_action()
        source = self.qt.url
        current_epoch = self.owned._epoch
        self.owned.set_personal_dj_enabled(True)
        self.assertEqual(self.qt.url, source)
        self.assertEqual(self.owned._epoch, current_epoch)
        self.assertIs(self.owned._prepared_audio, active)
        self.assertIn(active.lease, self.cache.leases)
        self.downloads.completed.emit(old_token, old_prepared, None)
        self.assertIn(old_prepared.lease, self.cache.released)
        self.assertNotIn(active.lease, self.cache.released)
        self.perform(self.owned.prefetch_tasks, "fm_prefetch")
        _, voice = self.perform(self.downloads, "audio_prefetch")
        self.assertEqual(voice.stream.quality, "dj_voice")
        self.owned._end_current_stream()
        self.assertIs(self.owned._dj_prepared_audio, voice)
        self.assertEqual(Path(self.dj_qt.url.toLocalFile()), voice.path)
        self.assertIn(active.lease, self.cache.released)
        self.perform(self.tasks, "stream")
        _, music = self.perform(self.downloads, "audio")
        self.assertIs(self.owned._prepared_audio, music)
        self.assertTrue(self.owned.is_personal_dj_speaking())

    def test_enabling_dj_during_current_download_does_not_cancel_or_unpin_current_audio(self):
        from tests.test_personal_dj_playback import segments
        self.service.personal_dj = Mock(return_value=segments())
        self.service.personal_fm = Mock(return_value=self.songs)
        self.owned._replace_queue(self.songs, fm_source="netease")
        self.perform(self.tasks, "stream")
        token, action = next(job for job in self.downloads.jobs if job[0][0] == "audio")
        self.owned.set_personal_dj_enabled(True)
        prepared = action()
        self.downloads.completed.emit(token, prepared, None)
        self.assertIs(self.owned._prepared_audio, prepared)
        self.assertIn(prepared.lease, self.cache.leases)
        self.assertTrue(self.owned.track.is_playing)
        self.assertEqual(self.owned.track.track_id, "owned:netease:0")

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
