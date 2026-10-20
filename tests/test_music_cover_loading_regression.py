"""Track-bound artwork lookup state across the provider and large music page."""

from __future__ import annotations

import asyncio
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from api.providers import WindowsMediaProvider
from local.music_surface_window import MusicSurfaceWindow
from tests import test_artwork_sources as artwork_tests
from tests.test_music_centered_layout import _Owner
from tests.test_provider_state import FakeSession


def cover(side: int, color: QColor) -> QImage:
    image = QImage(side, side, QImage.Format.Format_RGB32)
    image.fill(color)
    return image


class ArtworkLoadingSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _Owner()
        self.owner.media_is_playing = False
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.render(100.0)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def render(self, now: float) -> None:
        target = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            self.window.render(target)

    def start(self, key: str, now: float = 101.0) -> QImage:
        preview = cover(160, QColor(80, 55, 60))
        self.owner.media_track_id = key
        self.owner.track_title = key
        self.owner.album_art = preview
        self.owner.album_art_source = "thumbnail"
        self.owner.album_art_lookup_state = "pending"
        self.render(now)
        return preview

    def test_failed_lookup_does_not_expose_pixelated_preview(self):
        preview = self.start("pending-track")
        self.assertTrue(self.window._art_loading_waiting)
        self.assertTrue(self.window._should_blur_preview(preview))
        self.render(103.0)
        self.assertTrue(self.window._art_loading_waiting)
        self.assertIn("art_loading", self.window._transport_feedback)

        self.owner.album_art_lookup_state = "failed"
        self.render(103.1)
        self.assertFalse(self.window._art_loading_waiting)
        self.assertTrue(self.window._should_blur_preview(preview))
        self.assertIn("art_loading", self.window._transport_feedback)
        self.render(107.0)
        self.window._update_transport_feedback_regions(107.0)
        self.assertNotIn("art_loading", self.window._transport_feedback)
        self.assertTrue(self.window._should_blur_preview(preview))

    def test_elapsed_time_cannot_promote_a_small_preview(self):
        preview = self.start("late-track")
        self.render(118.99)
        self.assertTrue(self.window._art_loading_waiting)
        # Twenty seconds exceeds the former fixed eighteen-second visual cap.
        self.render(121.01)
        self.assertTrue(self.window._art_loading_waiting)
        self.assertTrue(self.window._should_blur_preview(preview))
        self.assertIn("art_loading", self.window._transport_feedback)

        final = cover(1200, QColor(25, 82, 111))
        self.owner.album_art = final
        self.owner.album_art_source = "cover-art-archive-original"
        self.owner.album_art_lookup_state = "ready"
        self.render(121.2)
        self.assertEqual(self.window._track_visual[3].cacheKey(), final.cacheKey())
        self.assertFalse(self.window._should_blur_preview(final))

    def test_ready_label_and_relabel_cannot_promote_the_same_small_image(self):
        preview = self.start("retained-preview")
        self.owner.album_art_source = "original"
        self.owner.album_art_lookup_state = "pending"
        self.render(101.2)
        self.assertTrue(self.window._art_loading_waiting)
        self.assertTrue(self.window._should_blur_preview(preview))

        self.owner.album_art_source = "thumbnail"
        self.owner.album_art_lookup_state = "ready"
        self.render(101.4)
        self.assertTrue(self.window._art_loading_waiting)
        self.assertTrue(self.window._should_blur_preview(preview))

    def test_completed_original_can_reuse_the_same_displayable_preview_pixels(self):
        self.start("same-object-upgrade")
        image = cover(320, QColor(25, 82, 111))
        self.owner.album_art = image
        self.owner.album_art_source = "thumbnail"
        self.render(101.2)
        self.assertTrue(self.window._art_loading_waiting)
        self.assertEqual(self.window._art_loading_preview_key, image.cacheKey())
        self.owner.album_art_source = "original"
        self.owner.album_art_lookup_state = "original"
        self.render(101.4)
        self.assertFalse(self.window._art_loading_waiting)
        self.assertFalse(self.window._should_blur_preview(image))
        self.render(110.)
        self.window._update_transport_feedback_regions(110.)
        self.assertNotIn("art_loading", self.window._transport_feedback)

    def test_ready_without_displayable_image_keeps_loading_visual(self):
        self.start("invalid-original")
        self.owner.album_art = QImage()
        self.owner.album_art_source = ""
        self.owner.album_art_lookup_state = "ready"
        self.render(101.3)
        self.assertTrue(self.window._art_loading_waiting)
        self.assertIn("art_loading", self.window._transport_feedback)
        self.assertTrue(self.window._track_visual[3].isNull())

    def test_new_track_replaces_old_failure_and_ignores_old_image_state(self):
        old_preview = self.start("old-track")
        self.owner.album_art_lookup_state = "failed"
        self.render(101.4)
        self.assertFalse(self.window._art_loading_waiting)
        self.assertTrue(self.window._should_blur_preview(old_preview))

        new_preview = self.start("new-track", 101.6)
        self.assertTrue(self.window._art_loading_waiting)
        self.assertEqual(self.window._art_loading_track_key, "new-track")
        self.assertEqual(self.window._art_loading_preview_key, new_preview.cacheKey())
        self.assertNotEqual(self.window._art_loading_preview_key, old_preview.cacheKey())
        self.render(102.5)
        self.assertTrue(self.window._art_loading_waiting)

    def test_first_opened_track_can_start_without_preview_then_accept_late_thumbnail(self):
        self.window.shutdown()
        self.owner.media_track_id = "first-empty"
        self.owner.track_title = "first-empty"
        self.owner.album_art = QImage()
        self.owner.album_art_source = ""
        self.owner.album_art_lookup_state = "pending"
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.render(100.0)
        self.assertEqual(self.window._art_loading_track_key, "first-empty")
        self.assertTrue(self.window._art_loading_waiting)
        self.assertIsNone(self.window._art_loading_preview_key)

        preview = cover(160, QColor(59, 83, 98))
        self.owner.album_art = preview
        self.owner.album_art_source = "thumbnail"
        self.render(100.7)
        self.assertTrue(self.window._art_loading_waiting)
        self.assertEqual(self.window._art_loading_preview_key, preview.cacheKey())
        self.assertTrue(self.window._should_blur_preview(preview))

    def test_first_opened_track_with_final_art_does_not_start_loading_visual(self):
        self.window.shutdown()
        final = cover(600, QColor(59, 83, 98))
        self.owner.media_track_id = "first-final-art"
        self.owner.track_title = "first-final-art"
        self.owner.album_art = final
        self.owner.album_art_source = "original"
        self.owner.album_art_lookup_state = "ready"
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.render(100.0)
        self.assertEqual(self.window._art_loading_track_key, "first-final-art")
        self.assertFalse(self.window._art_loading_waiting)
        self.assertFalse(self.window._should_blur_preview(final))
        self.assertNotIn("art_loading", self.window._transport_feedback)

    def test_failed_lookup_then_later_clear_art_ends_only_this_track_wait(self):
        preview = self.start("retry-track")
        self.owner.album_art_lookup_state = "failed"
        self.render(101.5)
        self.assertTrue(self.window._should_blur_preview(preview))

        self.owner.album_art_lookup_state = "pending"
        self.render(148.0)
        self.assertTrue(self.window._art_loading_waiting)
        self.assertTrue(self.window._should_blur_preview(preview))

        final = cover(1800, QColor(26, 75, 108))
        self.owner.album_art = final
        self.owner.album_art_source = "cover-art-archive-original"
        self.owner.album_art_lookup_state = "ready"
        self.render(148.1)
        self.assertEqual(self.window._track_visual[3].cacheKey(), final.cacheKey())
        self.assertFalse(self.window._art_loading_waiting)
        self.assertFalse(self.window._should_blur_preview(final))

    def test_final_source_is_not_rejected_by_surface_geometry(self):
        self.start("final-source")
        final = cover(600, QColor(42, 70, 109))
        self.owner.album_art = final
        self.owner.album_art_source = "original"
        self.owner.album_art_lookup_state = "ready"
        self.render(101.3)
        self.assertFalse(self.window._art_loading_waiting)
        self.assertFalse(self.window._should_blur_preview(final))

    def test_thumbnail_source_does_not_finish_loading_based_on_dimensions(self):
        self.start("large-thumbnail")
        large_thumbnail = cover(1800, QColor(29, 68, 97))
        self.owner.album_art = large_thumbnail
        self.owner.album_art_source = "thumbnail"
        self.owner.album_art_lookup_state = "ready"
        self.render(101.3)
        self.assertEqual(self.window._track_visual[3].cacheKey(), large_thumbnail.cacheKey())
        self.assertTrue(self.window._art_loading_waiting)
        self.assertTrue(self.window._should_blur_preview(large_thumbnail))

    def test_large_high_dpi_surface_does_not_reject_final_source(self):
        self.start("high-dpi-track")
        self.window.resize(3840, 2160)
        self.window._fullscreen = True
        image = cover(1800, QColor(34, 62, 96))
        self.owner.album_art = image
        self.owner.album_art_source = "original"
        self.owner.album_art_lookup_state = "ready"
        with patch.object(MusicSurfaceWindow, "devicePixelRatioF", return_value=2.0):
            cover_side = self.window._current_cover_rect(101.2).width()
            self.assertGreater(cover_side * 2.0, image.width())
            self.window._track_transition(101.2, QColor(34, 62, 96), QColor(20, 25, 40))
            self.assertFalse(self.window._art_loading_waiting)
            self.assertFalse(self.window._should_blur_preview(image))

    def test_completed_cover_does_not_restart_wait_when_surface_or_dpi_changes(self):
        self.start("completed-source")
        final = cover(600, QColor(42, 70, 109))
        self.owner.album_art = final
        self.owner.album_art_source = "netease-original"
        self.render(101.3)
        self.assertFalse(self.window._art_loading_waiting)
        self.window._update_transport_feedback_regions(110.0)
        self.assertNotIn("art_loading", self.window._transport_feedback)

        self.window.resize(3840, 2160)
        self.window._fullscreen = True
        with patch.object(MusicSurfaceWindow, "devicePixelRatioF", return_value=2.0):
            self.window._track_transition(110.2, QColor(42, 70, 109), QColor(20, 25, 40))
            self.assertFalse(self.window._art_loading_waiting)
            self.assertFalse(self.window._should_blur_preview(final))
            self.assertNotIn("art_loading", self.window._transport_feedback)

    def test_first_opened_final_cover_is_remembered_for_real_thumbnail_regression(self):
        self.window.shutdown()
        final = cover(600, QColor(42, 70, 109))
        self.owner.media_track_id = "first-completed"
        self.owner.track_title = "first-completed"
        self.owner.album_art = final
        self.owner.album_art_source = "cover-art-archive-original"
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.render(100.0)
        self.window.resize(1920, 1080)
        self.window._track_transition(100.2, QColor(42, 70, 109), QColor(20, 25, 40))
        self.assertFalse(self.window._art_loading_waiting)
        self.assertNotIn("art_loading", self.window._transport_feedback)

        preview = cover(160, QColor(42, 70, 109))
        self.owner.album_art = preview
        self.owner.album_art_source = "thumbnail"
        self.window._track_transition(100.3, QColor(42, 70, 109), QColor(20, 25, 40))
        self.assertTrue(self.window._art_loading_waiting)
        self.assertEqual(self.window._art_loading_preview_key, preview.cacheKey())
        self.assertTrue(self.window._should_blur_preview(preview))
        self.assertIn("art_loading", self.window._transport_feedback)

        self.owner.album_art_source = "cover-art-archive-original"
        self.window._track_transition(100.4, QColor(42, 70, 109), QColor(20, 25, 40))
        self.assertTrue(self.window._art_loading_waiting)
        self.owner.album_art = final
        self.window._track_transition(100.5, QColor(42, 70, 109), QColor(20, 25, 40))
        self.assertFalse(self.window._art_loading_waiting)
        self.assertFalse(self.window._should_blur_preview(final))

    def test_wait_check_does_not_evaluate_cover_geometry_or_lyric_layout(self):
        self.start("isolated-wait-check")
        with patch.object(self.window, "_current_cover_rect", side_effect=AssertionError("unexpected geometry lookup")):
            self.assertTrue(self.window._artwork_is_loading())
            self.owner.album_art = cover(600, QColor(42, 70, 109))
            self.owner.album_art_source = "original"
            self.assertFalse(self.window._artwork_is_loading())


