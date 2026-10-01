"""Upgrade player-bound cover size without changing artwork identity."""

import unittest
from urllib.parse import parse_qs, urlsplit

from api.artwork_sources import normalize_artwork_url


class ArtworkUrlUpgradeTests(unittest.TestCase):
    def test_known_cover_reuses_object_and_removes_duplicate_size_parameters(self):
        original = "https://p1.music.126.net/object-hash/12345.jpg?param=80y80&param=200y200"
        upgraded = normalize_artwork_url(original)
        self.assertEqual(urlsplit(upgraded).path, urlsplit(original).path)
        self.assertEqual(urlsplit(upgraded).netloc, urlsplit(original).netloc)
        self.assertEqual(parse_qs(urlsplit(upgraded).query), {"param": ["2560y2560"]})
        self.assertEqual(normalize_artwork_url(upgraded), upgraded)

    def test_unsized_known_image_gets_bounded_size_request(self):
        original = "http://p3.music.126.net/object/123.png"
        self.assertEqual(normalize_artwork_url(original, 1200), original + "?param=1200y1200")
        self.assertEqual(normalize_artwork_url(original, 100000), original + "?param=4096y4096")

    def test_unknown_signed_or_non_artwork_urls_are_preserved(self):
        originals = (
            "https://p1.music.126.net.evil.test/object/123.jpg?param=80y80",
            "https://other.test/123.jpg?param=80y80",
            "https://p1.music.126.net/object/123.jpg?param=80y80&token=signed",
            "https://p1.music.126.net/object/123.jpg?expires=123&signature=abc",
            "https://p1.music.126.net/api/lookup?param=80y80",
            "https://user:password@p1.music.126.net/object/123.jpg?param=80y80",
            "https://p1.music.126.net:1234/object/123.jpg?param=80y80",
            "https://p1.music.126.net:invalid/object/123.jpg",
            "file:///123.jpg",
        )
        for original in originals:
            with self.subTest(original=original):
                self.assertEqual(normalize_artwork_url(original), original)

    def test_invalid_size_keeps_the_existing_request(self):
        original = "https://p1.music.126.net/object/123.jpg?param=80y80"
        self.assertEqual(normalize_artwork_url(original, "invalid"), original)


if __name__ == "__main__":
    unittest.main()
