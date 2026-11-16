import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from api.lyrics import LocalLyricsStore
from api.providers import WindowsMediaProvider


def _provider(native="3458334243_XKUF5E", fresh=True):
    provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
    provider._capture_runtime = SimpleNamespace(_cef_source=SimpleNamespace(native_track_id=native, fresh=fresh))
    return provider


DETAIL = {"songs": [{"name": "Iceberg", "artists": [{"name": "Brian Cheng"}],
                     "album": {"picUrl": "https://p2.music.126.net/x.jpg"}, "duration": 257282}]}


class NeteaseExactCaptureTests(unittest.TestCase):
    def test_song_id_comes_from_a_fresh_native_track(self):
        self.assertEqual(_provider()._netease_exact_song_id(), "3458334243")
        self.assertEqual(_provider(fresh=False)._netease_exact_song_id(), "")
        self.assertEqual(_provider(native="abc_1")._netease_exact_song_id(), "")
        self.assertEqual(_provider(native=None)._netease_exact_song_id(), "")

    def test_details_are_used_only_when_they_name_the_captured_track(self):
        provider = _provider()
        with patch.object(WindowsMediaProvider, "_fetch_current_art_json", return_value=DETAIL):
            detail = provider._netease_exact_detail("Iceberg", "Brian Cheng", time.monotonic() + 3)
            self.assertEqual(detail["picture"], "https://p2.music.126.net/x.jpg")
            # A DJ speech segment or a moved capture names another song.
            self.assertIsNone(provider._netease_exact_detail("Another Song", "Brian Cheng", time.monotonic() + 3))

    def test_length_comes_from_fetched_details_of_the_same_song(self):
        provider = _provider()
        provider._netease_detail_cache = {"3458334243": {"title": "Iceberg", "duration": 257.282}}
        self.assertAlmostEqual(provider._netease_exact_cached_duration("Iceberg"), 257.282)
        self.assertEqual(provider._netease_exact_cached_duration("Other"), 0.0)
        self.assertEqual(_provider(fresh=False)._netease_exact_cached_duration("Iceberg"), 0.0)

    def test_exact_lyrics_replace_a_title_search_result(self):
        with tempfile.TemporaryDirectory() as folder:
            store = LocalLyricsStore(root=Path(folder))
            match = store.parse_online_lyrics(raw_lrc="[00:01.00]official line one\n[00:05.00]official line two",
                                              source="netease")
            self.assertTrue(store.adopt_exact_lyrics("Iceberg", "Brian Cheng", "", 257.0, match))
            cached = store.resolve_cached_or_local("Iceberg", "Brian Cheng", "", 257.0)
            self.assertEqual([line.text for line in cached.timed_lyrics][:1], ["official line one"])


if __name__ == "__main__":
    unittest.main()
