from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from enum import IntEnum
import threading
import time
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from local.windows_smtc import (SmtcSnapshot, WindowsSmtcBridge, _NativeSmtcSession,
    _SmtcWorker, _owned_key, owned_smtc_snapshot)


class Owned(QObject):
    changed = Signal()
    statusChanged = Signal(str)

    def __init__(self):
        super().__init__()
        self.track = SimpleNamespace(track_id="owned:netease:1", title="第一首", artist="歌手",
            album_art_bytes=b"", position_seconds=12.25, duration_seconds=180.0, is_playing=True)
        self.queue = (SimpleNamespace(source_id="netease", song_id="1", album="专辑"),)
        self._epoch = self._catalog_epoch = self._queue_epoch = self._mode_epoch = 1
        self.status, self._closed, self._autoplay_after_load = "playing", False, True
        self._stream_recovery = None
        self.player = SimpleNamespace(isSeekable=lambda: True,
            playbackState=lambda: SimpleNamespace(name="StoppedState"))
        self.voice, self.speaking = False, False
        self.commands, self.seeks = [], []

    def _is_voice_segment(self): return self.voice
    def is_personal_dj_speaking(self): return self.speaking

    def send_playback_command(self, command):
        self.commands.append((command, threading.get_ident()))
        if command == "toggle_play_pause":
            self.track.is_playing = not self.track.is_playing
            self._autoplay_after_load = not self._autoplay_after_load
        return True

    def seek_playback_position(self, value, track_id):
        self.seeks.append((value, track_id, threading.get_ident()))
        return True


class Mailbox:
    def __init__(self, callback):
        self.callback = callback
        self.snapshots, self.closed, self.last_error = [], False, ""
    def submit(self, snapshot): self.snapshots.append(snapshot)
    def close(self): self.closed = True


class WindowsSmtcBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owned, self.active = Owned(), True
        self.bridge = WindowsSmtcBridge(self.owned, lambda: self.active, worker_factory=Mailbox)
        self.mailbox = self.bridge._worker

    def tearDown(self):
        self.bridge.close()

    def request(self, command, value=None, *, key=None, deadline=None):
        return (key or _owned_key(self.owned), command, value,
            time.monotonic() + 2.0 if deadline is None else deadline)

    def test_metadata_uses_associated_song_and_actual_clock(self):
        snapshot = self.mailbox.snapshots[-1]
        self.assertEqual((snapshot.title, snapshot.artist, snapshot.album), ("第一首", "歌手", "专辑"))
        self.assertEqual((snapshot.position, snapshot.duration, snapshot.status), (12.25, 180.0, "playing"))

    def test_disabled_source_removes_session_instead_of_publishing_external_data(self):
        self.active = False
        self.bridge.refresh()
        self.assertIsNone(self.mailbox.snapshots[-1])
        self.bridge._apply_command(self.request("next"))
        self.assertEqual(self.owned.commands, [])

    def test_stopped_owned_player_removes_session(self):
        self.owned.track = None
        self.owned.changed.emit()
        self.assertIsNone(self.mailbox.snapshots[-1])

    def test_small_clock_updates_are_coalesced_but_seek_and_metadata_publish(self):
        initial = len(self.mailbox.snapshots)
        self.owned.track.position_seconds = 12.3
        self.owned.changed.emit()
        self.assertEqual(len(self.mailbox.snapshots), initial)
        self.owned.track.position_seconds = 20.0
        self.owned.changed.emit()
        self.owned.track.title = "新标题"
        self.owned.changed.emit()
        self.assertEqual(len(self.mailbox.snapshots), initial + 2)

    def test_play_pause_are_idempotent(self):
        self.bridge._apply_command(self.request("play"))
        self.assertEqual(self.owned.commands, [])
        self.bridge._apply_command(self.request("pause"))
        self.bridge._apply_command(self.request("pause"))
        self.bridge._apply_command(self.request("play"))
        self.assertEqual([call[0] for call in self.owned.commands], ["toggle_play_pause", "toggle_play_pause"])

    def test_pause_during_catalog_loading_changes_existing_resume_intent(self):
        self.owned.track.is_playing = False
        self.owned.status = "loading_catalog"
        self.bridge._apply_command(self.request("play"))
        self.assertEqual(self.owned.commands, [])
        self.bridge._apply_command(self.request("pause"))
        self.assertEqual(self.owned.commands[0][0], "toggle_play_pause")

    def test_pause_recovery_uses_resume_intent(self):
        self.owned.track.is_playing = False
        self.owned._stream_recovery = SimpleNamespace(resume=True)
        self.bridge._apply_command(self.request("pause"))
        self.assertEqual(self.owned.commands[0][0], "toggle_play_pause")

    def test_stale_song_generation_and_expired_callbacks_are_discarded(self):
        old = self.request("next")
        self.owned._epoch += 1
        self.bridge._apply_command(old)
        self.bridge._apply_command(self.request("previous", deadline=time.monotonic() - 1))
        self.assertEqual(self.owned.commands, [])

    def test_valid_navigation_uses_existing_owned_transport(self):
        self.bridge._apply_command(self.request("previous"))
        self.bridge._apply_command(self.request("next"))
        self.assertEqual([call[0] for call in self.owned.commands], ["previous", "next"])

    def test_native_worker_callback_is_queued_to_gui_thread(self):
        gui_thread = threading.get_ident()
        thread = threading.Thread(target=lambda: self.mailbox.callback(self.request("next")))
        thread.start()
        thread.join()
        self.assertEqual(self.owned.commands, [])
        self.app.processEvents()
        self.assertEqual(self.owned.commands, [("next", gui_thread)])

    def test_seek_preserves_track_guard(self):
        self.bridge._apply_command(self.request("seek", 44.0))
        self.assertEqual(self.owned.seeks, [(44.0, "owned:netease:1", threading.get_ident())])

    def test_close_disconnects_and_drops_pending_callbacks(self):
        self.bridge.close()
        self.bridge._apply_command(self.request("next"))
        self.owned.changed.emit()
        self.assertTrue(self.mailbox.closed)
        self.assertEqual(self.owned.commands, [])

    def test_speech_clock_is_not_seekable_and_loading_reports_changing(self):
        self.owned.voice = True
        self.owned.track.is_playing = False
        self.owned.status = "loading_stream"
        snapshot = owned_smtc_snapshot(self.owned, True)
        self.assertFalse(snapshot.seekable)
        self.assertEqual(snapshot.status, "changing")
        self.owned.speaking = True
        self.assertEqual(owned_smtc_snapshot(self.owned, True).status, "playing")

    def test_invalid_timeline_values_are_bounded(self):
        self.owned.track.position_seconds = float("inf")
        self.owned.track.duration_seconds = float("nan")
        snapshot = owned_smtc_snapshot(self.owned, True)
        self.assertEqual((snapshot.position, snapshot.duration, snapshot.seekable), (0.0, 0.0, False))

    def test_legacy_voice_decoder_keeps_system_pause_semantics(self):
        self.owned.track.is_playing, self.owned.voice = False, True
        self.owned.player.playbackState = lambda: SimpleNamespace(name="PlayingState")
        self.assertEqual(owned_smtc_snapshot(self.owned, True).status, "playing")
        self.bridge._apply_command(self.request("pause"))
        self.assertEqual(self.owned.commands[0][0], "toggle_play_pause")


