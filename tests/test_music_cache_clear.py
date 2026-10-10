from __future__ import annotations

import asyncio
from concurrent.futures import Future
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage

from api.artwork_sources import MusicBrainzArtworkResolver
from api.lyrics import LocalLyricsStore
from api.models import AlbumQueueEntry, TimedLyricLine
from api.providers import EmptyIslandDataProvider, WindowsMediaProvider, empty_visual_data
from tests.test_artwork_sources import ART_URL, RELEASE_ID, artwork_payload, recording_payload, ManualThread, ManualTimer
from tests.test_provider_state import FakeLyricsStore, FakeSession


class MusicCacheClearTests(unittest.TestCase):
    @staticmethod
    def png(side=600):
        image = QImage(side, side, QImage.Format.Format_RGB32)
        image.fill(0xFF203060)
        data, buffer = QByteArray(), QBuffer()
        buffer.setBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        return bytes(data)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.art_root = self.root / "art"
        self.art_root.mkdir()
        env = patch.dict(os.environ, {"GLUSPOT_ALBUM_ART_CACHE_DIR": str(self.art_root)})
        env.start()
        self.addCleanup(env.stop)
        self.provider = self.make_provider()
        self.cover = self.png()
        base = empty_visual_data()
        self.provider._last_data = replace(base, track=replace(
            base.track, title="Song", artist="Artist", track_id="track", position_seconds=43.5,
            position_sampled_at=90.0, position_source="windows-media", duration_seconds=240.0,
            is_playing=True, lyrics=("kept original",), timed_lyrics=(TimedLyricLine(1.0, "kept original"),),
            lyrics_source="online:netease", album_art_bytes=self.cover, album_art_source="original",
            album_queue=(AlbumQueueEntry("Next", "Singer", album_art_bytes=self.cover),),
        ))
        self.clock_fields = {
            "_last_ready_at": 90.0, "_local_seek_position_seconds": 44.0, "_local_seek_at": 91.0,
            "_local_seek_track_id": "track", "_last_raw_timeline_track_id": "track",
            "_last_raw_timeline_position": 43.5, "_last_raw_timeline_at": 90.0,
        }
        for key, value in self.clock_fields.items():
            setattr(self.provider, key, value)
        self.provider.audio_analyzer = object()
        self.audio_device = self.provider.audio_analyzer

    def make_provider(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.fallback = EmptyIslandDataProvider(empty_visual_data())
        provider.lyrics_store = FakeLyricsStore()
        provider._loop = provider._refresh_future = None
        provider._music_cache_epoch = provider._artwork_cache_epoch = provider._refresh_future_cache_epoch = 0
        provider._music_cache_lock = threading.RLock()
        provider._artwork_cache_root = self.art_root
        provider._art_cache = {}
        provider._pending_thumbnail_tasks = {}
        provider._ready_thumbnail_bytes = {}
        provider._queue_art_cache = {}
        provider._queue_art_inflight = set()
        provider._queue_art_failed_until = {}
        provider._queue_art_lock = threading.Lock()
        provider._queue_art_slots = threading.BoundedSemaphore(3)
        provider._current_art_lookup_lock = threading.Lock()
        provider._current_art_url_by_track_id = {}
        provider._current_art_lookup_generations = {}
        provider._current_art_lookup_inflight = set()
        provider._current_art_lookup_failed_until = {}
        provider._current_art_lookup_sources = {}
        provider._current_art_rejected_urls = {}
        provider._musicbrainz_artwork_resolver = MusicBrainzArtworkResolver()
        provider._last_refresh_at = 100.0
        provider._source_app_foreground_at = 0.0
        provider.netease_position = provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_music_player_mode": "netease", "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False, "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda *_args: None
        provider._is_source_app_foreground = lambda *_args: False
        return provider

    def assert_clock_kept(self):
        for key, value in self.clock_fields.items():
            self.assertEqual(getattr(self.provider, key), value, key)
        self.assertEqual(self.provider._last_data.track.position_seconds, 43.5)
        self.assertEqual(self.provider._last_data.track.track_id, "track")
        self.assertTrue(self.provider._last_data.track.is_playing)
        self.assertIs(self.provider.audio_analyzer, self.audio_device)

    def test_artwork_clear_only_owns_direct_hash_files_and_preserves_clock_and_lyrics(self):
        image_path = self.provider._queue_art_disk_path("https://example.test/cover.jpg")
        image_path.write_bytes(self.cover)
        temporary_path = self.provider._queue_art_disk_path("https://example.test/other.jpg").with_suffix(".tmp")
        temporary_path.write_bytes(b"interrupted")
        protected = [self.art_root / "user.img", self.art_root / "cover.jpg", self.art_root / "manual.lrc"]
        for path in protected:
            path.write_bytes(b"user data")
        nested = self.art_root / "nested"
        nested.mkdir()
        nested_path = nested / image_path.name
        nested_path.write_bytes(self.cover)
        provider = self.provider
        provider._queue_art_cache["url"] = self.cover
        provider._art_cache["thumbnail"] = self.cover
        provider._ready_thumbnail_bytes["track"] = self.cover
        provider._queue_art_failed_until["url"] = 1000
        provider._current_art_url_by_track_id["track"] = "url"
        provider._current_art_lookup_sources["track"] = "netease"
        provider._current_art_lookup_failed_until["track"] = 1000
        provider._current_art_lookup_generations["track"] = (object(), 1000)
        provider._netease_art_source_failed_until = 1000
        resolver = provider._musicbrainz_artwork_resolver
        resolver._cache["old"] = (1000, "url")
        resolver._failed_until = 1000
        lyrics_before = provider._last_data.track.timed_lyrics

        result = provider.clear_music_cache("artwork")
        self.assertEqual(result["deleted_files"], 2)
        self.assertEqual(result["deleted_bytes"], len(self.cover) + len(b"interrupted"))
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["kind"], "artwork")
        self.assertTrue(result["refresh_requested"])
        self.assertTrue(all(path.exists() for path in protected))
        self.assertTrue(nested_path.exists())
        self.assertFalse(image_path.exists())
        self.assertFalse(temporary_path.exists())
        self.assertEqual(provider._last_data.track.album_art_bytes, b"")
        self.assertEqual(provider._last_data.track.album_queue[0].album_art_bytes, b"")
        self.assertEqual(provider._last_data.track.timed_lyrics, lyrics_before)
        self.assertEqual(provider._last_data.track.album_art_lookup_state, "pending")
        self.assertEqual(provider._queue_art_cache, {})
        self.assertEqual(provider._current_art_url_by_track_id, {})
        self.assertEqual(provider._current_art_lookup_generations, {})
        self.assertEqual(provider._queue_art_failed_until, {})
        self.assertEqual(provider._netease_art_source_failed_until, 0)
        self.assertEqual(resolver._cache, {})
        self.assertEqual(resolver._failed_until, 0)
        self.assertEqual(provider._last_refresh_at, 0)
        self.assert_clock_kept()

    def test_lyrics_clear_preserves_manual_files_and_artwork_then_retries_current_lookup(self):
        local, generated = self.root / "lyrics", self.root / "generated"
        store = LocalLyricsStore(root=local, generated_root=generated)
        manual = local / "hand written.lrc"
        manual.write_text("[00:01.00]keep my file\n", encoding="utf-8")
        generated_file = generated / "artist - song.lrc"
        generated_file.write_text("[by:netease]\n[00:01.00]old generated words\n", encoding="utf-8")
        store.resolve_in_background = Mock(return_value=True)
        self.provider.lyrics_store = store
        self.provider._queue_art_cache["url"] = self.cover
        disk_cover = self.provider._queue_art_disk_path("url")
        disk_cover.write_bytes(self.cover)

        result = self.provider.clear_music_cache("lyrics")
        self.assertEqual(result["deleted_files"], 1)
        self.assertEqual(result["errors"], [])
        self.assertTrue(manual.exists())
        self.assertFalse(generated_file.exists())
        self.assertTrue(disk_cover.exists())
        self.assertEqual(self.provider._queue_art_cache["url"], self.cover)
        self.assertEqual(self.provider._last_data.track.album_art_bytes, self.cover)
        self.assertEqual(self.provider._last_data.track.timed_lyrics, ())
        self.assertEqual(self.provider._last_data.track.lyrics_source, "")
        self.provider._ensure_lyrics_lookup_started("Song", "Artist")
        store.resolve_in_background.assert_called_once()
        self.assert_clock_kept()

    def test_late_download_cannot_restore_disk_or_erase_the_new_same_url_job(self):
        provider, cover = self.provider, self.cover
        url = "https://example.test/cover.jpg"
        started, release = threading.Event(), threading.Event()
        real_thread = threading.Thread
        ManualThread.jobs = []

        class Response:
            def __init__(self, slow):
                self.slow = slow

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                if self.slow:
                    started.set()
                    if not release.wait(3):
                        raise TimeoutError("test download release missing")
                return cover

        with patch("api.providers.threading.Thread", ManualThread), patch(
            "api.providers.urlopen", side_effect=[Response(True), Response(False)]
        ):
            provider._schedule_queue_album_art_prefetch(url)
            old_worker = real_thread(target=ManualThread.jobs.pop(0))
            old_worker.start()
            try:
                self.assertTrue(started.wait(2))
                provider.clear_music_cache("artwork")
                self.assertTrue(provider._schedule_queue_album_art_prefetch(url))
                new_worker = ManualThread.jobs.pop(0)
            finally:
                release.set()
                old_worker.join(3)
            self.assertEqual(provider._queue_art_cache, {})
            self.assertFalse(provider._queue_art_disk_path(url).exists())
            self.assertEqual(provider._queue_art_inflight, {url})
            new_worker()
        self.assertEqual(provider._queue_art_cache[url], cover)
        self.assertTrue(provider._queue_art_disk_path(url).exists())
        self.assertEqual(provider._queue_art_inflight, set())

    def test_cleared_metadata_worker_and_timer_cannot_publish_or_clear_new_lookup(self):
        provider = self.provider
        provider._online_current_art_lookup_enabled = True
        provider._music_player_adapter = "netease"
        provider._resolve_netease_art_url = Mock(return_value=ART_URL)
        provider._schedule_queue_album_art_prefetch = Mock()
        ManualThread.jobs, ManualTimer.timers = [], []
        with patch("api.providers.threading.Thread", ManualThread), patch("api.providers.threading.Timer", ManualTimer):
            self.assertTrue(provider._schedule_current_netease_art_lookup("track", "Song", "Artist"))
            old_worker, old_timer = ManualThread.jobs.pop(0), ManualTimer.timers[0]
            provider.clear_music_cache("artwork")
            self.assertTrue(provider._schedule_current_netease_art_lookup("track", "Song", "Artist"))
            new_worker = ManualThread.jobs.pop(0)
            old_worker()
            old_timer.callback()
            provider._resolve_netease_art_url.assert_not_called()
            self.assertEqual(provider._current_art_lookup_inflight, {"track"})
            self.assertEqual(provider._current_art_lookup_failed_until, {})
            new_worker()
        self.assertEqual(provider._current_art_url_by_track_id["track"], ART_URL)
        provider._schedule_queue_album_art_prefetch.assert_called_once_with(ART_URL)

    def test_old_completed_refresh_cannot_restore_cleared_lyrics(self):
        future = Future()
        future.set_result(self.provider._last_data)
        self.provider._refresh_future = future
        self.provider.lyrics_store.clear_generated_cache = Mock(return_value={"deleted_files": 0, "deleted_bytes": 0, "errors": []})
        self.provider.clear_music_cache("lyrics")
        self.assertIsNone(self.provider._refresh_future)
        # Simulate a cancelled delivery already captured by another callback.
        self.provider._refresh_future = future
        self.provider._consume_refresh_future(100)
        self.assertEqual(self.provider._last_data.track.timed_lyrics, ())
        self.assert_clock_kept()

    def test_clear_during_media_read_discards_the_old_generation(self):
        provider = self.provider
        provider.lyrics_store.clear_generated_cache = Mock(return_value={"deleted_files": 0, "deleted_bytes": 0, "errors": []})

        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()

            async def session():
                entered.set()
                await release.wait()
                return FakeSession("cloudmusic.exe", "Song", "Artist", True, 43.5, 240)

            provider._get_session = session
            read = asyncio.create_task(provider._read_visual_data())
            await entered.wait()
            provider.clear_music_cache("lyrics")
            release.set()
            self.assertIsNone(await read)

        asyncio.run(scenario())
        self.assertEqual(provider._last_data.track.timed_lyrics, ())
        self.assert_clock_kept()

    def test_slow_thumbnail_bytes_cannot_repopulate_after_clear(self):
        provider, cover = self.provider, self.cover

        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()

            class Stream:
                size = len(cover)

                async def read_async(self, _buffer, _size, _options):
                    entered.set()
                    await release.wait()
                    return SimpleNamespace(length=len(cover))

            class Thumbnail:
                async def open_read_async(self):
                    return Stream()

            reader = SimpleNamespace(read_bytes=lambda _size: cover)
            with patch("api.providers.Buffer", lambda _size: object()), patch(
                "api.providers.DataReader", SimpleNamespace(from_buffer=lambda _filled: reader)
            ):
                task = asyncio.create_task(provider._read_thumbnail(SimpleNamespace(thumbnail=Thumbnail()), "track"))
                await entered.wait()
                provider.clear_music_cache("artwork")
                release.set()
                self.assertEqual(await task, b"")

        asyncio.run(scenario())
        self.assertEqual(provider._art_cache, {})
        self.assertEqual(provider._ready_thumbnail_bytes, {})

    def test_uncooperative_thumbnail_callback_cannot_restore_cleared_ready_data(self):
        provider, cover = self.provider, self.cover

        async def scenario():
            release = asyncio.Event()

            async def thumbnail(*_args):
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    return cover
                return cover

            provider._read_thumbnail = thumbnail
            self.assertEqual(await provider._thumbnail_for_track(SimpleNamespace(), "track"), b"")
            task = provider._pending_thumbnail_tasks["track"]
            provider.clear_music_cache("artwork")
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)

        asyncio.run(scenario())
        self.assertEqual(provider._pending_thumbnail_tasks, {})
        self.assertEqual(provider._ready_thumbnail_bytes, {})

    def test_redirected_cache_root_does_not_delete_files_from_new_location(self):
        other = self.root / "other"
        other.mkdir()
        protected = other / ("a" * 64 + ".img")
        protected.write_bytes(b"not owned at initialization")
        with patch.dict(os.environ, {"GLUSPOT_ALBUM_ART_CACHE_DIR": str(other)}):
            result = self.provider.clear_music_cache("artwork")
        self.assertEqual(result["deleted_files"], 0)
        self.assertTrue(result["errors"])
        self.assertTrue(protected.exists())

    def test_delete_error_is_reported_and_does_not_change_media_clock(self):
        path = self.provider._queue_art_disk_path("url")
        path.write_bytes(self.cover)
        with patch.object(Path, "unlink", side_effect=PermissionError("file in use")):
            result = self.provider.clear_music_cache("artwork")
        self.assertEqual(result["deleted_files"], 0)
        self.assertIn("file in use", result["errors"][0])
        self.assertTrue(path.exists())
        self.assert_clock_kept()

    def test_two_providers_use_independent_atomic_temporary_files(self):
        first, second = self.provider, self.make_provider()
        url = "https://example.test/shared.jpg"
        first_cover, second_cover = self.cover, self.png(800)
        barrier = threading.Barrier(2)
        original_replace = os.replace
        temporary_paths = []

        def overlap(source, destination):
            temporary_paths.append(Path(source))
            barrier.wait(timeout=3)
            original_replace(source, destination)

        with patch("api.providers.os.replace", overlap):
            workers = [threading.Thread(target=provider._persist_queue_art, args=(url, data))
                       for provider, data in ((first, first_cover), (second, second_cover))]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(4)
        self.assertEqual(len(temporary_paths), 2)
        self.assertNotEqual(temporary_paths[0], temporary_paths[1])
        self.assertEqual(first._queue_art_disk_path(url).read_bytes() in (first_cover, second_cover), True)
        self.assertEqual(list(self.art_root.glob("*.tmp")), [])

    def test_clear_recognizes_unique_atomic_files_and_preserves_unrelated_tmp(self):
        digest = "a" * 64
        owned = self.art_root / f"{digest}.abc_1234.tmp"
        unrelated = self.art_root / f"{digest}.user-work.tmp"
        owned.write_bytes(b"owned partial write")
        unrelated.write_bytes(b"user file")
        result = self.provider.clear_music_cache("artwork")
        self.assertEqual(result["deleted_files"], 1)
        self.assertFalse(owned.exists())
        self.assertTrue(unrelated.exists())

    def test_invalid_kind_does_not_reset_any_cache(self):
        before = self.provider._last_data
        with self.assertRaises(ValueError):
            self.provider.clear_music_cache("player")
        self.assertIs(self.provider._last_data, before)
        self.assertEqual(self.provider._music_cache_epoch, 0)

    def test_cache_diagnostics_report_epochs_and_display_evidence_without_disk_paths(self):
        before = self.provider._music_cache_diagnostics()
        self.assertEqual(before["display_artwork_min_side"], 600)
        self.assertEqual(before["display_artwork_source"], "original")
        self.provider.clear_music_cache("artwork")
        after = self.provider._music_cache_diagnostics()
        self.assertEqual(after["epoch"], 1)
        self.assertEqual(after["artwork_epoch"], 1)
        self.assertEqual(after["display_artwork_min_side"], 0)
        self.assertEqual(after["display_artwork_lookup_state"], "pending")
        self.assertEqual(after["last_clear"]["kind"], "artwork")
        self.assertNotIn(str(self.art_root), str(after))

    def test_gui_media_read_returns_immediately_during_slow_worker_disk_deletion(self):
        provider = self.provider
        provider._loop = object()
        path = provider._queue_art_disk_path("url")
        path.write_bytes(self.cover)
        entered, release = threading.Event(), threading.Event()
        original_unlink = Path.unlink
        reports = []

        def slow_unlink(candidate, *args, **kwargs):
            if candidate == path:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("slow deletion was not released")
            return original_unlink(candidate, *args, **kwargs)

        with patch.object(Path, "unlink", slow_unlink), patch("api.providers.time.monotonic", return_value=100.0), patch(
            "api.providers.asyncio.run_coroutine_threadsafe", side_effect=AssertionError("GUI started a refresh during cleanup")
        ), patch.object(provider, "_foreground_correct_cached_data", side_effect=AssertionError("GUI probed foreground during cleanup")):
            worker = threading.Thread(target=lambda: reports.append(provider.clear_music_cache("artwork")))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                started = time.perf_counter()
                visual = provider.get_visual_data()
                elapsed = time.perf_counter() - started
                self.assertLess(elapsed, 0.15)
                self.assertEqual(visual.track.track_id, "track")
                self.assertAlmostEqual(visual.track.position_seconds, 53.5)
                self.assertEqual(provider._last_data.track.position_seconds, 43.5)
                self.assertIsNone(provider._refresh_future)
                self.assert_clock_kept()
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(reports[0]["deleted_files"], 1)
        self.assertEqual(reports[0]["errors"], [])
        self.assert_clock_kept()


