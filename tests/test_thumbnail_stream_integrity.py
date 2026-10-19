from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage

from api.providers import WindowsMediaProvider


class Stream:
    def __init__(self, size, chunks=(), *, reported_lengths=(), before_read=None):
        self.size = size
        self.chunks = list(chunks)
        self.reported_lengths = list(reported_lengths)
        self.before_read = before_read
        self.requests = []
        self.seeks = []
        self.closed = False

    def seek(self, position):
        self.seeks.append(position)

    async def read_async(self, buffer, requested, options):
        self.requests.append(requested)
        if self.before_read is not None:
            await self.before_read(len(self.requests))
        data = self.chunks.pop(0) if self.chunks else b""
        length = self.reported_lengths.pop(0) if self.reported_lengths else len(data)
        return SimpleNamespace(data=data, length=length)

    def close(self):
        self.closed = True


class Thumbnail:
    content_type = "image/png"

    def __init__(self, *streams):
        self.streams = list(streams)
        self.opens = 0

    async def open_read_async(self):
        self.opens += 1
        return self.streams.pop(0)


class Reader:
    def __init__(self, filled):
        self.data = filled.data
        self.closed = False

    def read_bytes(self, length):
        return self.data

    def close(self):
        self.closed = True


class ThumbnailStreamIntegrityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        image = QImage(2, 2, QImage.Format.Format_RGB32)
        image.fill(0xFF203060)
        data, buffer = QByteArray(), QBuffer()
        buffer.setBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        self.png = bytes(data)
        # A complete PNG is itself a decodable prefix of a valid stream with
        # trailing bytes. Decode success cannot establish stream completeness.
        self.complete = self.png + b"complete-stream-tail"
        self.provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        self.provider._artwork_cache_epoch = 0
        self.provider._art_cache = {}
        self.provider._pending_thumbnail_tasks = {}
        self.provider._ready_thumbnail_bytes = {}
        self.provider._last_refresh_at = 100.0
        self.readers = []

        def reader_for(filled):
            reader = Reader(filled)
            self.readers.append(reader)
            return reader

        for target, value in (
            ("api.providers.Buffer", lambda size: SimpleNamespace(capacity=size)),
            ("api.providers.DataReader", SimpleNamespace(from_buffer=reader_for)),
        ):
            replacement = patch(target, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def properties(self, *streams):
        return SimpleNamespace(thumbnail=Thumbnail(*streams))

    def assert_not_published(self):
        self.assertEqual(self.provider._art_cache, {})
        self.assertEqual(self.provider._ready_thumbnail_bytes, {})

    async def test_decodable_short_read_waits_for_complete_stream_before_caching(self):
        self.assertTrue(self.provider._is_valid_image_bytes(self.png))
        stream = Stream(len(self.complete), [self.png, self.complete[len(self.png):]])
        result = await self.provider._read_thumbnail(self.properties(stream), "track")
        self.assertEqual(result, self.complete)
        self.assertEqual(list(self.provider._art_cache.values()), [self.complete])
        self.assertEqual(stream.requests, [len(self.complete), len(self.complete) - len(self.png)])
        self.assertEqual(stream.seeks, [0])
        self.assertTrue(stream.closed)
        self.assertEqual(len(self.readers), 2)
        self.assertTrue(all(reader.closed for reader in self.readers))

    async def test_early_eof_never_caches_prefix_and_next_refresh_can_retry_same_track(self):
        incomplete = Stream(len(self.complete), [self.png])
        recovered = Stream(len(self.complete), [self.complete])
        properties = self.properties(incomplete, recovered)
        self.assertEqual(await self.provider._thumbnail_for_track(properties, "track"), b"")
        self.assert_not_published()
        self.assertEqual(self.provider._pending_thumbnail_tasks, {})
        self.assertEqual(properties.thumbnail.opens, 1)
        self.assertEqual(await self.provider._thumbnail_for_track(properties, "track"), self.complete)
        self.assertEqual(properties.thumbnail.opens, 2)
        self.assertEqual(len(incomplete.requests), 2)
        self.assertEqual(len(recovered.requests), 1)
        self.assertTrue(incomplete.closed and recovered.closed)

    async def test_existing_incomplete_or_invalid_thumbnail_cache_is_replaced(self):
        for cached in (self.png, b"x" * len(self.complete)):
            with self.subTest(cached_length=len(cached)):
                stream = Stream(len(self.complete), [self.complete])
                properties = self.properties(stream)
                key = self.provider._thumbnail_cache_key("track", properties.thumbnail, stream.size)
                self.provider._art_cache = {key: cached}
                self.assertEqual(await self.provider._read_thumbnail(properties, "track"), self.complete)
                self.assertEqual(self.provider._art_cache, {key: self.complete})
                self.assertEqual(len(stream.requests), 1)
                self.assertTrue(stream.closed)

    async def test_complete_valid_cache_hit_closes_stream_without_reading(self):
        stream = Stream(len(self.complete))
        properties = self.properties(stream)
        key = self.provider._thumbnail_cache_key("track", properties.thumbnail, stream.size)
        self.provider._art_cache[key] = self.complete
        self.assertEqual(await self.provider._read_thumbnail(properties, "track"), self.complete)
        self.assertEqual(stream.requests, [])
        self.assertEqual(self.readers, [])
        self.assertTrue(stream.closed)

    async def test_zero_progress_and_overreported_length_stop_without_publication(self):
        for reported in (0, len(self.complete) + 1):
            with self.subTest(reported=reported):
                stream = Stream(len(self.complete), [self.png], reported_lengths=[reported])
                self.assertEqual(await self.provider._read_thumbnail(self.properties(stream), "track"), b"")
                self.assertEqual(len(stream.requests), 1)
                self.assert_not_published()
                self.assertTrue(stream.closed)

    async def test_reader_length_mismatch_cannot_publish_and_closes_both_resources(self):
        stream = Stream(len(self.complete), [self.png], reported_lengths=[len(self.complete)])
        self.assertEqual(await self.provider._read_thumbnail(self.properties(stream), "track"), b"")
        self.assert_not_published()
        self.assertEqual(len(stream.requests), 1)
        self.assertTrue(stream.closed)
        self.assertTrue(self.readers[0].closed)

    async def test_invalid_complete_image_is_not_cached(self):
        stream = Stream(6, [b"broken"])
        self.assertEqual(await self.provider._read_thumbnail(self.properties(stream), "track"), b"")
        self.assert_not_published()
        self.assertTrue(stream.closed)
        self.assertTrue(self.readers[0].closed)

    async def test_size_bound_rejects_before_allocating_or_reading_and_closes_stream(self):
        for size in (0, -1, 12_000_001):
            with self.subTest(size=size):
                stream = Stream(size)
                self.assertEqual(await self.provider._read_thumbnail(self.properties(stream), "track"), b"")
                self.assertEqual(stream.requests, [])
                self.assertTrue(stream.closed)
                self.assert_not_published()

    async def test_large_complete_stream_uses_bounded_chunks(self):
        payload = self.png + b"x" * 140_000
        stream = Stream(len(payload), [payload[index:index + 65_536] for index in range(0, len(payload), 65_536)])
        self.assertEqual(await self.provider._read_thumbnail(self.properties(stream), "track"), payload)
        self.assertEqual(stream.requests, [65_536, 65_536, len(payload) - 131_072])
        self.assertTrue(all(reader.closed for reader in self.readers))
        self.assertTrue(stream.closed)

    async def test_total_read_deadline_cancels_later_chunk_without_pinning_attempt(self):
        blocker = asyncio.Event()

        async def before_read(number):
            if number == 2:
                await blocker.wait()

        stream = Stream(len(self.complete), [self.png], before_read=before_read)
        self.provider.THUMBNAIL_READ_TIMEOUT_SECONDS = 0.02
        self.assertEqual(await self.provider._thumbnail_for_track(self.properties(stream), "track"), b"")
        # The first-frame deadline may fire while the shorter total deadline
        # is still unwinding cancellation, especially at Windows timer
        # resolution. Wait for that actual attempt and its registered cleanup
        # callback, rather than assuming a scheduling turn has completed it.
        attempt = self.provider._pending_thumbnail_tasks.get("track")
        if attempt is not None:
            completed, _pending = await asyncio.wait((attempt,), timeout=1.0)
            self.assertIn(attempt, completed)
            with self.assertRaises(asyncio.TimeoutError):
                await attempt
        self.assertEqual(self.provider._pending_thumbnail_tasks, {})
        self.assert_not_published()
        self.assertEqual(len(stream.requests), 2)
        self.assertTrue(stream.closed)

    async def test_clear_during_later_chunk_discards_complete_result_and_closes_stream(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def before_read(number):
            if number == 2:
                entered.set()
                await release.wait()

        stream = Stream(len(self.complete), [self.png, self.complete[len(self.png):]], before_read=before_read)
        task = asyncio.create_task(self.provider._read_thumbnail(self.properties(stream), "track"))
        await asyncio.wait_for(entered.wait(), 1.0)
        self.assert_not_published()
        self.provider._artwork_cache_epoch += 1
        self.provider._art_cache.clear()
        release.set()
        self.assertEqual(await task, b"")
        self.assert_not_published()
        self.assertTrue(stream.closed)
        self.assertTrue(all(reader.closed for reader in self.readers))

    async def test_cancellation_after_prefix_closes_stream_and_never_publishes(self):
        entered, blocker = asyncio.Event(), asyncio.Event()

        async def before_read(number):
            if number == 2:
                entered.set()
                await blocker.wait()

        stream = Stream(len(self.complete), [self.png], before_read=before_read)
        task = asyncio.create_task(self.provider._read_thumbnail(self.properties(stream), "track"))
        await asyncio.wait_for(entered.wait(), 1.0)
        self.assert_not_published()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_not_published()
        self.assertTrue(stream.closed)
        self.assertTrue(all(reader.closed for reader in self.readers))


if __name__ == "__main__":
    unittest.main()
