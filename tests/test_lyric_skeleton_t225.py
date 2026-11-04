"""Loading markers must not survive a terminal display-preparation failure."""
from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from local.music_lyric_preparation_store import LyricPresentationStore
from local.music_surface_window import MusicSurfaceWindow
from api.lyric_diagnostics import log_lyric_event
from api.online_music import LyricPayload, OnlineSong
from local.owned_music_player import OwnedMusicPlayer
from tests.test_music_lyric_preparation import ControlledTasks
from tests.test_music_surface_transition import _TransitionOwner


class LyricSkeletonFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.owner.media_track_id = "owned:netease:2672503107"
        self.owner.timed_lyrics = []
        self.owner.lyrics = ["加载歌词中"]
        self.owner.enable_dual_line_lyrics = False
        self.tasks = ControlledTasks()
        self.store = LyricPresentationStore(self.owner, tasks=self.tasks)
        self.owner._lyric_presentation_store = self.store
        self.window = MusicSurfaceWindow(self.owner)
        self.window._timer.stop()
        self.addCleanup(self.owner.close)
        self.addCleanup(self.store.close)
        self.addCleanup(self.window.shutdown)

    def prepare_current(self):
        self.window._resolve_display_lyrics()
        while self.tasks.actions:
            self.tasks.finish()
        return self.window._resolve_display_lyrics()

    def fail_next_preparation(self):
        self.window._resolve_display_lyrics()
        token, _action = self.tasks.actions.pop(0)
        self.tasks.completed.emit(token, None, "request_failed")

    def test_failed_real_result_releases_cached_loading_marker_and_skeleton(self):
        self.assertEqual(self.prepare_current(), (["加载歌词中"], []))
        self.window._update_lyric_loading_state(100.)
        self.window._update_lyric_loading_state(101.1)
        self.assertTrue(self.window._lyric_loading_visible(101.1))
        self.owner.timed_lyrics = [(float(i * 4), f"Resolved row {i}") for i in range(5)]
        self.owner.lyrics = [text for _start, text in self.owner.timed_lyrics]
        self.fail_next_preparation()
        rows = self.window._resolve_display_lyrics()
        self.assertEqual(rows, (["暂无歌词"], []))
        self.assertFalse(self.window._lyrics_lookup_pending(*rows))
        self.window._update_lyric_loading_state(101.2, *rows)
        self.assertFalse(self.window._lyric_loading_visible(105.))

    def test_failed_same_track_refresh_keeps_established_lyric_ink(self):
        self.owner.timed_lyrics = [(float(i * 4), f"Established row {i}") for i in range(5)]
        self.owner.lyrics = [text for _start, text in self.owner.timed_lyrics]
        established = self.prepare_current()
        self.owner.timed_lyrics = [*self.owner.timed_lyrics, (24., "New same-track row")]
        self.owner.lyrics = [text for _start, text in self.owner.timed_lyrics]
        self.fail_next_preparation()
        self.assertEqual(self.window._resolve_display_lyrics(), established)
        self.assertFalse(self.window._lyrics_lookup_pending(*established))

    def test_debug_reports_provider_store_resize_and_exit_without_lyric_text(self):
        self.prepare_current()
        with patch.dict(os.environ, {"GLUSPOT_LYRIC_DEBUG": "1"}), \
                patch("local.music_surface_window.log_lyric_event") as log:
            self.window._update_lyric_loading_state(100.)
            self.assertEqual(log.call_args.args[2], "provider_pending")
            count = log.call_count
            self.window._update_lyric_loading_state(100.1)
            self.assertEqual(log.call_count, count)
            self.window._update_lyric_loading_state(106.)
            self.assertGreater(log.call_count, count)
            self.window._lyric_resize_frozen = True
            self.window._update_lyric_loading_state(106.1)
            self.assertEqual(log.call_args.args[2], "resize_settle")
            self.window._lyric_resize_frozen = False
            self.owner.timed_lyrics = [(1., "Private resolved lyric")]
            self.owner.lyrics = ["Private resolved lyric"]
            self.fail_next_preparation()
            rows = self.window._resolve_display_lyrics()
            self.window._update_lyric_loading_state(106.2, *rows)
            self.assertEqual(log.call_args.args[2], "result_cycle_exit")
            self.window._update_lyric_loading_state(111., *rows)
            self.assertEqual(log.call_args.args[2], "preparation_failed_exit")
            self.assertTrue(log.call_args.kwargs["store_failed"])
            self.assertNotIn("Private resolved lyric", str(log.call_args_list))


class LyricDebugLogTests(unittest.TestCase):
    def test_flag_disabled_by_default_and_unknown_fields_or_labels_never_log(self):
        with patch.dict(os.environ, {"GLUSPOT_LYRIC_DEBUG": ""}), \
                patch("api.lyric_diagnostics._log") as log:
            log_lyric_event("surface", "owned:netease:2672503107", "resolved", rows=3)
            log.info.assert_not_called()
        with patch.dict(os.environ, {"GLUSPOT_LYRIC_DEBUG": "1"}), \
                patch("api.lyric_diagnostics._log") as log:
            log_lyric_event("surface", "private track", "resolved", rows=3, waiting=False,
                            url="https://private.invalid", error="private exception")
            output = str(log.info.call_args)
            self.assertIn("rows=3", output)
            self.assertIn("waiting=0", output)
            self.assertNotIn("private", output)
            self.assertNotIn("url", output)
            count = log.info.call_count
            log_lyric_event("surface", "private track", "private exception")
            self.assertEqual(log.info.call_count, count)

    def test_owned_lookup_logs_fetch_and_parse_without_payload(self):
        payload = LyricPayload("netease", "2672503107", raw_lrc="private lyric text")
        match = SimpleNamespace(lyrics=("private lyric text",), timed_lyrics=())
        player = SimpleNamespace(service=SimpleNamespace(lyrics=Mock(return_value=payload)),
                                 _parse_lyrics_payload=Mock(return_value=match))
        song = OnlineSong("netease", "2672503107", "Keep Me Warm")
        with patch("local.owned_music_player.log_lyric_event") as log:
            self.assertEqual(OwnedMusicPlayer._fetch_lyrics(player, song, {}), (payload, match))
            self.assertEqual([call.args[2] for call in log.call_args_list], ["fetch_begin", "fetch_ready"])
            self.assertNotIn("private lyric text", str(log.call_args_list))
            player._parse_lyrics_payload.side_effect = ValueError("private exception")
            with self.assertRaises(ValueError):
                OwnedMusicPlayer._fetch_lyrics(player, song, {})
            self.assertEqual(log.call_args.args[2], "parse_failed")
            self.assertNotIn("private exception", str(log.call_args_list))
