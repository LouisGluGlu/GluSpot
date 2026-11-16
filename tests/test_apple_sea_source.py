import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from api.online_music import CatalogPage, OnlineSong, StreamInfo
from local import apple_sea_source as borrow
from local import sea_lyric_background as sea


def _apple(title="晴天", artist="周杰伦", seconds=269.0):
    return OnlineSong("apple_music", "i.1", title, artist, duration_seconds=seconds, catalog_id="12345")


def _netease(song_id, title, artist, seconds):
    return OnlineSong("netease", song_id, title, artist, duration_seconds=seconds)


class MatchTests(unittest.TestCase):
    def test_same_title_shared_artist_and_closest_length(self):
        candidates = (
            _netease("live", "晴天 (Live)", "周杰伦", 300.0),
            _netease("cover", "晴天", "某翻唱", 269.0),
            _netease("far", "晴天", "周杰伦", 275.0),
            _netease("near", "晴天", "周杰伦", 268.4),
        )
        self.assertEqual(borrow.best_match(candidates, "晴天", "周杰伦", 269.0).song_id, "near")

    def test_versions_and_featured_artists_still_match(self):
        candidates = (_netease("a", "Save Your Tears - Remix", "The Weeknd, Ariana Grande", 191.0),)
        self.assertEqual(borrow.best_match(candidates, "Save Your Tears (Remix)", "The Weeknd & Ariana Grande", 191.5).song_id, "a")

    def test_a_different_length_is_not_the_same_recording(self):
        self.assertIsNone(borrow.best_match((_netease("x", "晴天", "周杰伦", 280.0),), "晴天", "周杰伦", 269.0))


class StoreTests(unittest.TestCase):
    def test_round_trip_and_a_remembered_miss(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            borrow.save("apple-1", {"change_end": 61.0, "drums": [[1.0, 0.5]], "syllables": [], "pushes": [],
                                    "word_switch": None, "audio_seconds": 200.0}, root)
            self.assertEqual(borrow.load("apple-1", root)["change_end"], 61.0)
            borrow.save("apple-2", {"miss": True}, root)
            self.assertTrue(borrow.load("apple-2", root)["miss"])
            with mock.patch.object(borrow.time, "time", return_value=borrow.time.time() + 2 * 86400):
                self.assertIsNone(borrow.load("apple-2", root))
            self.assertIsNone(borrow.load("apple-3", root))

    def test_key_is_safe_and_uses_the_catalog_id(self):
        self.assertEqual(borrow.plan_key(_apple()), "apple-12345")
        self.assertEqual(borrow.plan_key(OnlineSong("apple_music", "i/../x", "t")), "apple-i_.._x")


class BorrowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def service(self, *, trial=False):
        calls = []
        service = SimpleNamespace()
        service.search = lambda source, query, **kw: calls.append(("search", source, query)) or CatalogPage(
            (_netease("n1", "晴天", "周杰伦", 268.8),), 1, False)
        service.stream_url = lambda source, song_id: calls.append(("stream", source, song_id)) or StreamInfo(
            source, song_id, "https://example.test/a.mp3", True, trial=trial)
        return service, calls

    def cache(self):
        released = []
        cache = SimpleNamespace(prepare=lambda stream, cancelled: SimpleNamespace(path=Path("x.mp3")),
                                release=lambda prepared: released.append(prepared))
        return cache, released

    def test_borrows_and_stores_only_the_analysis(self):
        service, calls = self.service()
        cache, released = self.cache()
        rng = np.random.default_rng(3)
        pcm = (rng.standard_normal(sea.RATE * 120) * 0.05).astype(np.float32)
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(borrow, "decode", return_value=pcm):
            data = borrow.borrow(service, cache, _apple(), root=Path(folder))
            self.assertIsNotNone(data)
            self.assertEqual(released and len(released), 1)
            self.assertEqual([c[0] for c in calls], ["search", "stream"])
            self.assertEqual(calls[0][1], "netease")
            stored = borrow.load("apple-12345", Path(folder))
            self.assertAlmostEqual(stored["audio_seconds"], 120.0, places=0)
            self.assertNotIn("url", str(stored))
            analyzer = sea.SeaAnalyzer()
            analyzer.adopt("apple-12345", stored)
            plan = analyzer.plan("apple-12345", (QColor("#225588"), QColor("#cc3344")), (1.0, 1.0), None, 120.0)
            self.assertTrue(plan.ready and plan.adopted)
            self.assertEqual(len(plan.drums), len(stored["drums"]))
            self.assertNotIn("apple-12345", analyzer._threads)   # nothing to decode for a borrowed plan

    def test_a_preview_clip_is_a_remembered_miss(self):
        service, _calls = self.service(trial=True)
        cache, _released = self.cache()
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(borrow.borrow(service, cache, _apple(), root=Path(folder)))
            self.assertTrue(borrow.load("apple-12345", Path(folder))["miss"])


if __name__ == "__main__":
    unittest.main()
