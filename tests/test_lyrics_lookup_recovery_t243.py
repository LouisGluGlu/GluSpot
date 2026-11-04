"""Current recording lyric delivery stays bounded when an old request hangs."""
from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from api.lyrics import LocalLyricsStore, LyricsMatch
from api.models import TimedLyricLine


def match(text):
    return LyricsMatch((TimedLyricLine(1., text),), (text,), "netease")


class LyricsLookupRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = LocalLyricsStore(Path(directory.name))
        self.now = 100.
        self.clock = patch("api.lyrics.time.monotonic", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_hung_lookup_retries_once_then_publishes_terminal_no_lyrics(self):
        with patch("api.lyrics.threading.Thread") as threads:
            self.assertTrue(self.store.resolve_in_background("Song", "Artist"))
            self.now = 114.9
            self.assertTrue(self.store.is_lookup_pending("Song", "Artist"))
            self.now = 115.
            self.assertFalse(self.store.is_lookup_pending("Song", "Artist"))
            self.assertTrue(self.store.resolve_in_background("Song", "Artist"))
            self.now = 129.9
            self.assertTrue(self.store.is_lookup_pending("Song", "Artist"))
            self.now = 130.
            self.assertFalse(self.store.is_lookup_pending("Song", "Artist"))
            self.assertFalse(self.store.resolve_in_background("Song", "Artist"))
            self.now = 3700.
            self.assertFalse(self.store.resolve_in_background("Song", "Artist"))
            self.assertEqual(threads.call_count, 2)
        self.assertFalse(self.store._lookup_attempts)
        self.assertEqual(self.store.resolve_cached_or_local("Song", "Artist"), LyricsMatch((), (), ""))

    def test_timeout_without_a_retry_poll_does_not_restart_budget(self):
        with patch("api.lyrics.threading.Thread") as threads:
            self.store.resolve_in_background("Song", "Artist")
            self.now = 135.
            self.assertFalse(self.store.resolve_in_background("Song", "Artist"))
            self.assertFalse(self.store.is_lookup_pending("Song", "Artist"))
            self.assertEqual(threads.call_count, 1)

    def test_retrying_exception_uses_same_pending_budget_and_accepts_recovery(self):
        with patch.object(self.store, "_resolve_online", side_effect=[RuntimeError("fixture"), match("recovered")]) as resolve:
            with patch("api.lyrics.threading.Thread") as threads:
                self.store.resolve_in_background("Song", "Artist")
                action = threads.call_args.kwargs
            action["target"](*action["args"])
        self.assertEqual(resolve.call_count, 2)
        self.assertFalse(self.store.is_lookup_pending("Song", "Artist"))
        self.assertEqual(self.store.resolve_cached_or_local("Song", "Artist").lyrics, ("recovered",))

    def test_expired_result_and_fallback_cannot_overwrite_retry_or_clear_pending(self):
        for complete_retry_first in (False, True):
            with self.subTest(complete_retry_first=complete_retry_first):
                self.store.clear_generated_cache()
                self.now = 100.
                old_started, release_old, retry_started, release_retry = (threading.Event() for _ in range(4))
                workers = []
                real_thread = threading.Thread
                count = 0

                def resolve(_title, _artist, _album, _duration, publish):
                    nonlocal count
                    count += 1
                    if count == 1:
                        old_started.set()
                        release_old.wait(3.)
                        publish(match("expired fallback"))
                        return match("expired final")
                    retry_started.set()
                    release_retry.wait(3.)
                    return match("current retry")

                def thread_factory(**kwargs):
                    worker = real_thread(**kwargs)
                    workers.append(worker)
                    return worker

                with patch.object(self.store, "_resolve_online", side_effect=resolve), \
                        patch("api.lyrics.threading.Thread", side_effect=thread_factory):
                    try:
                        self.store.resolve_in_background("Song", "Artist")
                        self.assertTrue(old_started.wait(2.))
                        self.now = 115.
                        self.assertFalse(self.store.is_lookup_pending("Song", "Artist"))
                        self.assertTrue(self.store.resolve_in_background("Song", "Artist"))
                        self.assertTrue(retry_started.wait(2.))
                        if complete_retry_first:
                            release_retry.set()
                            workers[1].join(2.)
                        release_old.set()
                        workers[0].join(2.)
                        if not complete_retry_first:
                            self.assertTrue(self.store.is_lookup_pending("Song", "Artist"))
                            self.assertFalse(self.store._resolved_cache)
                            self.assertEqual(list(self.store.generated_root.glob("*.lrc")), [])
                            release_retry.set()
                            workers[1].join(2.)
                    finally:
                        release_old.set()
                        release_retry.set()
                        for worker in workers:
                            worker.join(2.)
                self.assertFalse(self.store.is_lookup_pending("Song", "Artist"))
                self.assertEqual(self.store.resolve_cached_or_local("Song", "Artist").lyrics, ("current retry",))
                self.assertIn("current retry", (self.store.generated_root / "artist - song.lrc").read_text(encoding="utf-8"))

    def test_rapid_switches_and_recordings_have_independent_retry_state(self):
        with patch("api.lyrics.threading.Thread") as threads:
            for index in range(6):
                self.assertTrue(self.store.resolve_in_background(f"Song {index}", "Artist"))
            self.now = 115.
            self.assertFalse(self.store.is_lookup_pending("Song 5", "Artist"))
            self.assertTrue(self.store.resolve_in_background("Song 5", "Artist"))
            # An expired A worker cannot publish its fallback into B or make
            # the current recording's retry appear completed.
            with patch.object(self.store, "_resolve_online", side_effect=AssertionError("expired network work")):
                old = threads.call_args_list[0].kwargs
                old["target"](*old["args"])
            self.assertTrue(self.store.is_lookup_pending("Song 5", "Artist"))
            self.assertTrue(self.store.resolve_in_background("Song 5", "Artist", "Different recording", 300.))
            self.assertEqual(threads.call_count, 8)

    def test_late_result_after_terminal_timeout_cannot_resurrect_generated_file(self):
        with patch("api.lyrics.threading.Thread") as threads:
            self.store.resolve_in_background("Song", "Artist")
            old = threads.call_args.kwargs
        self.now = 130.
        self.assertFalse(self.store.is_lookup_pending("Song", "Artist"))
        with patch.object(self.store, "_resolve_online", side_effect=AssertionError("expired network work")):
            old["target"](*old["args"])
        self.assertFalse(self.store._resolved_cache)
        self.assertEqual(list(self.store.generated_root.glob("*.lrc")), [])
