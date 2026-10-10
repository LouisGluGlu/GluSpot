from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from api.lyrics import LocalLyricsStore, LyricsMatch
from api.models import TimedLyricLine, TimedLyricUnit


def _match(text: str = "cached line") -> LyricsMatch:
    row = TimedLyricLine(1.0, text)
    return LyricsMatch((row,), (text,), "netease")


class LyricsCacheClearTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.store = LocalLyricsStore(self.base / "library", generated_root=self.base / "generated")
        self.threads: list[threading.Thread] = []
        self.releases: list[threading.Event] = []
        real_thread = threading.Thread

        def track_thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            self.threads.append(thread)
            return thread

        self.thread_patch = patch("api.lyrics.threading.Thread", side_effect=track_thread)
        self.thread_patch.start()
        self.addCleanup(self.thread_patch.stop)
        self.addCleanup(self._release_workers)

    def _release_workers(self):
        for release in self.releases:
            release.set()
        for thread in self.threads:
            thread.join(3.0)

    def _join(self, index: int):
        self.threads[index].join(3.0)
        self.assertFalse(self.threads[index].is_alive(), "lyric worker did not settle")

    def _controlled_lookup(self, results: tuple[LyricsMatch, ...], publish_old_fallback: bool = False):
        started = [threading.Event() for _ in results]
        releases = [threading.Event() for _ in results]
        self.releases.extend(releases)
        call_lock = threading.Lock()
        count = 0

        def lookup(*args):
            nonlocal count
            with call_lock:
                index = count
                count += 1
            started[index].set()
            if not releases[index].wait(3.0):
                raise AssertionError("test did not release lyric lookup")
            if index == 0 and publish_old_fallback and len(args) > 4:
                args[4](_match("old fallback"))
            return results[index]

        self.store._resolve_online = lookup
        return started, releases

    def test_clear_generated_files_and_all_memory_but_preserve_manual_and_source_stats(self):
        self.store._persist_match("Song", "Artist", "", _match())
        generated = self.store.generated_root / "artist - song.lrc"
        size = generated.stat().st_size
        manual = self.store.root / "artist - manual.lrc"
        manual_text = "[00:01.00]hand authored\n"
        manual.write_text(manual_text, encoding="utf-8")
        stats = self.store.generated_root / ".source_hits.json"
        stats.write_text('{"netease": 7}', encoding="utf-8")
        before_counts = dict(self.store._source_hit_counts)
        self.store.resolve_cached_or_local("Song", "Artist")
        self.store.resolve_cached_or_local("Manual", "Artist")
        self.store._pending_keys.add("pending")
        self.store._enrich_pending_keys.add("enrich")
        self.store._enrich_attempted_at["enrich"] = 1.0
        self.store._enrich_retry_seconds["enrich"] = 30.0
        self.store._missed_at["missed"] = (1.0, float("inf"))
        self.store._local_miss_cache["missing"] = ()
        self.store._lyric_index_cache["old"] = (0, {})

        result = self.store.clear_generated_cache()

        self.assertEqual(result, {"deleted_files": 1, "deleted_bytes": size, "errors": []})
        self.assertFalse(generated.exists())
        self.assertEqual(manual.read_text(encoding="utf-8"), manual_text)
        self.assertEqual(stats.read_text(encoding="utf-8"), '{"netease": 7}')
        self.assertEqual(self.store._source_hit_counts, before_counts)
        for name in (
            "_resolved_cache", "_resolved_local_paths", "_pending_keys", "_enrich_pending_keys",
            "_enrich_attempted_at", "_enrich_retry_seconds", "_missed_at", "_local_miss_cache",
            "_lyric_index_cache",
        ):
            self.assertFalse(getattr(self.store, name), name)
        reread = self.store.resolve_cached_or_local("Manual", "Artist")
        self.assertEqual(reread.timed_lyrics[0].text, "hand authored")

    def test_shared_directory_only_removes_files_written_by_gluspot(self):
        store = LocalLyricsStore(self.base / "shared")
        store._persist_match("Generated", "Artist", "", _match())
        generated = store.root / "artist - generated.lrc"
        imported = store.root / "artist - imported.lrc"
        imported.write_text("[by:netease]\n[00:01.00]user import\n", encoding="utf-8")
        manual = store.root / "manual.lrc"
        manual.write_text("[00:01.00]manual\n", encoding="utf-8")

        result = store.clear_generated_cache()

        self.assertEqual(result["deleted_files"], 1)
        self.assertFalse(result["errors"])
        self.assertFalse(generated.exists())
        self.assertTrue(imported.exists())
        self.assertTrue(manual.exists())

    def test_legacy_generated_file_is_removable_in_separate_cache_directory(self):
        legacy = self.store.generated_root / "artist - legacy.lrc"
        legacy.write_text("[by:qq]\n[00:01.00]old generated\n", encoding="utf-8")
        nested = self.store.generated_root / "nested"
        nested.mkdir()
        nested_file = nested / "nested.lrc"
        nested_file.write_text("[by:netease]\n[00:01.00]nested\n", encoding="utf-8")

        result = self.store.clear_generated_cache()

        self.assertEqual(result["deleted_files"], 1)
        self.assertFalse(legacy.exists())
        self.assertTrue(nested_file.exists())

    def test_old_lookup_and_fallback_cannot_repopulate_or_clear_new_pending(self):
        started, releases = self._controlled_lookup((_match("old result"), _match("new result")), True)
        self.assertTrue(self.store.resolve_in_background("Song", "Artist"))
        self.assertTrue(started[0].wait(2.0))
        self.store.clear_generated_cache()
        self.assertTrue(self.store.resolve_in_background("Song", "Artist"))
        self.assertTrue(started[1].wait(2.0))

        releases[0].set()
        self._join(0)

        self.assertTrue(self.store.is_lookup_pending("Song", "Artist"))
        self.assertFalse(self.store._resolved_cache)
        self.assertEqual(list(self.store.generated_root.glob("*.lrc")), [])
        releases[1].set()
        self._join(1)
        current = self.store.resolve_cached_or_local("Song", "Artist")
        self.assertEqual(current.timed_lyrics[0].text, "new result")
        self.assertFalse(self.store.is_lookup_pending("Song", "Artist"))
        self.assertIn("new result", (self.store.generated_root / "artist - song.lrc").read_text(encoding="utf-8"))

    def test_old_empty_lookup_cannot_install_permanent_miss_after_clear(self):
        started, releases = self._controlled_lookup((LyricsMatch((), ()),))
        self.assertTrue(self.store.resolve_in_background("Song", "Artist"))
        self.assertTrue(started[0].wait(2.0))
        self.store.clear_generated_cache()
        releases[0].set()
        self._join(0)

        self.assertFalse(self.store._missed_at)
        self.store._resolve_online = lambda *_args: _match("retried")
        self.assertTrue(self.store.resolve_in_background("Song", "Artist"))
        self._join(1)
        self.assertEqual(self.store.resolve_cached_or_local("Song", "Artist").timed_lyrics[0].text, "retried")

    def test_synchronous_lookup_started_before_clear_cannot_publish_after_clear(self):
        started, releases = self._controlled_lookup((_match("old synchronous result"),))
        returned = []
        worker = threading.Thread(target=lambda: returned.append(self.store.resolve("Song", "Artist")))
        worker.start()
        self.assertTrue(started[0].wait(2.0))
        self.store.clear_generated_cache()
        releases[0].set()
        self._join(0)

        self.assertFalse(returned[0].timed_lyrics)
        self.assertFalse(self.store._resolved_cache)
        self.assertEqual(list(self.store.generated_root.glob("*.lrc")), [])

    def test_worker_not_started_until_after_clear_does_not_start_network_lookup(self):
        key = self.store._cache_key("Song", "Artist", "", 0.0)
        generation = self.store._cache_generation
        self.store.clear_generated_cache()
        with patch.object(self.store, "_resolve_online", side_effect=AssertionError("stale network call")):
            self.store._resolve_background(key, "Song", "Artist", "", 0.0, generation)
            self.store._enrich_background(key, "Song", "Artist", "", 0.0, False, generation)

        self.assertFalse(self.store._resolved_cache)
        self.assertEqual(list(self.store.generated_root.glob("*.lrc")), [])

    def test_old_enrichment_cannot_restore_files_or_new_attempt_state(self):
        row = TimedLyricLine(1.0, "new words\n新译文", (
            TimedLyricUnit(1.0, 1.5, "new "), TimedLyricUnit(1.5, 2.0, "words"),
        ), "unit")
        new = LyricsMatch((row,), (row.text,), "qq")
        started, releases = self._controlled_lookup((_match("old result"), new))
        self.store.settings_getter = lambda key, default=None: True if key == "v2_enable_dual_line_lyrics" else default
        self.assertTrue(self.store.enrich_in_background("Song", "Artist"))
        self.assertTrue(started[0].wait(2.0))
        self.store.clear_generated_cache()
        self.assertTrue(self.store.enrich_in_background("Song", "Artist"))
        self.assertTrue(started[1].wait(2.0))
        key = self.store._cache_key("Song", "Artist", "", 0.0)
        attempt = self.store._enrich_attempted_at[key]

        releases[0].set()
        self._join(0)

        self.assertIn(key, self.store._enrich_pending_keys)
        self.assertEqual(self.store._enrich_attempted_at[key], attempt)
        self.assertEqual(self.store._enrich_retry_seconds[key], self.store.ENRICH_RETRY_SECONDS)
        self.assertFalse(self.store._resolved_cache)
        self.assertEqual(list(self.store.generated_root.glob("*.lrc")), [])
        releases[1].set()
        self._join(1)
        self.assertNotIn(key, self.store._enrich_pending_keys)
        self.assertEqual(self.store._resolved_cache[key].timed_lyrics[0].text, row.text)

    def test_clearing_waits_for_inflight_atomic_disk_write_then_removes_result(self):
        started = threading.Event()
        release = threading.Event()
        self.releases.append(release)
        original_render = self.store._render_lrc

        def delayed_render(*args):
            started.set()
            if not release.wait(3.0):
                raise AssertionError("test did not release disk write")
            return original_render(*args)

        self.store._render_lrc = delayed_render
        self.store._resolve_online = lambda *_args: _match()
        self.assertTrue(self.store.resolve_in_background("Song", "Artist"))
        self.assertTrue(started.wait(2.0))
        finished = threading.Event()
        results = []

        def clear():
            results.append(self.store.clear_generated_cache())
            finished.set()

        clearer = threading.Thread(target=clear)
        clearer.start()
        self.assertFalse(finished.wait(0.05))
        release.set()
        self._join(0)
        clearer.join(3.0)
        self.assertTrue(finished.is_set())
        self.assertEqual(results[0]["deleted_files"], 1)
        self.assertFalse(self.store._resolved_cache)
        self.assertEqual(list(self.store.generated_root.glob("*.lrc")), [])

    def test_redirected_cache_directory_is_rejected_without_deleting_other_files(self):
        self.store._persist_match("Song", "Artist", "", _match())
        original = self.store.generated_root / "artist - song.lrc"
        other = self.base / "other"
        other.mkdir()
        protected = other / "protected.lrc"
        protected.write_text("[by:netease]\n[00:01.00]external\n", encoding="utf-8")
        self.store.generated_root = other

        result = self.store.clear_generated_cache()

        self.assertEqual(result["deleted_files"], 0)
        self.assertTrue(result["errors"])
        self.assertTrue(original.exists())
        self.assertTrue(protected.exists())

    def test_file_symlink_is_not_followed_or_removed(self):
        target = self.base / "external.lrc"
        target.write_text("[by:netease]\n[00:01.00]external\n", encoding="utf-8")
        link = self.store.generated_root / "linked.lrc"
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"File symlinks unavailable: {exc}")

        result = self.store.clear_generated_cache()

        self.assertEqual(result["deleted_files"], 0)
        self.assertTrue(link.is_symlink())
        self.assertTrue(target.exists())

    def test_linked_root_is_rejected_without_following_it(self):
        target = self.base / "external-cache"
        target.mkdir()
        protected = target / "protected.lrc"
        protected.write_text("[by:netease]\n[00:01.00]external\n", encoding="utf-8")
        directory = self.store.generated_root
        directory.rmdir()
        try:
            directory.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Directory symlinks unavailable: {exc}")

        result = self.store.clear_generated_cache()

        self.assertEqual(result["deleted_files"], 0)
        self.assertTrue(result["errors"])
        self.assertTrue(protected.exists())

    def test_file_deletion_error_is_reported_without_dropping_protected_files(self):
        self.store._persist_match("Song", "Artist", "", _match())
        generated = self.store.generated_root / "artist - song.lrc"
        real_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == generated:
                raise PermissionError("file is in use")
            return real_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", unlink):
            result = self.store.clear_generated_cache()

        self.assertEqual(result["deleted_files"], 0)
        self.assertIn("file is in use", result["errors"][0])
        self.assertTrue(generated.exists())


if __name__ == "__main__":
    unittest.main()
