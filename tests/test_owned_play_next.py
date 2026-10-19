"""Listener additions preserve the sounding decoder and existing queue order."""
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import OnlineSong, StreamInfo
from local.owned_music_player import OwnedMusicPlayer
from tests.test_owned_buffered_playback import FixtureCache
from tests.test_owned_playback import Player, Tasks
from tests.test_personal_dj_playback import segments


class OwnedPlayNextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.tasks, self.artwork, self.prefetch, self.downloads = (Tasks() for _ in range(4))
        self.qt, self.dj_qt = Player(), Player()
        self.service = SimpleNamespace(stream_url=Mock(side_effect=lambda source, song:
            StreamInfo(source, song, "https://fixture.invalid/music", True)),
            lyrics=Mock(return_value=None), personal_fm=Mock(return_value=()),
            personal_dj=Mock(return_value=()))
        self.owned = OwnedMusicPlayer(self.service, LocalLyricsStore(self.root / "lyrics"),
            player=self.qt, audio=object(), dj_player=self.dj_qt, dj_audio=object(),
            tasks=self.tasks, artwork_tasks=self.artwork, prefetch_tasks=self.prefetch,
            stream_cache=None)
        self.songs = tuple(OnlineSong("netease", str(i), f"Song {i}", duration_seconds=120)
            for i in range(5))
        self.addition = OnlineSong("netease", "added", "Search result", duration_seconds=150)

    def tearDown(self):
        self.owned.close()
        self.folder.cleanup()

    def perform(self, tasks, kind):
        token, action = next(job for job in reversed(tasks.jobs) if job[0][0] == kind)
        result = action()
        tasks.completed.emit(token, result, None)
        return token, result

    def start(self, songs=None, *, fm=False, buffered=False):
        if buffered:
            self.owned.stream_cache = FixtureCache(self.root)
            self.owned.download_tasks = self.downloads
            self.downloads.completed.connect(self.owned._completed)
        self.owned._replace_queue(self.songs if songs is None else songs,
            fm_source="netease" if fm else "")
        self.perform(self.tasks, "stream")
        if buffered:
            self.perform(self.downloads, "audio")

    def song_ids(self):
        return tuple(song.song_id for song in self.owned.queue)

    def test_idle_invalid_and_pending_replacement_reject_without_mutating_queue(self):
        self.assertFalse(self.owned.enqueue_next(self.addition))
        self.start()
        original = self.owned.queue
        for invalid in (None, "song", replace(self.addition, song_id=""),
                replace(self.addition, source_id=""), replace(self.addition, title=" "),
                replace(self.addition, media_kind="dj_voice")):
            with self.subTest(invalid=invalid):
                self.assertFalse(self.owned.enqueue_next(invalid))
                self.assertEqual(self.owned.queue, original)
        self.owned._catalog("daily", "netease", lambda: self.songs)
        self.assertFalse(self.owned.enqueue_next(self.addition))
        self.assertEqual(self.owned.queue, original)

    def test_addition_preserves_current_decoder_clock_lyrics_and_playback_state(self):
        self.start(buffered=True)
        self.qt.positionChanged.emit(35000)
        self.owned.track = replace(self.owned.track, lyrics=("Current lyric",), lyrics_source="fixture")
        before = (self.owned.track.track_id, self.owned.track.position_seconds,
            self.owned.track.position_sampled_at, self.owned.track.lyrics,
            self.owned._epoch, self.owned._stream_epoch, self.qt.source(), self.qt.playbackState(),
            self.owned._prepared_audio, self.owned.status)
        with patch.object(self.qt, "stop", wraps=self.qt.stop) as stop, \
                patch.object(self.qt, "play", wraps=self.qt.play) as play, \
                patch.object(self.qt, "setSource", wraps=self.qt.setSource) as source:
            self.assertTrue(self.owned.enqueue_next(self.addition))
        stop.assert_not_called()
        play.assert_not_called()
        source.assert_not_called()
        self.assertEqual((self.owned.track.track_id, self.owned.track.position_seconds,
            self.owned.track.position_sampled_at, self.owned.track.lyrics,
            self.owned._epoch, self.owned._stream_epoch, self.qt.source(), self.qt.playbackState(),
            self.owned._prepared_audio, self.owned.status), before)
        self.assertEqual(self.song_ids(), ("0", "added", "1", "2", "3", "4"))
        self.assertEqual(self.owned.track.album_queue[0].track_id, "owned:netease:added")
        self.assertIn(before[-2].lease, self.owned.stream_cache.leases)

    def test_paused_song_stays_paused_and_no_service_call_runs_synchronously(self):
        self.start()
        self.owned.send_playback_command("toggle_play_pause")
        self.service.stream_url.reset_mock()
        self.assertTrue(self.owned.enqueue_next(self.addition))
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertFalse(self.owned.track.is_playing)
        self.service.stream_url.assert_not_called()
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, "owned:netease:added")
        self.perform(self.tasks, "stream")
        self.assertTrue(self.owned.track.is_playing)

    def test_current_pending_stream_is_still_valid_after_enqueue(self):
        self.owned.play_queue(self.songs)
        token, action = self.tasks.jobs[-1]
        self.assertTrue(self.owned.enqueue_next(self.addition))
        self.tasks.completed.emit(token, action(), None)
        self.assertEqual(self.owned.track.track_id, "owned:netease:0")
        self.assertTrue(self.owned.track.is_playing)
        self.assertEqual(self.owned._stream_epoch, self.owned._epoch)

    def test_eof_plays_addition_then_preserves_original_following_songs(self):
        self.start()
        self.owned.enqueue_next(self.addition)
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.owned.track.track_id, "owned:netease:added")
        self.perform(self.tasks, "stream")
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.owned.track.track_id, "owned:netease:1")
        self.assertEqual(self.song_ids(), ("0", "added", "1", "2", "3", "4"))

    def test_latest_addition_is_next_without_losing_prior_additions(self):
        self.start()
        second = replace(self.addition, song_id="second", title="Another result")
        self.owned.enqueue_next(self.addition)
        self.owned.enqueue_next(second)
        self.assertEqual(self.song_ids(), ("0", "second", "added", "1", "2", "3", "4"))
        for expected in ("second", "added", "1"):
            self.owned.send_playback_command("next")
            self.assertEqual(self.owned.track.track_id, f"owned:netease:{expected}")

    def test_repeat_one_obeys_explicit_next_once_then_repeats_added_song(self):
        self.start()
        self.owned.set_playback_mode("repeat_one")
        self.owned.enqueue_next(self.addition)
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.owned.track.track_id, "owned:netease:added")
        self.assertEqual(self.owned.playback_mode, "repeat_one")
        self.perform(self.tasks, "stream")
        epoch = self.owned._epoch
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.owned.track.track_id, "owned:netease:added")
        self.assertEqual(self.owned._epoch, epoch + 1)

    def test_shuffle_keeps_original_plan_and_records_deliberate_next_in_history(self):
        self.owned._random.shuffle = lambda values: values.reverse()
        self.owned.set_playback_mode("shuffle")
        self.start()
        original = tuple(self.owned.queue[i].song_id for i in self.owned._upcoming_indices())
        self.owned.enqueue_next(self.addition)
        self.assertEqual(self.owned.track.album_queue[0].track_id, "owned:netease:added")
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, "owned:netease:added")
        self.assertEqual(tuple(self.owned.queue[i].song_id for i in self.owned._upcoming_indices()), original)
        self.owned.send_playback_command("previous")
        self.assertEqual(self.owned.track.track_id, "owned:netease:0")
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, "owned:netease:added")
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, f"owned:netease:{original[0]}")

    def test_changing_mode_keeps_deliberate_next_priority(self):
        self.start()
        self.owned.enqueue_next(self.addition)
        for mode in ("shuffle", "sequential", "repeat_one", "shuffle"):
            self.owned.set_playback_mode(mode)
            self.assertEqual(self.owned.track.album_queue[0].track_id, "owned:netease:added")
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, "owned:netease:added")

    def test_fm_inflight_recommendations_append_without_overwriting_addition(self):
        self.start(self.songs[:2], fm=True)
        pending = self.owned._fm_prefetch_request
        self.assertIsNotNone(pending)
        self.owned.enqueue_next(self.addition)
        self.prefetch.completed.emit(pending, self.songs[2:], None)
        self.assertEqual(self.song_ids(), ("0", "added", "1", "2", "3", "4"))
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, "owned:netease:added")
        self.assertTrue(self.owned.is_personal_fm())

    def test_fm_history_pruning_remaps_pending_additions(self):
        self.start(self.songs, fm=True)
        self.owned.index = 4
        self.owned._load_song()
        self.assertEqual(self.owned.index, 3)
        self.owned.enqueue_next(self.addition)
        self.owned.send_playback_command("previous")
        self.assertEqual(self.owned.track.track_id, "owned:netease:3")
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, "owned:netease:added")
        self.assertFalse(self.owned._play_next_indices)

    def test_dj_addition_keeps_current_introduction_and_next_genuine_pair(self):
        self.owned.personal_dj_enabled = True
        batch = segments(1) + segments(2)
        self.owned._replace_queue(batch, fm_source="netease", fm_kind="dj")
        self.perform(self.tasks, "stream")
        self.perform(self.tasks, "dj_stream")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        intro = (self.owned._dj_overlay_voices, self.owned._dj_generation,
            self.owned._dj_source_ready, self.dj_qt.source(), self.dj_qt.playbackState())
        self.owned.enqueue_next(self.addition)
        self.assertEqual((self.owned._dj_overlay_voices, self.owned._dj_generation,
            self.owned._dj_source_ready, self.dj_qt.source(), self.dj_qt.playbackState()), intro)
        self.assertEqual(self.owned._next_dj_preload(), ("idle", None))
        self.assertEqual(self.owned.queue[-2:], batch[-2:])
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, "owned:netease:added")
        self.assertFalse(self.owned._dj_overlay_voices)
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-2")
        self.assertEqual(self.owned._dj_overlay_voices[0].introduced_song_id, "song-2")

    def test_dj_preferences_preserve_listener_additions_while_replacing_recommendations(self):
        self.start(self.songs[:2], fm=True)
        self.owned.enqueue_next(self.addition)
        self.owned.set_personal_dj_enabled(True)
        self.assertEqual(self.song_ids(), ("0", "added"))
        self.owned._set_personal_dj_voice_preference("another-voice")
        self.assertEqual(self.song_ids(), ("0", "added"))
        self.owned.set_personal_dj_enabled(False)
        self.assertEqual(self.song_ids(), ("0", "added"))
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, "owned:netease:added")

    def test_old_prefetched_file_is_released_and_late_delivery_cannot_replace_new_next(self):
        self.start(buffered=True)
        token, prepared = self.perform(self.downloads, "audio_prefetch")
        current = self.owned._prepared_audio
        self.owned.enqueue_next(self.addition)
        self.assertIn(prepared.lease, self.owned.stream_cache.released)
        self.assertNotIn(current.lease, self.owned.stream_cache.released)
        self.assertIsNone(self.owned._prefetched_audio)
        late_stream = StreamInfo("netease", "1", "https://fixture.invalid/late", True)
        late = self.owned.stream_cache.prepare(late_stream)
        self.downloads.completed.emit(token, late, None)
        self.assertIn(late.lease, self.owned.stream_cache.released)
        self.assertIsNone(self.owned._prefetched_audio)
        _, queued = self.perform(self.downloads, "audio_prefetch")
        self.assertEqual(queued.stream.song_id, "added")
        self.owned.send_playback_command("next")
        self.assertIs(self.owned._prepared_audio, queued)

    def test_repeat_one_prepares_the_explicit_next_file(self):
        self.start(buffered=True)
        self.owned.set_playback_mode("repeat_one")
        self.owned.enqueue_next(self.addition)
        _, prepared = self.perform(self.downloads, "audio_prefetch")
        self.assertEqual(prepared.stream.song_id, "added")
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertIs(self.owned._prepared_audio, prepared)

    def test_dj_plain_addition_prepares_music_without_a_fabricated_introduction(self):
        self.start((segments(1)[1], *segments(2)), fm=True, buffered=True)
        self.owned.personal_dj_enabled = True
        self.owned._fm_kind = "dj"
        self.owned.enqueue_next(self.addition)
        _, prepared = self.perform(self.downloads, "audio_prefetch")
        self.assertEqual(prepared.stream.song_id, "added")
        self.assertEqual(self.owned._next_dj_preload(), ("idle", None))
        self.owned.send_playback_command("next")
        self.assertIs(self.owned._prepared_audio, prepared)
        self.assertFalse(self.owned._dj_overlay_voices)

    def test_mixed_source_song_uses_its_own_existing_stream_authority(self):
        self.start()
        qq = replace(self.addition, source_id="qq", song_id="qq-added")
        self.assertTrue(self.owned.enqueue_next(qq))
        self.owned.send_playback_command("next")
        self.perform(self.tasks, "stream")
        self.service.stream_url.assert_called_with("qq", "qq-added")
        self.assertEqual(self.owned.track.track_id, "owned:qq:qq-added")

    def test_stop_and_replacement_retire_all_explicit_next_priorities(self):
        self.start()
        self.owned.enqueue_next(self.addition)
        self.owned.play_queue(self.songs)
        self.assertFalse(self.owned._play_next_indices)
        self.owned.enqueue_next(self.addition)
        self.owned.stop()
        self.assertFalse(self.owned._play_next_indices)
        self.assertFalse(self.owned.enqueue_next(self.addition))

    def test_worker_request_dispatches_on_owner_thread_and_stale_request_rejects(self):
        self.start()
        result = []
        worker = threading.Thread(target=lambda: result.append(self.owned.enqueue_next(self.addition)))
        worker.start()
        deadline = time.monotonic() + 1.5
        while worker.is_alive() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        worker.join(timeout=.5)
        self.assertEqual(result, [True])
        self.assertEqual(self.song_ids()[1], "added")
        generation = (self.owned._epoch, self.owned._catalog_epoch,
            self.owned._queue_epoch, self.owned._mode_epoch)
        done, response = threading.Event(), {"generation": generation}
        request = ("enqueue_next", self.addition, self.owned.track.track_id,
            time.monotonic() + 1, done, response)
        self.owned.play_queue(self.songs)
        self.owned._apply_control_request(request)
        self.assertTrue(done.is_set())
        self.assertFalse(response.get("succeeded", False))
        self.assertEqual(self.owned.queue, self.songs)

    def test_browsed_fm_selection_keeps_selected_song_and_continuous_source(self):
        self.assertTrue(self.owned.play_personal_fm_selection("netease", self.songs, 2))
        self.assertEqual(self.owned.track.track_id, "owned:netease:2")
        self.assertTrue(self.owned.is_personal_fm())
        self.assertEqual(self.owned._fm_kind, "fm")
        self.assertEqual(self.owned.queue, self.songs)
        self.perform(self.tasks, "stream")
        request = self.owned._fm_prefetch_request
        self.assertIsNotNone(request)
        incoming = (replace(self.addition, song_id="continued"),)
        self.prefetch.completed.emit(request, incoming, None)
        self.assertEqual(self.owned.queue, self.songs + incoming)

    def test_browsed_fm_selection_keeps_dj_future_genuine_without_inventing_current_intro(self):
        self.owned.set_personal_dj_enabled(True)
        self.assertTrue(self.owned.play_personal_fm_selection("netease", self.songs[:2], 1))
        self.assertEqual(self.owned._fm_kind, "dj")
        self.assertFalse(self.owned._dj_overlay_voices)
        self.assertEqual(self.owned.track.track_id, "owned:netease:1")
        self.perform(self.tasks, "stream")
        request = self.owned._fm_prefetch_request
        self.assertIsNotNone(request)
        incoming = segments(9)
        self.prefetch.completed.emit(request, incoming, None)
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-9")
        self.assertEqual(self.owned._dj_overlay_voices, incoming[:1])

    def test_browsed_fm_selection_rejects_other_sources_speech_and_invalid_index(self):
        self.start()
        original = self.owned.queue, self.owned.track, self.owned._epoch
        for source, songs, index in (("qq", self.songs, 0), ("netease", None, 0),
                ("netease", (), 0), ("netease", self.songs, -1),
                ("netease", self.songs, True), ("netease", self.songs, "1"),
                ("netease", (replace(self.addition, source_id="qq"),), 0),
                ("netease", segments(1), 0)):
            with self.subTest(source=source, songs=songs, index=index):
                self.assertFalse(self.owned.play_personal_fm_selection(source, songs, index))
                self.assertEqual((self.owned.queue, self.owned.track, self.owned._epoch), original)


if __name__ == "__main__":
    unittest.main()