class ArtworkLookupProviderStateTests(unittest.TestCase):
    def setUp(self):
        fixture = artwork_tests.ArtworkVisualPriorityTests()
        self.preview_bytes = fixture.png(64, 0xFF806020)
        self.original_bytes = fixture.png(2560, 0xFF204060)
        self.provider = fixture.provider(self.preview_bytes)
        self.provider._online_current_art_lookup_enabled = True
        self.provider._current_art_lookup_lock = threading.Lock()
        self.provider._current_art_lookup_inflight = set()
        self.provider._current_art_lookup_failed_until = {}
        self.provider._current_art_url_by_track_id = {}
        self.provider._queue_art_lock = threading.Lock()
        self.provider._queue_art_failed_until = {}

    def read(self):
        result = asyncio.run(self.provider._read_visual_data())
        self.provider._last_data = result
        return result.track

    def test_track_data_reports_pending_failure_and_ready_without_losing_preview(self):
        first = self.read()
        key = first.track_id
        self.assertEqual(first.album_art_source, "thumbnail")
        self.assertEqual(first.album_art_lookup_state, "pending")
        with self.provider._current_art_lookup_lock:
            self.provider._current_art_lookup_failed_until[key] = time.monotonic() + 45.0
        failed = self.read()
        self.assertEqual(failed.track_id, key)
        self.assertEqual(failed.album_art_lookup_state, "failed")
        self.assertEqual(failed.album_art_bytes, self.preview_bytes)

        self.provider._current_online_album_art_bytes = lambda _key: self.original_bytes
        ready = self.read()
        self.assertEqual(ready.album_art_lookup_state, "ready")
        self.assertEqual(ready.album_art_bytes, self.original_bytes)

    def test_undersized_player_original_still_allows_an_artwork_upgrade(self):
        self.provider._player_window_track_metadata = lambda *_args: SimpleNamespace(
            title="Song", artist="Artist", album_title="Album",
            duration_seconds=240.0, album_art_bytes=self.preview_bytes,
        )
        self.provider._queue_album_art_bytes = lambda _track: self.preview_bytes
        first = self.read()
        self.assertEqual(first.album_art_source, "thumbnail")
        self.assertEqual(first.album_art_bytes, self.preview_bytes)
        self.assertEqual(first.album_art_lookup_state, "pending")
        self.provider._schedule_current_netease_art_lookup.assert_called()

    def test_next_track_does_not_inherit_prior_failure(self):
        first = self.read()
        with self.provider._current_art_lookup_lock:
            self.provider._current_art_lookup_failed_until[first.track_id] = time.monotonic() + 45.0
        self.assertEqual(self.read().album_art_lookup_state, "failed")
        self.provider._get_session = lambda: asyncio.sleep(
            0, result=FakeSession("cloudmusic.exe", "Another Song", "Artist", True, 1, 215)
        )
        second = self.read()
        self.assertNotEqual(second.track_id, first.track_id)
        self.assertEqual(second.album_art_lookup_state, "pending")

    def test_metadata_deadline_ends_pending_and_late_worker_cannot_publish(self):
        # Drive the real scheduler with deterministic worker/timer fakes. The
        # provider read must carry each state in TrackData, not only an
        # internal side table used by the visual surface.
        del self.provider._schedule_current_netease_art_lookup
        self.provider._music_player_adapter = "netease"
        self.provider._resolve_netease_art_url = Mock(return_value="")
        self.provider._musicbrainz_artwork_resolver = SimpleNamespace(
            resolve=Mock(return_value="https://archive.org/download/late/front.jpg")
        )
        self.provider._schedule_queue_album_art_prefetch = Mock()
        artwork_tests.ManualThread.jobs = []
        artwork_tests.ManualTimer.timers = []
        with patch("api.providers.threading.Thread", artwork_tests.ManualThread), patch(
            "api.providers.threading.Timer", artwork_tests.ManualTimer
        ):
            pending = self.read()
            self.assertEqual(pending.album_art_lookup_state, "pending")
            self.assertEqual(len(artwork_tests.ManualTimer.timers), 1)
            self.assertEqual(artwork_tests.ManualTimer.timers[0].interval, 9.0)
            worker = artwork_tests.ManualThread.jobs.pop(0)
            artwork_tests.ManualTimer.timers[0].callback()
            failed = self.read()
            self.assertEqual(failed.track_id, pending.track_id)
            self.assertEqual(failed.album_art_lookup_state, "failed")
            self.assertEqual(failed.album_art_bytes, self.preview_bytes)
            worker()
            self.assertNotIn(pending.track_id, self.provider._current_art_url_by_track_id)
            self.provider._schedule_queue_album_art_prefetch.assert_not_called()

    def test_download_failure_reports_failed_for_low_resolution_preview(self):
        first = self.read()
        url = "https://archive.org/download/matched/front.jpg"
        with self.provider._current_art_lookup_lock:
            self.provider._current_art_url_by_track_id[first.track_id] = url
        with self.provider._queue_art_lock:
            self.provider._queue_art_failed_until[url] = time.monotonic() + 30.0
        failed = self.read()
        self.assertEqual(failed.album_art_lookup_state, "failed")
        self.assertEqual(failed.album_art_source, "thumbnail")
        self.assertEqual(failed.album_art_bytes, self.preview_bytes)

    def test_real_1200_caa_cover_completes_without_requery_loop(self):
        first = self.read()
        key = first.track_id
        fallback_1200 = artwork_tests.ArtworkVisualPriorityTests.png(1200, 0xFF304060)
        self.provider._current_art_url_by_track_id = {key: "https://coverartarchive.org/release/one/front-1200"}
        self.provider._current_art_lookup_sources = {key: "cover-art-archive"}
        self.provider._queue_album_art_bytes = lambda _track: fallback_1200

        second = self.read()
        self.assertEqual(second.album_art_bytes, fallback_1200)
        self.assertEqual(second.album_art_source, "cover-art-archive-original")
        self.assertEqual(second.album_art_lookup_state, "ready")
        third = self.read()
        self.assertEqual(third.album_art_lookup_state, "ready")
        self.assertEqual(third.album_art_bytes, fallback_1200)
        # The existing URL remains bound to this track, so the actual
        # scheduler must not start a fresh metadata request on each poll.
        real_schedule = WindowsMediaProvider._schedule_current_netease_art_lookup.__get__(self.provider)
        self.assertFalse(real_schedule(key, "Song", "Artist", album_title="Album", duration_seconds=240.0))
        self.assertEqual(
            self.provider._current_art_url_by_track_id[key],
            "https://coverartarchive.org/release/one/front-1200",
        )

    def test_source_limited_1417_cover_completes_and_revisits_without_caa_lookup(self):
        # A 2560 request can return the source's real 1417-pixel original.
        # A decoded original is not a failed download of imaginary pixels.
        first = self.read()
        key = first.track_id
        url = "https://music.126.net/exact-album.jpg?param=2560y2560"
        source_art = artwork_tests.ArtworkVisualPriorityTests.png(1417, 0xFF304060)
        self.provider._current_art_url_by_track_id = {key: url}
        self.provider._current_art_lookup_sources = {key: "netease"}
        self.provider._queue_album_art_bytes = lambda track: (
            source_art if getattr(track, "album_art_url", "") == url else b""
        )
        self.provider._resolve_netease_art_url = Mock(return_value=url)
        self.provider._musicbrainz_artwork_resolver = SimpleNamespace(resolve=Mock(return_value=""))
        del self.provider._schedule_current_netease_art_lookup
        artwork_tests.ManualThread.jobs = []
        artwork_tests.ManualTimer.timers = []
        with patch("api.providers.threading.Thread", artwork_tests.ManualThread), patch(
            "api.providers.threading.Timer", artwork_tests.ManualTimer
        ):
            for revisit in (False, True):
                if revisit:
                    self.provider._last_data = None
                settled = self.read()
                self.assertEqual(settled.album_art_bytes, source_art)
                self.assertEqual(settled.album_art_source, "netease-original")
                self.assertEqual(settled.album_art_lookup_state, "ready")
                self.assertEqual(artwork_tests.ManualThread.jobs, [])
        self.provider._resolve_netease_art_url.assert_not_called()
        self.provider._musicbrainz_artwork_resolver.resolve.assert_not_called()

    def test_temporary_metadata_failure_can_retry_same_track_after_cooldown(self):
        first = self.read()
        key = first.track_id
        self.provider._current_art_lookup_failed_until[key] = time.monotonic() + 45.0
        self.provider._resolve_netease_art_url = Mock(return_value="https://example.test/recovered.jpg")
        self.provider._musicbrainz_artwork_resolver = SimpleNamespace(resolve=Mock(return_value=""))
        self.provider._schedule_queue_album_art_prefetch = Mock()
        del self.provider._schedule_current_netease_art_lookup
        artwork_tests.ManualThread.jobs = []
        artwork_tests.ManualTimer.timers = []
        with patch("api.providers.threading.Thread", artwork_tests.ManualThread), patch(
            "api.providers.threading.Timer", artwork_tests.ManualTimer
        ):
            self.assertEqual(self.read().album_art_lookup_state, "failed")
            self.assertEqual(artwork_tests.ManualThread.jobs, [])
            self.provider._current_art_lookup_failed_until[key] = time.monotonic() - 1.0
            retried = self.read()
            self.assertEqual(retried.track_id, key)
            self.assertEqual(retried.album_art_lookup_state, "pending")
            self.assertEqual(len(artwork_tests.ManualThread.jobs), 1)
            artwork_tests.ManualThread.jobs.pop(0)()
            self.assertEqual(self.provider._current_art_url_by_track_id[key], "https://example.test/recovered.jpg")
        self.provider._resolve_netease_art_url.assert_called_once()
        self.provider._schedule_queue_album_art_prefetch.assert_called_once_with("https://example.test/recovered.jpg")


if __name__ == "__main__":
    unittest.main()
