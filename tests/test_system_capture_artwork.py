"""System artwork upgrades keep SMTC timing and survive preview/metadata churn."""
import asyncio
import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from api.providers import WindowsMediaProvider
from tests import test_artwork_sources as artwork_tests
from tests.test_artwork_sources import Clock, JsonResponse, ManualThread, ManualTimer
from tests.test_provider_state import FakeSession


class SystemCaptureArtworkTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.preview = artwork_tests.ArtworkVisualPriorityTests.png(108, 0xFF704020)
        self.original = artwork_tests.ArtworkVisualPriorityTests.png(800, 0xFF204070)
        self.provider = artwork_tests.ArtworkVisualPriorityTests().provider(self.preview)
        del self.provider._schedule_current_netease_art_lookup
        self.provider._online_current_art_lookup_enabled = True
        self.provider._queue_art_cache = {}
        self.provider._queue_art_disk_path = lambda _url: None
        self.provider._persist_queue_art = Mock()
        self.provider.netease_position = SimpleNamespace(track_metadata=Mock(return_value=None))
        self.provider._musicbrainz_artwork_resolver = SimpleNamespace(resolve=Mock(return_value=""))
        ManualThread.jobs, ManualTimer.timers = [], []
        for target, value in (("time.monotonic", self.clock), ("threading.Thread", ManualThread),
                              ("threading.Timer", ManualTimer)):
            guard = patch("api.providers." + target, value)
            guard.start()
            self.addCleanup(guard.stop)

    def read(self):
        data = asyncio.run(self.provider._read_visual_data())
        self.provider._last_data = data
        return data.track

    def queue(self, url, **changes):
        values = dict(title="Song", artist="Artist", album_title="Album", duration_seconds=240,
                      album_art_url=url)
        values.update(changes)
        self.provider.netease_position.track_metadata.return_value = SimpleNamespace(**values)

    def test_smtc_108_cloudsearch_picurl_download_800_becomes_original(self):
        first = self.read()
        self.assertEqual(first.album_art_source, "thumbnail")
        self.assertEqual(first.album_art_lookup_state, "pending")
        self.assertEqual(ManualThread.jobs, [])
        self.clock.now += 1.05
        self.read()
        payload = {"code": 200, "result": {"songs": [{"name": "Song", "dt": 240000,
            "ar": [{"name": "Artist"}], "al": {"name": "Album", "picUrl": "https://p2.music.126.net/object/123.jpg"}}]}}
        with patch("api.providers.urlopen", return_value=JsonResponse(payload)) as search:
            ManualThread.jobs.pop(0)()
        self.assertEqual(search.call_args.args[0].full_url, "https://music.163.com/api/cloudsearch/pc")
        self.assertEqual(self.read().album_art_bytes, self.preview)
        with patch("api.providers.urlopen", return_value=io.BytesIO(self.original)) as download:
            ManualThread.jobs.pop(0)()
        self.assertTrue(download.call_args.args[0].full_url.endswith("?param=2560y2560"))
        settled = self.read()
        self.assertEqual(settled.album_art_bytes, self.original)
        self.assertEqual(settled.album_art_source, "netease-original")
        self.assertEqual(settled.album_art_lookup_state, "ready")
        self.assertEqual(settled.position_source, "windows-media")
        self.assertEqual(self.provider._music_player_adapter, "system")
        self.assertEqual(self.provider._current_art_lookup_inflight, set())
        self.assertEqual(self.provider._queue_art_inflight, set())
        self.provider._musicbrainz_artwork_resolver.resolve.assert_not_called()

    def test_queue_url_is_first_and_only_artwork_authority_not_metadata_or_progress(self):
        url = "https://p1.music.126.net/object/456.jpg?imageView=&thumbnail=32y32&type=webp"
        self.queue(url, album_title="Queue album")
        self.provider._resolve_netease_art_url = Mock(side_effect=AssertionError("unneeded search"))
        first = self.read()
        self.assertEqual(first.album_art_bytes, self.preview)
        self.assertEqual(first.duration_seconds, 240)
        self.assertEqual(first.position_seconds, 12)
        with patch("api.providers.urlopen", return_value=io.BytesIO(self.original)) as download:
            ManualThread.jobs.pop(0)()
        self.assertTrue(download.call_args.args[0].full_url.endswith("?param=2560y2560"))
        self.assertEqual(self.read().album_art_source, "netease-original")
        self.provider._resolve_netease_art_url.assert_not_called()
        self.assertEqual(self.provider._music_player_adapter, "system")

    def test_wrong_queue_title_artist_or_known_duration_cannot_supply_cover(self):
        for changes in ({"title": "Other"}, {"artist": "Other"}, {"duration_seconds": 280}):
            with self.subTest(changes=changes):
                self.queue("https://p1.music.126.net/wrong.jpg", **changes)
                self.assertEqual(self.read().album_art_bytes, self.preview)
                self.assertEqual(ManualThread.jobs, [])

    def test_album_duration_churn_reuses_url_and_inflight_lookup_identity(self):
        self.read()
        self.clock.now += 1.1
        self.provider._resolve_netease_art_url = Mock(return_value="https://p1.music.126.net/object/789.jpg")
        self.read()
        self.assertEqual(len(ManualThread.jobs), 1)
        session = FakeSession("cloudmusic.exe", "Song", "Artist", True, 15, 245)
        async def enriched_properties():
            return SimpleNamespace(title="Song", artist="Artist", album_title="Enriched Album")
        session.try_get_media_properties_async = enriched_properties
        self.provider._get_session = lambda: asyncio.sleep(0, result=session)
        changed = self.read()
        self.assertEqual(len(ManualThread.jobs), 1)
        ManualThread.jobs.pop(0)()
        with patch("api.providers.urlopen", return_value=io.BytesIO(self.original)):
            ManualThread.jobs.pop(0)()
        settled = self.read()
        self.assertEqual(settled.track_id, changed.track_id)
        self.assertEqual(settled.album_art_source, "netease-original")
        self.assertEqual(settled.album_art_bytes, self.original)
        self.provider._resolve_netease_art_url.assert_called_once()

    def test_persistent_failed_queue_url_cannot_cancel_or_overwrite_fallback(self):
        queue_url = "https://p1.music.126.net/object/broken.jpg?param=2560y2560"
        self.queue(queue_url)
        self.read()
        # Model an exhausted URL backoff, then start metadata fallback.
        ManualThread.jobs.clear()
        self.provider._queue_art_inflight.clear()
        self.provider._queue_art_failed_until[queue_url] = self.clock.now + 5
        self.provider._queue_art_attempts = {queue_url: 3}
        fallback = "https://coverartarchive.org/release/matched/front-1200"
        self.provider._resolve_netease_art_url = Mock(return_value=queue_url)
        self.provider._musicbrainz_artwork_resolver.resolve.return_value = fallback
        self.read()
        self.assertEqual(len(ManualThread.jobs), 1)
        self.read()  # Queue U remains present while fallback is inflight.
        ManualThread.jobs.pop(0)()
        self.assertEqual(self.provider._current_art_url_by_track_id["song|artist"], fallback)
        self.read()  # Queue U must not overwrite the published fallback.
        with patch("api.providers.urlopen", return_value=io.BytesIO(self.original)):
            ManualThread.jobs.pop(0)()
        self.assertEqual(self.read().album_art_source, "cover-art-archive-original")

    def test_download_failure_clears_pending_and_retries_with_backoff(self):
        url = "https://p1.music.126.net/object/retry.jpg?param=2560y2560"
        for delay in (0.75, 1.5, 5.0, 5.0):
            self.assertTrue(self.provider._schedule_queue_album_art_prefetch(url))
            with patch("api.providers.urlopen", side_effect=TimeoutError("secret URL/token text")):
                ManualThread.jobs.pop(0)()
            self.assertEqual(self.provider._queue_art_inflight, set())
            retry_at = self.provider._queue_art_failed_until[url]
            self.assertAlmostEqual(retry_at, self.clock.now + delay)
            self.assertFalse(self.provider._schedule_queue_album_art_prefetch(url))
            self.clock.now = retry_at + 0.01
        self.assertTrue(self.provider._schedule_queue_album_art_prefetch(url))
        with patch("api.providers.urlopen", return_value=io.BytesIO(self.original)):
            ManualThread.jobs.pop(0)()
        self.assertNotIn(url, self.provider._queue_art_failed_until)
        self.assertNotIn(url, self.provider._queue_art_attempts)

    def test_lookup_failure_backoff_is_per_track_and_clears_pending(self):
        self.provider._music_player_adapter = "netease"
        self.provider._resolve_netease_art_url = Mock(side_effect=TimeoutError())
        for delay in (0.75, 1.5, 5.0):
            self.assertTrue(self.provider._schedule_current_netease_art_lookup("a", "Song", "Artist"))
            ManualThread.jobs.pop(0)()
            self.assertEqual(self.provider._current_art_lookup_inflight, set())
            self.assertEqual(self.provider._current_art_lookup_generations, {})
            self.assertAlmostEqual(self.provider._current_art_lookup_failed_until["a"], self.clock.now + delay)
            self.assertFalse(self.provider._schedule_current_netease_art_lookup("a", "Song", "Artist"))
            self.assertTrue(self.provider._schedule_current_netease_art_lookup("b", "Other", "Artist"))
            self.provider._resolve_netease_art_url.side_effect = None
            self.provider._resolve_netease_art_url.return_value = "https://example.test/other.jpg"
            ManualThread.jobs.pop(0)()
            ManualThread.jobs.clear()  # Prefetch belongs to the other track.
            self.provider._resolve_netease_art_url.side_effect = TimeoutError()
            self.clock.now += delay + 0.01
            self.provider._current_art_url_by_track_id.pop("b")

    def test_repeated_tiny_downloads_keep_backoff_and_eventually_allow_fallback(self):
        url = "https://p1.music.126.net/object/tiny.jpg?param=2560y2560"
        self.queue(url)
        self.provider._resolve_netease_art_url = Mock(return_value=url)
        fallback = "https://coverartarchive.org/release/matched/front-1200"
        self.provider._musicbrainz_artwork_resolver.resolve.return_value = fallback
        self.read()
        for count, delay in enumerate((0.75, 1.5, 5.0), 1):
            with patch("api.providers.urlopen", return_value=io.BytesIO(self.preview)):
                ManualThread.jobs.pop(0)()
            current = self.read()
            self.assertEqual(current.album_art_bytes, self.preview)
            self.assertEqual(self.provider._queue_art_attempts[url], count)
            self.assertAlmostEqual(self.provider._queue_art_failed_until[url], self.clock.now + delay)
            if count < 3:
                self.assertEqual(ManualThread.jobs, [])
                self.clock.now += delay + 0.01
                self.read()
        self.assertEqual(len(ManualThread.jobs), 1)
        ManualThread.jobs.pop(0)()  # Metadata worker advances to CAA.
        with patch("api.providers.urlopen", return_value=io.BytesIO(self.original)):
            ManualThread.jobs.pop(0)()
        self.assertEqual(self.read().album_art_source, "cover-art-archive-original")

    def test_worker_and_timer_start_failure_release_singleflight_markers(self):
        self.provider._music_player_adapter = "netease"
        for target in ("threading.Thread.start", "threading.Timer.start"):
            with self.subTest(target=target), patch("api.providers." + target, side_effect=RuntimeError()):
                self.assertFalse(self.provider._schedule_current_netease_art_lookup("a", "Song", "Artist"))
                self.assertEqual(self.provider._current_art_lookup_inflight, set())
                self.assertEqual(self.provider._current_art_lookup_generations, {})
            self.clock.now += 10
        with patch("api.providers.threading.Thread.start", side_effect=RuntimeError()):
            self.assertFalse(self.provider._schedule_queue_album_art_prefetch("https://example.test/cover.jpg"))
            self.assertEqual(self.provider._queue_art_inflight, set())
        self.assertTrue(self.provider._current_art_lookup_slots.acquire(blocking=False))
        self.assertTrue(self.provider._current_art_lookup_slots.acquire(blocking=False))

    def test_incomplete_but_decodable_download_is_rejected_and_logs_hide_secrets(self):
        url = "https://example.test/cover.jpg?token=DO_NOT_LOG"
        response = io.BytesIO(self.original)
        response.headers = {"Content-Length": str(len(self.original) + 10)}
        with self.assertLogs("GluSpot.artwork", level="INFO") as logs, patch("api.providers.urlopen", return_value=response):
            self.assertTrue(self.provider._schedule_queue_album_art_prefetch(url))
            ManualThread.jobs.pop(0)()
        self.assertNotIn(url, self.provider._queue_art_cache)
        self.assertEqual(self.provider._queue_art_inflight, set())
        output = "\n".join(logs.output)
        self.assertIn("stage=download", output)
        self.assertIn("err=IncompleteRead", output)
        self.assertNotIn("DO_NOT_LOG", output)
        self.assertNotIn("https://", output)

    def test_expired_download_retries_and_old_completion_cannot_erase_new_same_url_job(self):
        url = "https://fixture.invalid/cover.png"
        self.assertTrue(self.provider._schedule_queue_album_art_prefetch(url))
        old = ManualThread.jobs.pop(0)
        ManualTimer.timers[-1].callback()
        self.assertEqual(self.provider._queue_art_inflight, set())
        self.clock.now = self.provider._queue_art_failed_until[url] + .01
        self.assertTrue(self.provider._schedule_queue_album_art_prefetch(url))
        new = ManualThread.jobs.pop(0)
        with patch("api.providers.urlopen", return_value=io.BytesIO(self.original)):
            old()
        self.assertEqual(self.provider._queue_art_inflight, {url})
        self.assertNotIn(url, self.provider._queue_art_cache)
        with patch("api.providers.urlopen", return_value=io.BytesIO(self.original)):
            new()
        self.assertEqual(self.provider._queue_art_inflight, set())
        self.assertEqual(self.provider._queue_art_cache[url], self.original)

    def test_two_expired_metadata_jobs_release_logical_capacity_for_current_track(self):
        for key in ("old1", "old2"):
            self.assertTrue(self.provider._schedule_current_netease_art_lookup(key, key, "Artist"))
        old = list(ManualThread.jobs)
        for timer in list(ManualTimer.timers):
            timer.callback()
        self.assertEqual(self.provider._current_art_lookup_inflight, set())
        self.assertTrue(self.provider._schedule_current_netease_art_lookup("new", "New", "Artist"))
        self.provider._resolve_netease_art_url = Mock(return_value="https://fixture.invalid/current.png")
        for action in old:
            action()
        self.assertNotIn("old1", self.provider._current_art_url_by_track_id)
        self.assertNotIn("old2", self.provider._current_art_url_by_track_id)
        self.assertEqual(self.provider._current_art_lookup_inflight, {"new"})

    def test_expired_downloads_use_bounded_reserve_and_do_not_queue_unlimited_threads(self):
        for batch in range(2):
            for index in range(3):
                self.assertTrue(self.provider._schedule_queue_album_art_prefetch(
                    f"https://fixture.invalid/{batch}-{index}.png"))
            for timer in list(ManualTimer.timers):
                if not timer.cancelled:
                    timer.callback()
        jobs = len(ManualThread.jobs)
        self.assertFalse(self.provider._schedule_queue_album_art_prefetch("https://fixture.invalid/overflow.png"))
        self.assertEqual(len(ManualThread.jobs), jobs)
        with patch("api.providers.urlopen", return_value=io.BytesIO(self.original)):
            ManualThread.jobs.pop(0)()
        self.assertTrue(self.provider._schedule_queue_album_art_prefetch("https://fixture.invalid/current.png"))

    def test_late_download_without_timer_delivery_still_cannot_publish(self):
        url = "https://fixture.invalid/late.png"
        self.assertTrue(self.provider._schedule_queue_album_art_prefetch(url))
        self.clock.now += 8.01
        with patch("api.providers.urlopen", return_value=io.BytesIO(self.original)):
            ManualThread.jobs.pop(0)()
        self.assertNotIn(url, self.provider._queue_art_cache)
        self.assertEqual(self.provider._queue_art_inflight, set())


if __name__ == "__main__":
    unittest.main()
