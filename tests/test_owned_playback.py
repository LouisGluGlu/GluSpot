from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import CatalogPage, OnlineSong, StreamInfo
from api.providers import EmptyIslandDataProvider, PlaybackSourceProvider, WindowsMediaProvider
from api.models import ColorToken
from local.owned_music_player import OwnedMusicPlayer
from local.playback_entry_controller import startup_destination


class Tasks(QObject):
    completed = Signal(object, object, object)

    def __init__(self):
        super().__init__()
        self.jobs = []
        self.closed = False
        self.accepted = True

    def submit(self, token, action):
        self.jobs.append((token, action))
        return self.accepted

    def close(self):
        self.closed = True


class Player(QObject):
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object, str)

    def __init__(self):
        super().__init__()
        self.url = None
        self.state = QMediaPlayer.PlaybackState.StoppedState
        self.position = 0

    def setAudioOutput(self, audio): pass
    def stop(self): self.state = QMediaPlayer.PlaybackState.StoppedState
    def setSource(self, url): self.url = url
    def play(self): self.state = QMediaPlayer.PlaybackState.PlayingState
    def pause(self): self.state = QMediaPlayer.PlaybackState.PausedState
    def playbackState(self): return self.state
    def isSeekable(self): return True
    def setPosition(self, value): self.position = value


class ThreadRecordingPlayer(Player):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.seekable = True

    def _record(self, name): self.calls.append((name, threading.get_ident()))
    def stop(self):
        self._record("stop")
        super().stop()
    def setSource(self, url):
        self._record("source")
        super().setSource(url)
    def play(self):
        self._record("play")
        super().play()
    def pause(self):
        self._record("pause")
        super().pause()
    def playbackState(self):
        self._record("state")
        return super().playbackState()
    def isSeekable(self):
        self._record("seekable")
        return self.seekable
    def setPosition(self, value):
        self._record("position")
        super().setPosition(value)


class OwnedPlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tasks = Tasks()
        self.artwork_tasks = Tasks()
        self.qt_player = Player()
        self.service = SimpleNamespace(stream_url=lambda *args: None, lyrics=lambda *args: None)
        self.lyrics = LocalLyricsStore(root=Path(self.temp.name))
        self.player = OwnedMusicPlayer(self.service, self.lyrics, player=self.qt_player,
                                      audio=object(), tasks=self.tasks, artwork_tasks=self.artwork_tasks)
        self.songs = (OnlineSong("netease", "1", "第一首", duration_seconds=120),
                      OnlineSong("netease", "2", "第二首", duration_seconds=150))

    def tearDown(self):
        self.player.close()
        self.temp.cleanup()

    def test_stale_stream_cannot_replace_new_song(self):
        self.player.play_queue(self.songs)
        old_token = self.tasks.jobs[-1][0]
        self.player.send_playback_command("next")
        self.tasks.completed.emit(old_token, StreamInfo("netease", "1", "https://example.test/a", True), None)
        self.assertTrue(self.qt_player.url.isEmpty())
        self.assertEqual(self.player.track.track_id, "owned:netease:2")

    def test_stream_starts_before_lyric_request_and_restricted_stream_does_not_play(self):
        self.player.play_queue(self.songs)
        token = self.tasks.jobs[-1][0]
        self.tasks.completed.emit(token, StreamInfo("netease", "1", playable=False), None)
        self.assertEqual(self.player.status, "stream_unavailable")
        self.assertTrue(self.qt_player.url.isEmpty())
        self.tasks.completed.emit(token, StreamInfo("netease", "1", "https://example.test/a", True, True), None)
        self.assertEqual(self.qt_player.state, QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(self.player.status, "trial")
        self.assertEqual(self.tasks.jobs[-1][0][0], "lyrics")

    def test_missing_artwork_has_terminal_miss_and_lyrics_begin_loading(self):
        self.player.play_queue(self.songs)
        self.assertEqual(self.player.track.album_art_lookup_state, "miss")
        self.assertEqual(self.player.track.lyrics, ("加载歌词中",))
        self.assertEqual(self.artwork_tasks.jobs, [])

    def test_artwork_updates_original_colors_and_only_matching_queue_entry(self):
        songs = (OnlineSong("netease", "1", "第一首", artwork_url="https://example.test/cover.png"), self.songs[1])
        self.player.play_queue(songs)
        self.assertEqual(self.player.track.album_art_lookup_state, "pending")
        art_token, action = self.artwork_tasks.jobs[-1]
        colors = ColorToken(200, 30, 50), ColorToken(130, 40, 70)
        with patch("local.owned_music_player.fetch_online_artwork", return_value=b"source-art") as fetch, \
                patch("local.owned_music_player.online_artwork_colors", return_value=colors):
            result = action()
        fetch.assert_called_once_with(songs[0].artwork_url)
        self.artwork_tasks.completed.emit(art_token, result, None)
        self.assertEqual(self.player.track.album_art_bytes, b"source-art")
        self.assertEqual(self.player.track.album_art_source, "original")
        self.assertEqual(self.player.track.album_art_lookup_state, "original")
        self.assertEqual(self.player.track.album_color, colors[0])
        self.assertEqual(self.player.track.album_queue[0].album_art_bytes, b"source-art")
        self.assertEqual(self.player.track.album_queue[1].album_art_bytes, b"")
        self.assertTrue(self.qt_player.url.isEmpty())

    def test_stale_artwork_skips_request_and_cannot_replace_new_song(self):
        songs = (OnlineSong("netease", "1", "第一首", artwork_url="https://example.test/old.png"), self.songs[1])
        self.player.play_queue(songs)
        token, action = self.artwork_tasks.jobs[-1]
        self.player.send_playback_command("next")
        with patch("local.owned_music_player.fetch_online_artwork") as fetch:
            self.assertIsNone(action())
            fetch.assert_not_called()
        self.artwork_tasks.completed.emit(token, (b"old-art", ColorToken(1, 2, 3), ColorToken(4, 5, 6)), None)
        self.assertEqual(self.player.track.track_id, "owned:netease:2")
        self.assertEqual(self.player.track.album_art_bytes, b"")
        self.assertEqual(self.player.track.album_art_lookup_state, "miss")

    def test_artwork_failure_and_full_queue_finish_loading(self):
        song = OnlineSong("netease", "1", "第一首", artwork_url="https://example.test/cover.png")
        for accepted, result, error in ((True, None, None), (True, None, "request_failed"), (False, None, None)):
            with self.subTest(accepted=accepted, error=error):
                self.artwork_tasks.accepted = accepted
                self.player.play_queue((song,))
                token, action = self.artwork_tasks.jobs[-1]
                if accepted:
                    self.artwork_tasks.completed.emit(token, result, error)
                self.assertEqual(self.player.track.album_art_lookup_state, "failed")
                self.assertEqual(self.player.track.album_art_bytes, b"")

    def test_missing_failed_and_rejected_lyrics_finish_without_interrupting_audio(self):
        for result, error, accepted in ((None, None, True), (None, "unsupported", True), (None, None, False)):
            with self.subTest(error=error, accepted=accepted):
                self.tasks.accepted = True
                self.player.play_queue(self.songs)
                stream_token = self.tasks.jobs[-1][0]
                self.tasks.accepted = accepted
                self.tasks.completed.emit(stream_token, StreamInfo("netease", "1", "https://example.test/a", True), None)
                self.assertEqual(self.qt_player.state, QMediaPlayer.PlaybackState.PlayingState)
                if accepted:
                    self.tasks.completed.emit(self.tasks.jobs[-1][0], result, error)
                self.assertEqual(self.player.track.lyrics, ("暂无歌词",))
                self.assertEqual(self.player.track.timed_lyrics, ())
                self.assertEqual(self.player.status, "playing")

    def test_stale_lyrics_cannot_replace_new_song_loading_marker(self):
        self.player.play_queue(self.songs)
        self.tasks.completed.emit(self.tasks.jobs[-1][0], StreamInfo("netease", "1", "https://example.test/a", True), None)
        lyric_token = self.tasks.jobs[-1][0]
        self.player.send_playback_command("next")
        match = self.lyrics.parse_online_lyrics(raw_lrc="[00:01.00]旧歌词")
        self.tasks.completed.emit(lyric_token, match, None)
        self.assertEqual(self.player.track.lyrics, ("加载歌词中",))

    def test_artwork_workers_do_not_hold_stream_or_gui_while_fetch_waits(self):
        from local.music_tasks import MusicTasks
        release, entered = threading.Event(), threading.Event()
        workers = MusicTasks()
        player = OwnedMusicPlayer(self.service, self.lyrics, player=Player(), audio=object(),
            tasks=self.tasks, artwork_tasks=workers)
        def fetch(url):
            entered.set()
            release.wait(2)
            return b""
        try:
            with patch("local.owned_music_player.fetch_online_artwork", side_effect=fetch):
                started = time.monotonic()
                player.play_queue((OnlineSong("netease", "1", "第一首", artwork_url="https://example.test/cover.png"),))
                self.assertLess(time.monotonic() - started, .1)
                self.assertTrue(entered.wait(1))
                self.tasks.completed.emit(self.tasks.jobs[-1][0], StreamInfo("netease", "1", "https://example.test/a", True), None)
                self.assertEqual(player.player.state, QMediaPlayer.PlaybackState.PlayingState)
                self.assertEqual(player.track.album_art_lookup_state, "pending")
                release.set()
        finally:
            release.set()
            player.close()

    def test_position_observations_retain_qt_clock_and_seek_checks_identity(self):
        self.player.play_queue(self.songs)
        self.qt_player.positionChanged.emit(12345)
        track = self.player.visual_data().track
        self.assertEqual(track.position_seconds, 12.345)
        self.assertEqual(track.position_observed_seconds, 12.345)
        self.assertEqual(track.position_source, "owned-player")
        self.assertGreater(track.position_sampled_at, 0)
        self.assertFalse(self.player.seek_playback_position(10, "owned:netease:2"))
        self.assertTrue(self.player.seek_playback_position(999, track.track_id))
        self.assertEqual(self.qt_player.position, 120000)

    def test_bad_stream_scheme_and_closed_reply_cannot_play(self):
        self.player.play_queue(self.songs)
        token = self.tasks.jobs[-1][0]
        self.tasks.completed.emit(token, StreamInfo("netease", "1", "file:///secret", True), None)
        self.assertTrue(self.qt_player.url.isEmpty())
        self.player.close()
        self.tasks.completed.emit(token, StreamInfo("netease", "1", "https://example.test/a", True), None)
        self.assertTrue(self.qt_player.url.isEmpty())

    def test_end_of_queue_fetches_next_personal_fm_batch(self):
        self.service.personal_fm = lambda source: self.songs
        self.player.play_personal_fm()
        token, action = self.tasks.jobs[-1]
        self.tasks.completed.emit(token, action(), None)
        self.assertEqual(self.player._fm_source, "netease")
        self.player.index = 1
        self.qt_player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.tasks.jobs[-1][0][2], "fm")

    def test_playlist_follows_all_pages(self):
        offsets = []
        def page(source, playlist, *, limit, offset):
            offsets.append(offset)
            return CatalogPage((self.songs[0 if offset == 0 else 1],), next_offset=100, has_more=offset == 0)
        self.service.playlist_tracks = page
        self.player.play_playlist("netease", "list")
        token, action = self.tasks.jobs[-1]
        self.tasks.completed.emit(token, action(), None)
        self.assertEqual(offsets, [0, 100])
        self.assertEqual(self.player.queue, self.songs)

    def test_stale_playlist_stops_pagination_after_current_page(self):
        offsets = []
        def page(source, playlist, *, limit, offset):
            offsets.append(offset)
            self.player.stop()
            return CatalogPage((self.songs[0],), next_offset=100, has_more=True)
        self.service.playlist_tracks = page
        self.player.play_playlist("netease", "list")
        token, action = self.tasks.jobs[-1]
        self.assertEqual(action(), ())
        self.assertEqual(offsets, [0])

    def test_queued_old_stream_skips_network_when_new_song_already_selected(self):
        self.service.stream_url = lambda *args: self.fail("stale stream requested")
        self.player.play_queue(self.songs)
        token, action = self.tasks.jobs[-1]
        self.player.send_playback_command("next")
        self.assertIsNone(action())

    def test_daily_requires_real_catalog_result_and_does_not_guess_a_queue(self):
        self.service.daily_recommendations = lambda source: ()
        self.player.play_daily()
        token, action = self.tasks.jobs[-1]
        self.tasks.completed.emit(token, action(), None)
        self.assertEqual(self.player.status, "catalog_unavailable")
        self.assertIsNone(self.player.track)

    def test_yrc_preserves_word_times_with_lrc_fallback(self):
        match = self.lyrics.parse_online_lyrics(raw_yrc="[1000,1000](1000,500,0)你好(1500,500,0)世界",
                                               raw_lrc="[00:01.00]你好世界", source="netease")
        self.assertEqual(len(match.timed_lyrics[0].units), 2)
        self.assertEqual(match.timed_lyrics[0].units[1].start_seconds, 1.5)
        fallback = self.lyrics.parse_online_lyrics(raw_yrc="invalid", raw_lrc="[00:01.00]你好世界")
        self.assertEqual(fallback.timed_lyrics[0].time_seconds, 1)
        self.assertEqual(fallback.timed_lyrics[0].units, ())

    def test_owned_provider_does_not_poll_or_route_external_capture(self):
        self.player.play_queue(self.songs)
        external = EmptyIslandDataProvider()
        external.get_visual_data = lambda: self.fail("external poll while owned")
        external.plugin_provider = SimpleNamespace(plugins=lambda: ())
        external.message_detector = SimpleNamespace(messages=lambda: ())
        provider = PlaybackSourceProvider(external, self.player, lambda key, default: default)
        self.assertEqual(provider.get_visual_data().track.track_id, "owned:netease:1")
        self.assertIsNone(provider.get_audio_frame())
        self.assertFalse(provider.has_live_audio())
        self.assertEqual(provider.get_audio_route_snapshot().capture_scope, "owned-player")
        self.assertFalse(provider.select_audio_output("external"))

    def test_capture_disable_detaches_meter_without_waiting_for_stream_shutdown(self):
        release, entered = threading.Event(), threading.Event()
        def stop():
            entered.set()
            release.wait(2)
        external = WindowsMediaProvider.__new__(WindowsMediaProvider)
        external.audio_analyzer = SimpleNamespace(stop=stop)
        started = time.monotonic()
        external.suspend_live_audio()
        self.assertLess(time.monotonic() - started, .1)
        self.assertIsNone(external.audio_analyzer)
        self.assertTrue(entered.wait(1))
        release.set()


class OwnedControlThreadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.qt_player = ThreadRecordingPlayer()
        self.tasks, self.artwork_tasks = Tasks(), Tasks()
        self.service = SimpleNamespace(stream_url=lambda *args: None, lyrics=lambda *args: None)
        self.player = OwnedMusicPlayer(self.service, LocalLyricsStore(root=Path(self.temp.name)),
            player=self.qt_player, audio=object(), tasks=self.tasks, artwork_tasks=self.artwork_tasks)
        self.songs = (OnlineSong("netease", "1", "first", duration_seconds=120),
                      OnlineSong("netease", "2", "second", duration_seconds=150))
        self.player.play_queue(self.songs)
        self.qt_player.calls.clear()
        self.main_thread = threading.get_ident()
        self.workers = []

    def tearDown(self):
        self.player.close()
        deadline = time.monotonic() + 2.5
        while any(worker.is_alive() for worker in self.workers) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        for worker in self.workers:
            worker.join(.1)
        self.temp.cleanup()

    def start_control(self, action):
        """Observe enqueue on its emitter thread; GUI delivery waits for the test."""
        emitted, finished = threading.Event(), threading.Event()
        request, result = [], []
        def observe(value):
            request.append(value)
            emitted.set()
        self.player._control_requested.connect(observe, Qt.ConnectionType.DirectConnection)
        def run():
            try:
                result.append(action())
            finally:
                finished.set()
        worker = threading.Thread(target=run, daemon=True)
        self.workers.append(worker)
        worker.start()
        self.assertTrue(emitted.wait(1), "worker did not enqueue Qt control")
        self.player._control_requested.disconnect(observe)
        return worker, finished, result, request[0]

    def finish_control(self, control):
        worker, finished, result, _ = control
        deadline = time.monotonic() + 2.5
        while not finished.is_set() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        worker.join(.1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(result), 1)
        return result[0]

    def test_background_pause_next_and_seek_touch_media_only_on_main_thread(self):
        for operation, expected in (("pause", ("state", "pause")),
                                    ("next", ("stop", "source")),
                                    ("seek", ("seekable", "position"))):
            with self.subTest(operation=operation):
                self.player.play_queue(self.songs)
                self.qt_player.state = QMediaPlayer.PlaybackState.PlayingState
                self.qt_player.calls.clear()
                action = (lambda: self.player.seek_playback_position(12.5)) if operation == "seek" else \
                    (lambda: self.player.send_playback_command("toggle_play_pause" if operation == "pause" else "next"))
                control = self.start_control(action)
                self.assertEqual(self.qt_player.calls, [])
                self.assertTrue(self.finish_control(control))
                self.assertEqual(tuple(name for name, _ in self.qt_player.calls), expected)
                self.assertTrue(all(thread == self.main_thread for _, thread in self.qt_player.calls))
                if operation == "pause":
                    self.assertEqual(self.qt_player.state, QMediaPlayer.PlaybackState.PausedState)
                elif operation == "next":
                    self.assertEqual(self.player.track.track_id, "owned:netease:2")
                else:
                    self.assertEqual(self.qt_player.position, 12500)

    def test_background_returns_actual_rejection_from_gui(self):
        control = self.start_control(lambda: self.player.send_playback_command("previous"))
        self.assertFalse(self.finish_control(control))
        self.assertEqual(self.qt_player.calls, [])
        self.qt_player.seekable = False
        control = self.start_control(lambda: self.player.seek_playback_position(10))
        self.assertFalse(self.finish_control(control))
        self.assertEqual(self.qt_player.calls, [("seekable", self.main_thread)])

    def test_queued_command_and_seek_cannot_apply_after_track_changes(self):
        for kind in ("command", "seek"):
            with self.subTest(kind=kind):
                self.player.play_queue(self.songs)
                action = (lambda: self.player.send_playback_command("next")) if kind == "command" else \
                    (lambda: self.player.seek_playback_position(10))
                control = self.start_control(action)
                self.player.play_queue(self.songs, index=1)
                self.qt_player.calls.clear()
                self.assertFalse(self.finish_control(control))
                self.assertEqual(self.qt_player.calls, [])
                self.assertEqual(self.player.track.track_id, "owned:netease:2")

    def test_expired_request_is_not_applied_after_worker_timeout(self):
        control = self.start_control(lambda: self.player.send_playback_command("toggle_play_pause"))
        worker, finished, result, request = control
        worker.join(2.5)  # Deliberately leave the owning Qt loop undelivered.
        self.assertTrue(finished.is_set())
        self.assertEqual(result, [False])
        # Windows' monotonic clock can equal the deadline when Event.wait ends.
        with patch("local.owned_music_player.time.monotonic", return_value=request[3]):
            self.app.processEvents()
        self.assertTrue(request[4].is_set())
        self.assertEqual(self.qt_player.calls, [])

    def test_close_rejects_already_queued_command_without_media_reentry(self):
        control = self.start_control(lambda: self.player.send_playback_command("toggle_play_pause"))
        self.player.close()
        self.qt_player.calls.clear()
        self.assertFalse(self.finish_control(control))
        self.assertEqual(self.qt_player.calls, [])
        self.assertIsNone(self.player.track)

    def test_gui_controls_return_synchronously_without_worker_event_wait(self):
        with patch.object(self.player, "_request_control", side_effect=AssertionError("GUI control queued")):
            self.assertTrue(self.player.send_playback_command("toggle_play_pause"))
            self.assertTrue(self.player.seek_playback_position(7, self.player.track.track_id))
            self.assertTrue(self.player.send_playback_command("next"))
        self.assertTrue(all(thread == self.main_thread for _, thread in self.qt_player.calls))
        self.assertEqual(self.player.track.track_id, "owned:netease:2")


class StartupRoutingTests(unittest.TestCase):
    def test_first_choice_matrix(self):
        for owned, system, playing, expected in (
            (True, False, False, "login"), (True, False, True, "login"),
            (False, True, False, "island"), (False, True, True, "island"),
            (True, True, False, "login"), (True, True, True, "island"),
        ):
            with self.subTest(owned=owned, system=system, playing=playing):
                self.assertEqual(startup_destination(first_run=True, owned=owned, system=system,
                    playing=playing, has_login=False), expected)

    def test_subsequent_launch_matrix(self):
        for owned, system in ((True, False), (False, True), (True, True)):
            for playing, saved in ((False, False), (False, True), (True, False), (True, True)):
                with self.subTest(owned=owned, system=system, playing=playing, saved=saved):
                    self.assertEqual(startup_destination(first_run=False, owned=owned, system=system,
                        playing=playing, has_login=saved), "login" if not playing and not saved else "island")