class Button(IntEnum):
    PLAY, PAUSE, STOP, RECORD, FAST_FORWARD, REWIND, NEXT, PREVIOUS = range(8)


class Updater:
    def __init__(self):
        self.music_properties = SimpleNamespace()
        self.thumbnail = None
        self.updates = 0
    def clear_all(self): self.thumbnail = None
    def update(self): self.updates += 1


class Controls:
    def __init__(self):
        self.display_updater, self.events, self.removed, self.timeline = Updater(), {}, [], None
    def add_button_pressed(self, callback): self.events["button"] = callback; return 11
    def add_playback_position_change_requested(self, callback): self.events["seek"] = callback; return 12
    def remove_button_pressed(self, token): self.removed.append(token)
    def remove_playback_position_change_requested(self, token): self.removed.append(token)
    def update_timeline_properties(self, timeline): self.timeline = timeline


class NativePlayer:
    def __init__(self):
        self.command_manager = SimpleNamespace(is_enabled=True)
        self.system_media_transport_controls = Controls()
        self.closed, self.source = False, None
    def close(self): self.closed = True


class NativeSmtcSessionTests(unittest.TestCase):
    def setUp(self):
        self.sdk = SimpleNamespace(MediaPlayer=NativePlayer, Button=Button,
            PlaybackStatus=SimpleNamespace(PLAYING=3, PAUSED=4, CHANGING=1, STOPPED=2, CLOSED=0),
            PlaybackType=SimpleNamespace(MUSIC=1), Timeline=SimpleNamespace)
        self.requests = []
        self.session = _NativeSmtcSession(self.requests.append, sdk=self.sdk)
        self.controls = self.session.controls
        self.snapshot = SmtcSnapshot(("owned:netease:1", 1), "Title", "Artist", "Album", b"",
            "playing", 10.0, 180.0, True)

    def tearDown(self): self.session.close()

    def test_native_transport_has_no_source_and_never_decodes_audio(self):
        asyncio.run(self.session.publish(self.snapshot))
        self.assertIsNone(self.session.player.source)
        self.assertTrue(self.session.player.is_muted)
        self.assertFalse(self.session.player.command_manager.is_enabled)
        self.assertTrue(self.controls.is_enabled)
        self.assertFalse(self.controls.is_stop_enabled)

    def test_metadata_and_timeline_are_distinct_from_audio_source(self):
        asyncio.run(self.session.publish(self.snapshot))
        self.assertEqual(self.controls.display_updater.music_properties.title, "Title")
        self.assertEqual(self.controls.display_updater.music_properties.album_title, "Album")
        self.assertEqual(self.controls.timeline.position, timedelta(seconds=10))
        self.assertEqual(self.controls.timeline.max_seek_time, timedelta(seconds=180))

    def test_position_only_update_does_not_rewrite_metadata(self):
        asyncio.run(self.session.publish(self.snapshot))
        asyncio.run(self.session.publish(replace(self.snapshot, position=11.0)))
        self.assertEqual(self.controls.display_updater.updates, 1)
        self.assertEqual(self.controls.timeline.position, timedelta(seconds=11))

    def test_new_song_without_artwork_clears_old_thumbnail(self):
        asyncio.run(self.session.publish(self.snapshot))
        self.controls.display_updater.thumbnail = object()
        asyncio.run(self.session.publish(replace(self.snapshot, key=("owned:netease:2", 2))))
        self.assertIsNone(self.controls.display_updater.thumbnail)

    def test_buttons_and_seek_carry_published_generation(self):
        asyncio.run(self.session.publish(self.snapshot))
        for button in (Button.PLAY, Button.PAUSE, Button.NEXT, Button.PREVIOUS, Button.STOP):
            self.controls.events["button"](None, SimpleNamespace(button=button))
        self.controls.events["seek"](None, SimpleNamespace(requested_playback_position=timedelta(seconds=30)))
        self.assertEqual([r[1] for r in self.requests], ["play", "pause", "next", "previous", "seek"])
        self.assertTrue(all(r[0] == self.snapshot.key for r in self.requests))
        self.assertEqual(self.requests[-1][2], 30.0)

    def test_nonseekable_snapshot_rejects_seek_callback(self):
        asyncio.run(self.session.publish(replace(self.snapshot, seekable=False)))
        self.controls.events["seek"](None, SimpleNamespace(requested_playback_position=timedelta(seconds=30)))
        self.assertEqual(self.controls.timeline.max_seek_time, timedelta())
        self.assertEqual(self.requests, [])

    def test_close_unregisters_disables_and_rejects_late_events(self):
        asyncio.run(self.session.publish(self.snapshot))
        self.session.close()
        self.controls.events["button"](None, SimpleNamespace(button=Button.NEXT))
        self.assertEqual(self.controls.removed, [11, 12])
        self.assertFalse(self.controls.is_enabled)
        self.assertEqual(self.controls.playback_status, 0)
        self.assertTrue(self.session.player.closed)
        self.assertEqual(self.requests, [])


