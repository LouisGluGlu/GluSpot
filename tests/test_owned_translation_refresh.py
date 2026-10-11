"""Session lyric preferences reparse one current payload without transport I/O."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import LyricPayload, OnlineSong, StreamInfo
from api.providers import PlaybackSourceProvider
from local.owned_music_player import OwnedMusicPlayer
from local.music_tasks import MusicTasks
from tests.test_owned_playback import Tasks, ThreadRecordingPlayer


class OwnedTranslationRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.settings = {"v2_enable_dual_line_lyrics": False,
            "v2_dual_line_secondary_source": "translation",
            "v2_owned_playback_enabled": True, "v2_system_capture_enabled": True}
        self.getter = lambda key, default=None: self.settings.get(key, default)
        self.payload = LyricPayload("netease", "1", raw_lrc="[00:01.00]Shine on",
            raw_yrc="[1000,1000](1000,500,0)Shine (1500,500,0)on",
            translation="[00:01.00]继续闪耀", romanization="[00:01.00]shine romanized", source="fixture")
        self.service = SimpleNamespace(lyrics=Mock(return_value=self.payload))
        self.store = LocalLyricsStore(Path(self.folder.name) / "manual",
            generated_root=Path(self.folder.name) / "generated", settings_getter=self.getter)
        self.tasks, self.qt_player = Tasks(), ThreadRecordingPlayer()
        self.player = OwnedMusicPlayer(self.service, self.store, player=self.qt_player,
            audio=object(), tasks=self.tasks, artwork_tasks=Tasks(), prefetch_tasks=Tasks(), stream_cache=None)
        self.addCleanup(self.player.close)
        self.system = SimpleNamespace(suspend_live_audio=Mock(), stop_audio_cast=Mock(),
            _request_immediate_media_refresh=Mock(return_value="system-refresh"))
        self.provider = PlaybackSourceProvider(self.system, self.player, self.getter)
        self.addCleanup(self.provider._owned_audio_bridge.close)
        self.songs = (OnlineSong("netease", "1", "One", duration_seconds=120),
            OnlineSong("netease", "2", "Two", duration_seconds=150))

    def start_lyrics(self):
        self.player.play_queue(self.songs)
        stream = self.tasks.jobs[-1][0]
        self.tasks.completed.emit(stream, StreamInfo("netease", "1", "https://example.test/audio", True), None)
        self.assertEqual(self.tasks.jobs[-1][0][0], "lyrics")
        return self.tasks.jobs[-1]

    def finish(self, job=None):
        token, action = self.tasks.jobs[-1] if job is None else job
        self.tasks.completed.emit(token, action(), None)

    def load_lyrics(self):
        self.finish(self.start_lyrics())
        self.assertEqual(self.player.track.lyrics, ("Shine on",))
        self.assertIs(self.player._lyric_payload, self.payload)

    def refresh(self, enabled):
        self.settings["v2_enable_dual_line_lyrics"] = enabled
        return self.provider._request_immediate_media_refresh()

    def test_on_off_and_content_choices_reuse_payload_keep_clock_units_and_transport(self):
        self.load_lyrics()
        self.player.track = replace(self.player.track, position_seconds=37.5,
            position_observed_seconds=37.5, position_sampled_at=12.25)
        self.qt_player.position = 37500
        self.qt_player.calls.clear()
        before = self.player.track
        units = before.timed_lyrics[0].units
        epochs = (self.player._epoch, self.player._catalog_epoch, self.player._queue_epoch)
        stream_jobs = sum(token[0] == "stream" for token, _ in self.tasks.jobs)
        for enabled, source, expected in ((True, "translation", "继续闪耀"),
                (False, "translation", "Shine on"), (True, "transliteration", "shine romanized"),
                (True, "none", "Shine on"), (True, "translation", "继续闪耀")):
            self.settings["v2_dual_line_secondary_source"] = source
            self.assertTrue(self.refresh(enabled))
            self.finish()
            self.assertIn(expected, self.player.track.lyrics[0])
            self.assertEqual(self.player.track.timed_lyrics[0].units, units)
            self.assertEqual(replace(self.player.track, lyrics=before.lyrics,
                timed_lyrics=before.timed_lyrics, lyrics_source=before.lyrics_source), before)
        self.assertEqual(self.service.lyrics.call_count, 1)
        self.assertEqual(sum(token[0] == "stream" for token, _ in self.tasks.jobs), stream_jobs)
        self.assertEqual((self.player._epoch, self.player._catalog_epoch, self.player._queue_epoch), epochs)
        self.assertEqual(self.qt_player.position, 37500)
        self.assertEqual(self.qt_player.calls, [])
        self.system._request_immediate_media_refresh.assert_not_called()

    def test_toggle_during_first_fetch_reparses_latest_preference_without_second_fetch(self):
        initial = self.start_lyrics()
        old_result = initial[1]()  # The source finished before the button changed.
        self.assertEqual(old_result[1].lyrics, ("Shine on",))
        job_count = len(self.tasks.jobs)
        self.assertTrue(self.refresh(True))
        self.assertEqual(len(self.tasks.jobs), job_count)
        self.tasks.completed.emit(initial[0], old_result, None)
        self.assertEqual(self.player.track.lyrics, ("加载歌词中",))
        self.assertEqual(self.tasks.jobs[-1][0][0], "lyrics_refresh")
        self.finish()
        self.assertIn("继续闪耀", self.player.track.lyrics[0])
        self.assertEqual(self.service.lyrics.call_count, 1)

    def test_many_toggles_coalesce_one_worker_and_reject_a_completed_old_preference(self):
        self.load_lyrics()
        self.refresh(True)
        first = self.tasks.jobs[-1]
        old_result = first[1]()
        job_count = len(self.tasks.jobs)
        for enabled in (False, True, False, True, False):
            self.refresh(enabled)
        self.assertEqual(len(self.tasks.jobs), job_count)
        self.tasks.completed.emit(first[0], old_result, None)
        self.assertEqual(self.player.track.lyrics, ("Shine on",))
        self.assertEqual(len(self.tasks.jobs), job_count + 1)
        self.finish()
        self.assertEqual(self.player.track.lyrics, ("Shine on",))
        self.assertIsNone(self.player._lyrics_parse_pending)
        self.assertEqual(self.service.lyrics.call_count, 1)

    def test_rejected_and_failed_parse_leave_previous_lyrics_and_transport_intact(self):
        self.load_lyrics()
        before = self.player.track
        self.qt_player.calls.clear()
        self.tasks.accepted = False
        self.assertFalse(self.refresh(True))
        self.assertEqual(self.player.track, before)
        self.assertIsNone(self.player._lyrics_parse_pending)
        self.tasks.accepted = True
        self.assertTrue(self.refresh(True))
        self.tasks.completed.emit(self.tasks.jobs[-1][0], None, "request_failed")
        self.assertEqual(self.player.track, before)
        self.assertIsNone(self.player._lyrics_parse_pending)
        self.assertEqual(self.qt_player.calls, [])
        self.assertTrue(self.refresh(True))
        self.finish()
        self.assertIn("继续闪耀", self.player.track.lyrics[0])

    def test_rejected_or_failed_first_reparse_terminates_loading_without_interrupting_audio(self):
        for failure in ("rejected", "parse_error"):
            with self.subTest(failure=failure):
                self.tasks.accepted = True
                initial = self.start_lyrics()
                result = initial[1]()
                self.refresh(True)
                self.qt_player.calls.clear()
                if failure == "rejected":
                    self.tasks.accepted = False
                self.tasks.completed.emit(initial[0], result, None)
                if failure == "parse_error":
                    self.tasks.completed.emit(self.tasks.jobs[-1][0], None, "request_failed")
                self.assertEqual(self.player.track.lyrics, ("暂无歌词",))
                self.assertEqual(self.player.status, "playing")
                self.assertIsNone(self.player._lyrics_parse_pending)
                self.assertEqual(self.qt_player.calls, [])
                self.assertIs(self.player._lyric_payload, self.payload)
                self.settings["v2_enable_dual_line_lyrics"] = False

    def test_song_change_stop_close_and_failure_clear_payload_and_reject_old_parse(self):
        for action in ("next", "same_song_queue", "stop", "close", "failure"):
            with self.subTest(action=action):
                self.player._closed = False
                self.load_lyrics()
                self.refresh(True)
                pending = self.tasks.jobs[-1]
                old_result = pending[1]()
                if action == "next":
                    self.player.send_playback_command("next")
                elif action == "same_song_queue":
                    self.player.play_queue(self.songs)
                elif action == "stop":
                    self.player.stop()
                elif action == "close":
                    self.player.close()
                else:
                    from PySide6.QtMultimedia import QMediaPlayer
                    self.player._error(QMediaPlayer.Error.FormatError)
                current = self.player.track
                self.assertIsNone(self.player._lyric_payload)
                self.assertIsNone(self.player._lyrics_parse_pending)
                self.tasks.completed.emit(pending[0], old_result, None)
                self.assertEqual(self.player.track, current)
                self.assertIsNone(self.player._lyric_payload)
                self.settings["v2_enable_dual_line_lyrics"] = False

    def test_old_source_response_cannot_cache_across_song_epoch_or_identity(self):
        first = self.start_lyrics()
        result = first[1]()
        self.player.send_playback_command("next")
        current = self.player.track
        self.tasks.completed.emit(first[0], result, None)
        self.assertEqual(self.player.track, current)
        self.assertIsNone(self.player._lyric_payload)
        for source_id, song_id in (("qq", "1"), ("netease", "other")):
            self.start_lyrics()
            self.tasks.completed.emit(self.tasks.jobs[-1][0],
                (replace(self.payload, source_id=source_id, song_id=song_id), result[1]), None)
            self.assertIsNone(self.player._lyric_payload)
            self.assertEqual(self.player.track.lyrics, ("暂无歌词",))

    def test_source_provider_preserves_system_refresh_when_owned_is_idle_or_disabled(self):
        self.assertEqual(self.provider._request_immediate_media_refresh(), "system-refresh")
        self.player.play_queue(self.songs)
        self.settings["v2_owned_playback_enabled"] = False
        self.assertEqual(self.provider._request_immediate_media_refresh(), "system-refresh")
        self.assertEqual(self.system._request_immediate_media_refresh.call_count, 2)

    def test_parse_runs_only_in_submitted_action_not_refresh_or_completion(self):
        self.load_lyrics()
        with patch.object(self.store, "parse_online_lyrics", side_effect=AssertionError("GUI parsing")):
            self.assertTrue(self.refresh(True))
        token, action = self.tasks.jobs[-1]
        result = action()
        with patch.object(self.store, "parse_online_lyrics", side_effect=AssertionError("GUI parsing")):
            self.tasks.completed.emit(token, result, None)
        self.assertIn("继续闪耀", self.player.track.lyrics[0])

    def test_worker_uses_captured_preferences_without_mutating_shared_store_getter(self):
        self.load_lyrics()
        self.assertTrue(self.refresh(True))
        original_getter = self.store.settings_getter
        forbidden = Mock(side_effect=AssertionError("live preferences read on worker"))
        self.store.settings_getter = forbidden
        try:
            self.finish()
            self.assertIn("继续闪耀", self.player.track.lyrics[0])
            self.assertIs(self.store.settings_getter, forbidden)
            forbidden.assert_not_called()
        finally:
            self.store.settings_getter = original_getter

    def test_actual_background_parse_coalesces_while_gui_keeps_responding(self):
        self.load_lyrics()
        self.tasks.completed.disconnect(self.player._completed)
        workers = MusicTasks(self.player)
        self.player.tasks = workers
        workers.completed.connect(self.player._completed)
        entered, release = threading.Event(), threading.Event()
        parsed_threads = []
        original = LocalLyricsStore.parse_online_lyrics

        def parse(store, **kwargs):
            parsed_threads.append(threading.get_ident())
            if len(parsed_threads) == 1:
                entered.set()
                release.wait(2)
            return original(store, **kwargs)

        self.qt_player.calls.clear()
        try:
            with patch.object(LocalLyricsStore, "parse_online_lyrics", parse):
                self.assertTrue(self.refresh(True))
                self.assertTrue(entered.wait(1))
                # The GUI can change preferences while the first parse waits.
                self.assertTrue(self.refresh(False))
                self.app.processEvents()
                self.assertEqual(self.player.track.lyrics, ("Shine on",))
                release.set()
                deadline = time.monotonic() + 2
                while self.player._lyrics_parse_pending is not None and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.001)
                self.assertIsNone(self.player._lyrics_parse_pending)
                self.assertEqual(len(parsed_threads), 2)
                self.assertTrue(all(value != threading.get_ident() for value in parsed_threads))
                self.assertEqual(self.player.track.lyrics, ("Shine on",))
                self.assertEqual(self.qt_player.calls, [])
                self.assertEqual(self.service.lyrics.call_count, 1)
        finally:
            release.set()
            workers.close()


if __name__ == "__main__":
    unittest.main()