class ArtworkResolverClearTests(unittest.TestCase):
    def test_late_resolver_does_not_restore_cleared_results_or_overwrite_new_request(self):
        resolver = MusicBrainzArtworkResolver(request_gate=SimpleNamespace(wait=lambda _deadline: True))
        entered, release = threading.Event(), threading.Event()
        calls = []

        def fetch(url, _deadline):
            calls.append(url)
            if len(calls) == 1:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("test resolver release missing")
            return recording_payload() if "musicbrainz.org/ws/" in url else artwork_payload()

        resolver._fetch_json = fetch
        expected_url = f"https://coverartarchive.org/release/{RELEASE_ID}/front-1200"
        results = []
        old_worker = threading.Thread(target=lambda: results.append(resolver.resolve("Song", "Artist", "Album", 240)))
        old_worker.start()
        try:
            self.assertTrue(entered.wait(2))
            resolver.clear_cache()
            self.assertEqual(resolver.resolve("Song", "Artist", "Album", 240), expected_url)
        finally:
            release.set()
            old_worker.join(3)
        self.assertEqual(results, [""])
        self.assertEqual(len(resolver._cache), 1)
        self.assertEqual(next(iter(resolver._cache.values()))[1], expected_url)
        self.assertEqual(resolver._pending, set())


if __name__ == "__main__":
    unittest.main()