class SmtcWorkerTests(unittest.TestCase):
    def test_slow_os_publication_coalesces_and_closes_on_worker_thread(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        publications, threads = [], []
        class Session:
            def __init__(self, _callback): threads.append(threading.get_ident())
            async def publish(self, snapshot):
                publications.append(snapshot)
                if snapshot == "first":
                    entered.set()
                    release.wait(2.0)
                else:
                    finished.set()
            def close(self): threads.append(threading.get_ident())
        worker = _SmtcWorker(lambda _request: None, session_factory=Session)
        try:
            worker.submit("first")
            self.assertTrue(entered.wait(2.0))
            for i in range(1000): worker.submit(i)
            release.set()
            self.assertTrue(finished.wait(2.0))
        finally:
            release.set()
            worker.close()
        self.assertEqual(publications, ["first", 999])
        self.assertEqual(len(set(threads)), 1)
        self.assertNotEqual(threads[0], threading.get_ident())

    def test_inactive_snapshot_retires_native_session(self):
        published, retired = threading.Event(), threading.Event()
        class Session:
            def __init__(self, _callback): pass
            async def publish(self, _snapshot): published.set()
            def close(self): retired.set()
        worker = _SmtcWorker(lambda _request: None, session_factory=Session)
        try:
            worker.submit("playing")
            self.assertTrue(published.wait(2.0))
            worker.submit(None)
            self.assertTrue(retired.wait(2.0))
        finally:
            worker.close()


if __name__ == "__main__":
    unittest.main()
