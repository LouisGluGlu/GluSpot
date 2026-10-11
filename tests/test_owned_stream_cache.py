from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import build_opener, ProxyHandler
import wave

from api.online_music import StreamInfo
from api.owned_stream_cache import OwnedStreamCache, PreparedAudio, StreamDownloadError, _SafeRedirect


class OwnedStreamCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(44100)
            output.writeframes(struct.pack("<hh", 100, -100) * 22050)
        self.payload = buffer.getvalue()
        self.requests = []
        self.ready = threading.Event()
        payload, requests, ready = self.payload, self.requests, self.ready

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                kind = self.path.split("?", 1)[0].lstrip("/")
                ordinal = 1 + len(requests)
                requests.append({"kind": kind, "range": self.headers.get("Range"),
                    "if_range": self.headers.get("If-Range"), "cookie": self.headers.get("Cookie")})
                if kind == "denied":
                    self.send_error(403)
                    return
                low, high = 0, len(payload) - 1
                partial = self.headers.get("Range", "").startswith("bytes=") and kind != "ignored"
                if partial:
                    low = int(self.headers["Range"][6:].split("-", 1)[0])
                body = payload[low:high + 1]
                content_type = "text/html" if kind == "html" else "audio/wav"
                length = str(len(body))
                if kind == "negative":
                    length = "-1"
                if kind == "large":
                    length = str(OwnedStreamCache.MAX_FILE_BYTES + 1)
                self.send_response(206 if partial else 200)
                self.send_header("Content-Type", content_type)
                if kind != "unknown":
                    self.send_header("Content-Length", length)
                if kind == "gzip":
                    self.send_header("Content-Encoding", "gzip")
                if kind != "no-validator":
                    self.send_header("ETag", '"changed"' if partial and kind == "changed" else '"fixture-v1"')
                if partial:
                    range_low = low + 1 if kind == "wrong-range" else low
                    self.send_header("Content-Range", f"bytes {range_low}-{high}/{len(payload)}")
                self.end_headers()
                if kind == "slow":
                    ready.set()
                    time.sleep(3)
                if kind in {"resumed", "ignored", "changed", "wrong-range", "no-validator", "mismatch", "permanent"} and (ordinal == 1 or kind == "permanent"):
                    body = body[:len(body) // 2]
                if partial and kind == "mismatch":
                    body = b"x" + body[1:]
                if kind == "invalid-audio":
                    body = b"x" * len(body)
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.cache = OwnedStreamCache(self.root, opener=build_opener(ProxyHandler({}), _SafeRedirect()))

    def tearDown(self):
        self.cache.close()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        self.temp.cleanup()

    def stream(self, kind="healthy", **changes):
        values = dict(source_id="netease", song_id="generated", url=f"http://127.0.0.1:{self.server.server_port}/{kind}?signed-fixture-only=secret",
            playable=True, trial=False)
        values.update(changes)
        return StreamInfo(**values)

    def assert_failure(self, kind, code):
        with self.assertRaises(StreamDownloadError) as failed:
            self.cache.prepare(self.stream(kind))
        self.assertEqual(failed.exception.code, code)
        self.assertNotIn("secret", str(failed.exception))
        self.assertEqual(self.cache._active, 0)
        self.assertFalse(self.cache._entries)
        self.assertFalse(list(self.cache._session.glob("*.part")))

    def test_complete_file_is_published_without_remote_address_or_credentials(self):
        prepared = self.cache.prepare(self.stream())
        self.assertIsInstance(prepared, PreparedAudio)
        self.assertEqual(prepared.path.read_bytes(), self.payload)
        self.assertEqual(prepared.byte_count, len(self.payload))
        self.assertEqual(prepared.request_count, 1)
        self.assertFalse(prepared.resumed)
        self.assertNotIn("secret", repr(prepared))
        self.assertNotIn("generated", prepared.path.name)
        self.assertTrue(all(request["cookie"] is None for request in self.requests))
        self.assertEqual({path.suffix for path in self.cache._session.iterdir()}, {".audio", ".lock"})

    def test_truncated_initial_response_resumes_exact_bytes_before_publication(self):
        prepared = self.cache.prepare(self.stream("resumed"))
        self.assertEqual(prepared.path.read_bytes(), self.payload)
        self.assertEqual(prepared.request_count, 2)
        self.assertTrue(prepared.resumed)
        self.assertTrue(self.requests[1]["range"].startswith("bytes="))
        self.assertEqual(self.requests[1]["if_range"], '"fixture-v1"')

    def test_no_validator_uses_verified_overlap_and_same_total(self):
        prepared = self.cache.prepare(self.stream("no-validator"))
        self.assertEqual(prepared.path.read_bytes(), self.payload)
        self.assertTrue(prepared.resumed)
        self.assertIsNone(self.requests[1]["if_range"])

    def test_server_ignoring_range_restarts_instead_of_appending(self):
        prepared = self.cache.prepare(self.stream("ignored"))
        self.assertEqual(prepared.path.read_bytes(), self.payload)
        self.assertEqual(prepared.byte_count, len(self.payload))
        self.assertEqual(prepared.request_count, 2)

    def test_invalid_ranges_changed_etag_and_mismatched_overlap_never_publish(self):
        for kind in ("wrong-range", "changed", "mismatch"):
            with self.subTest(kind=kind):
                self.requests.clear()
                self.assert_failure(kind, "invalid_response")

    def test_permanent_short_body_is_bounded_to_three_requests(self):
        self.assert_failure("permanent", "incomplete")
        self.assertEqual(len(self.requests), self.cache.MAX_REQUESTS)

    def test_missing_invalid_lengths_encoded_html_and_invalid_audio_fail_safely(self):
        for kind, reason in (("unknown", "invalid_response"), ("negative", "invalid_response"),
                ("large", "too_large"), ("gzip", "invalid_response"), ("html", "invalid_response"),
                ("invalid-audio", "invalid_response"), ("denied", "access_denied")):
            with self.subTest(kind=kind):
                self.requests.clear()
                self.assert_failure(kind, reason)
                self.assertEqual(len(self.requests), 1)

    def test_url_validation_and_expiration_precede_network_and_storage(self):
        for url in ("file:///private", "javascript:bad", "https://user:password@fixture.invalid/audio",
                "https://fixture.invalid/audio#auth=secret", "https://fixture.invalid/\nsecret"):
            with self.subTest(url=url):
                with self.assertRaises(StreamDownloadError):
                    self.cache.prepare(self.stream(url=url))
        with self.assertRaises(StreamDownloadError) as failed:
            self.cache.prepare(self.stream(expires_at=time.time() - 1))
        self.assertEqual(failed.exception.code, "expired")
        self.assertFalse(self.requests)
        self.assertFalse(self.cache._session.exists())

    def test_cached_bytes_use_independent_leases_and_trial_identity(self):
        first = self.cache.prepare(self.stream())
        second = self.cache.prepare(self.stream())
        self.assertEqual(first.path, second.path)
        self.assertNotEqual(first.lease, second.lease)
        self.assertEqual(len(self.requests), 1)
        self.cache.release(first)
        self.cache.release(first)
        self.assertEqual(next(iter(self.cache._entries.values())).pins, 1)
        trial = self.cache.prepare(self.stream(trial=True))
        self.assertTrue(trial.stream.trial)
        self.assertNotEqual(first.path, trial.path)
        self.assertEqual(len(self.requests), 2)

    def test_lru_eviction_cannot_delete_pinned_decoder_file(self):
        self.cache.MAX_CACHE_FILES = 2
        pinned = self.cache.prepare(self.stream(song_id="pinned"))
        old = self.cache.prepare(self.stream(song_id="old"))
        self.cache.release(old)
        fresh = self.cache.prepare(self.stream(song_id="new"))
        self.assertTrue(pinned.path.exists())
        self.assertTrue(fresh.path.exists())
        self.assertFalse(old.path.exists())
        with self.assertRaises(StreamDownloadError) as failed:
            self.cache.prepare(self.stream(song_id="busy"))
        self.assertEqual(failed.exception.code, "busy")

    def test_live_session_lock_survives_another_cache_and_close_cleans_own_session(self):
        first = self.cache.prepare(self.stream())
        other = OwnedStreamCache(self.root)
        try:
            second = other.prepare(self.stream(song_id="other"))
            self.assertTrue(first.path.exists())
            self.assertTrue(second.path.exists())
            other.close()
            self.assertTrue(first.path.exists())
            self.assertFalse(second.path.exists())
        finally:
            other.close()
        folder = self.cache._session
        self.cache.close()
        self.assertFalse(folder.exists())

    def test_abandoned_session_is_reclaimed_but_unknown_files_are_preserved(self):
        abandoned = self.root / ("audio-" + "1" * 32)
        abandoned.mkdir()
        (abandoned / "owner.lock").write_bytes(b"0")
        (abandoned / ("2" * 32 + ".part")).write_bytes(b"old")
        unknown = self.root / ("audio-" + "3" * 32)
        unknown.mkdir()
        (unknown / "owner.lock").write_bytes(b"0")
        (unknown / "user.txt").write_text("keep")
        self.cache.prepare(self.stream())
        self.assertFalse(abandoned.exists())
        self.assertTrue((unknown / "user.txt").exists())

    def test_close_cancels_blocked_request_and_reclaims_late_partial(self):
        errors = []

        def download():
            try:
                self.cache.prepare(self.stream("slow"))
            except StreamDownloadError as error:
                errors.append(error.code)

        worker = threading.Thread(target=download)
        worker.start()
        self.assertTrue(self.ready.wait(2))
        started = time.monotonic()
        self.cache.close()
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(errors, ["cancelled"])
        self.assertFalse(self.cache._session.exists())

    def test_duplicate_download_cleanup_failure_keeps_delivered_leases_and_budget(self):
        barrier = threading.Barrier(2)
        outcomes, errors = [], []
        original_unlink = Path.unlink

        def download(_stream, path, _cancelled):
            path.write_bytes(self.payload)
            barrier.wait(timeout=3)
            return len(self.payload), 1, False

        def unlink(path, *args, **kwargs):
            if path.suffix == ".part" and path.exists():
                raise PermissionError("fixture storage lock")
            return original_unlink(path, *args, **kwargs)

        def prepare():
            try:
                outcomes.append(self.cache.prepare(self.stream()))
            except Exception as error:
                errors.append(type(error).__name__)

        self.cache.MAX_CACHE_FILES = 2
        with patch.object(self.cache, "_download", side_effect=download), patch.object(Path, "unlink", unlink):
            workers = [threading.Thread(target=prepare) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=4)
            self.assertFalse(any(worker.is_alive() for worker in workers))
            self.assertFalse(errors)
            self.assertEqual(len(outcomes), 2)
            self.assertEqual(len(self.cache._leases), 2)
            self.assertEqual(next(iter(self.cache._entries.values())).pins, 2)
            self.assertEqual(len(self.cache._orphans), 1)
            with self.assertRaises(StreamDownloadError) as failed:
                self.cache.prepare(self.stream(song_id="new"))
            self.assertEqual(failed.exception.code, "busy")
        self.cache.prepare(self.stream(song_id="new"))
        self.assertFalse(self.cache._orphans)
        for prepared in outcomes:
            self.cache.release(prepared)
        self.assertEqual(next(iter(self.cache._entries.values())).pins, 0)

    def test_storage_failures_are_safe_and_failed_eviction_retains_accounting(self):
        with patch.object(self.cache, "_ensure_session", side_effect=OSError("secret file path")):
            with self.assertRaises(StreamDownloadError) as failed:
                self.cache.prepare(self.stream())
        self.assertEqual(failed.exception.code, "storage")
        self.assertNotIn("secret", str(failed.exception))
        old = self.cache.prepare(self.stream())
        self.cache.release(old)
        self.cache.MAX_CACHE_FILES = 1
        with patch.object(Path, "unlink", side_effect=PermissionError("locked")):
            with self.assertRaises(StreamDownloadError) as failed:
                self.cache.prepare(self.stream(song_id="new"))
        self.assertEqual(failed.exception.code, "busy")
        self.assertEqual(len(self.cache._entries), 1)
        self.assertTrue(old.path.exists())
        self.assertEqual(self.cache._active, 0)

    def test_close_unlock_failure_still_closes_handle_and_data_failure_retains_lock(self):
        prepared = self.cache.prepare(self.stream())
        handle = self.cache._session_lock
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == prepared.path:
                raise PermissionError("locked")
            return original_unlink(path, *args, **kwargs)

        with patch.object(self.cache, "_file_lock", side_effect=OSError("unlock failed")), patch.object(Path, "unlink", unlink):
            self.cache.close()
        self.assertTrue(handle.closed)
        self.assertTrue((self.cache._session / "owner.lock").exists())
        self.assertTrue(prepared.path.exists())

    def test_extreme_expiry_and_control_identifiers_are_rejected_before_io(self):
        for changes in ({"expires_at": 10 ** 400}, {"expires_at": True}, {"expires_at": float("nan")},
                {"source_id": "netease\nother"}, {"song_id": "song\nother"}, {"quality": "standard\nother"},
                {"quality": "x" * 513}, {"source_id": "\ud800"}):
            with self.subTest(changes=changes):
                with self.assertRaises(StreamDownloadError) as failed:
                    self.cache.prepare(self.stream(**changes))
                self.assertEqual(failed.exception.code, "invalid_response")
        self.assertFalse(self.requests)
        self.assertFalse(self.cache._session.exists())


if __name__ == "__main__":
    unittest.main()
