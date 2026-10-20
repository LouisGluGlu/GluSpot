from __future__ import annotations

import io
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request
from urllib.error import HTTPError, URLError

from PySide6.QtGui import QColor

from api.models import ColorToken
from api.online_music_artwork import (
    _ArtworkRedirectHandler, OnlineArtworkError, fetch_online_artwork, fetch_online_thumbnail, online_artwork_colors,
)
from tests.test_music_palette import _png


class ArtworkResponse(io.BytesIO):
    def __init__(self, content, *, url="https://example.test/cover.png", headers=None):
        super().__init__(content)
        self.url = url
        self.headers = headers or {}

    def geturl(self):
        return self.url


class OnlineArtworkTests(unittest.TestCase):
    def setUp(self):
        self.content = _png(QColor(215, 35, 52))

    def fetch(self, content=None, **response_args):
        opener = Mock()
        opener.open.return_value = ArtworkResponse(self.content if content is None else content, **response_args)
        with patch("api.online_music_artwork.build_opener", return_value=opener):
            result = fetch_online_artwork("https://example.test/cover.png")
        return result, opener

    def test_valid_original_is_returned_and_existing_palette_is_reused(self):
        result, opener = self.fetch()
        self.assertEqual(result, self.content)
        request = opener.open.call_args.args[0]
        self.assertNotIn("Cookie", request.headers)
        self.assertNotIn("Authorization", request.headers)
        colors = online_artwork_colors(result, ColorToken(58, 68, 88), ColorToken(34, 44, 62))
        self.assertGreater(colors[0].red, colors[0].green + 35)

    def test_invalid_schemes_credentials_controls_and_ports_do_not_request(self):
        with patch("api.online_music_artwork.build_opener") as create:
            for url in ("file:///cover.png", "data:image/png;base64,x", "https://user:secret@example.test/x",
                        "https://example.test:9999/x", "https://example.test/\nx", "https://[broken/x", ""):
                self.assertEqual(fetch_online_artwork(url), b"")
            create.assert_not_called()

    def test_redirect_cannot_change_to_credentialed_or_non_http_target(self):
        handler = _ArtworkRedirectHandler()
        request = Request("https://example.test/x")
        for target in ("file:///cover.png", "ftp://example.test/x", "https://user:secret@example.test/x"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, "Found", {}, target)
        redirected = handler.redirect_request(request, None, 302, "Found", {}, "https://cdn.example.test/x")
        self.assertEqual(redirected.full_url, "https://cdn.example.test/x")

    def test_signed_url_is_preserved_and_known_unsigned_netease_url_is_upgraded(self):
        for url, expected in (
            ("https://p1.music.126.net/cover/x.jpg?token=synthetic&param=64y64",
             "https://p1.music.126.net/cover/x.jpg?token=synthetic&param=64y64"),
            ("https://p1.music.126.net/cover/x.jpg?param=64y64",
             "https://p1.music.126.net/cover/x.jpg?param=2560y2560"),
        ):
            opener = Mock()
            opener.open.return_value = ArtworkResponse(self.content, url=url)
            with patch("api.online_music_artwork.build_opener", return_value=opener):
                self.assertEqual(fetch_online_artwork(url), self.content)
            self.assertEqual(opener.open.call_args.args[0].full_url, expected)

    def test_announced_and_unannounced_oversized_responses_are_rejected(self):
        with patch("api.online_music_artwork.ONLINE_ARTWORK_MAX_BYTES", 16):
            self.assertEqual(self.fetch(headers={"Content-Length": "17"})[0], b"")
            self.assertEqual(self.fetch()[0], b"")

    def test_dimension_and_pixel_caps_reject_before_full_decode(self):
        with patch("api.online_music_artwork.ONLINE_ARTWORK_MAX_SIDE", 16):
            self.assertEqual(self.fetch()[0], b"")
        with patch("api.online_music_artwork.ONLINE_ARTWORK_MAX_PIXELS", 100):
            self.assertEqual(self.fetch()[0], b"")

    def test_read_budget_and_invalid_bitmap_finish_with_empty_bytes(self):
        with patch("api.online_music_artwork.time.monotonic", side_effect=[0.0, 7.0]):
            self.assertEqual(self.fetch()[0], b"")
        self.assertEqual(self.fetch(b"not a bitmap")[0], b"")
        self.assertEqual(self.fetch(url="file:///redirected.png")[0], b"")
        with patch("api.online_music_artwork.build_opener", side_effect=RuntimeError("signed-url-secret")):
            self.assertEqual(fetch_online_artwork("https://example.test/x?token=synthetic"), b"")

    def test_decodable_but_short_announced_response_is_not_published(self):
        result, _ = self.fetch(headers={"Content-Length": str(len(self.content) + 5)})
        self.assertEqual(result, b"")
        opener = Mock()
        opener.open.return_value = ArtworkResponse(self.content, headers={"Content-Length": str(len(self.content) + 5)})
        with patch("api.online_music_artwork.build_opener", return_value=opener), \
                self.assertRaises(OnlineArtworkError) as caught:
            fetch_online_artwork("https://example.test/cover.png", raise_errors=True)
        self.assertEqual(caught.exception.code, "incomplete")

    def test_failed_known_size_upgrade_falls_back_to_exact_song_bound_url(self):
        original = "https://p1.music.126.net/cover/x.jpg?param=64y64"
        opener = Mock()
        opener.open.side_effect = [URLError("synthetic signed-url-secret"), ArtworkResponse(self.content, url=original)]
        with patch("api.online_music_artwork.build_opener", return_value=opener):
            self.assertEqual(fetch_online_artwork(original, raise_errors=True), self.content)
        self.assertEqual([call.args[0].full_url for call in opener.open.call_args_list], [
            "https://p1.music.126.net/cover/x.jpg?param=2560y2560", original])

    def test_visible_queue_target_requests_small_same_object_with_existing_exact_fallback(self):
        original = "https://p1.music.126.net/cover/x.jpg?param=64y64"
        opener = Mock()
        opener.open.side_effect = [URLError("synthetic transient"), ArtworkResponse(self.content, url=original)]
        with patch("api.online_music_artwork.build_opener", return_value=opener):
            self.assertEqual(fetch_online_artwork(original, raise_errors=True, target_side=320), self.content)
        self.assertEqual([call.args[0].full_url for call in opener.open.call_args_list], [
            "https://p1.music.126.net/cover/x.jpg?param=320y320", original])

    def test_library_and_search_thumbnail_do_not_download_the_player_original(self):
        original = "https://p1.music.126.net/cover/x.jpg?param=64y64"
        opener = Mock()
        opener.open.return_value = ArtworkResponse(self.content, url=original)
        with patch("api.online_music_artwork.build_opener", return_value=opener):
            self.assertFalse(fetch_online_thumbnail(original).isNull())
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(opener.open.call_args.args[0].full_url,
                         "https://p1.music.126.net/cover/x.jpg?param=320y320")

    def test_queue_target_preserves_signed_and_unknown_urls_exactly(self):
        for original in ("https://p1.music.126.net/x.jpg?token=synthetic&param=64y64",
                         "https://example.test/cover.png?size=1024"):
            with self.subTest(original=original):
                opener = Mock()
                opener.open.return_value = ArtworkResponse(self.content, url=original)
                with patch("api.online_music_artwork.build_opener", return_value=opener):
                    self.assertEqual(fetch_online_artwork(original, target_side=320), self.content)
                self.assertEqual(opener.open.call_count, 1)
                self.assertEqual(opener.open.call_args.args[0].full_url, original)

    def test_signed_url_does_not_invent_fallback_or_expose_failure_text(self):
        opener = Mock()
        opener.open.side_effect = URLError("signed-url-secret")
        with patch("api.online_music_artwork.build_opener", return_value=opener), \
                self.assertRaises(OnlineArtworkError) as caught:
            fetch_online_artwork("https://p1.music.126.net/x.jpg?token=synthetic&param=64y64", raise_errors=True)
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(str(caught.exception), "network")

    def test_http_failures_have_sanitized_retry_classification(self):
        for status, expected in ((503, "network"), (429, "network"), (404, "not_found"), (403, "access_denied")):
            with self.subTest(status=status):
                opener = Mock()
                opener.open.side_effect = HTTPError("https://example.test/private", status, "secret", {}, None)
                with patch("api.online_music_artwork.build_opener", return_value=opener), \
                        self.assertRaises(OnlineArtworkError) as caught:
                    fetch_online_artwork("https://example.test/x.png", raise_errors=True)
                self.assertEqual(caught.exception.code, expected)

    def test_corrupt_http_success_is_retryable_without_exposing_response(self):
        opener = Mock()
        opener.open.return_value = ArtworkResponse(b"corrupt response secret")
        with patch("api.online_music_artwork.build_opener", return_value=opener), \
                self.assertRaises(OnlineArtworkError) as caught:
            fetch_online_artwork("https://example.test/x.png", raise_errors=True)
        self.assertEqual(str(caught.exception), "invalid_image")


if __name__ == "__main__":
    unittest.main()
