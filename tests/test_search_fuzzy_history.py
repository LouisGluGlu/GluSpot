import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from api.online_music import AccountSnapshot, OnlineSong
from local.music_library_query import SearchHistory, local_collection_matches, local_song_matches
from local.music_library_window import LibraryCollection, MusicLibraryWindow


def _song(song_id, title, artist, album=""):
    return OnlineSong("netease", song_id, title, artist, album=album)


class FuzzyMatchTests(unittest.TestCase):
    def test_ignores_case_width_spacing_and_punctuation(self):
        songs = (_song("1", "Blinding Lights", "The Weeknd"), _song("2", "Save Your Tears", "The Weeknd"))
        self.assertEqual(local_song_matches(songs, "blindinglights")[0].song_id, "1")
        self.assertEqual(local_song_matches(songs, "ＢＬＩＮＤＩＮＧ")[0].song_id, "1")      # full width
        self.assertEqual(local_song_matches(songs, "save-your")[0].song_id, "2")

    def test_artist_and_title_words_together(self):
        songs = (_song("1", "晴天", "周杰伦"), _song("2", "七里香", "周杰伦"), _song("3", "晴天", "某翻唱"))
        found = local_song_matches(songs, "周杰伦 晴天")
        self.assertEqual([song.song_id for song in found], ["1"])

    def test_typo_tolerant_letters_in_order_rank_last(self):
        songs = (_song("1", "Levitating", "Dua Lipa"), _song("2", "Lvtng club", "x"))
        found = local_song_matches(songs, "lvtng")
        self.assertEqual([song.song_id for song in found], ["2", "1"])   # substring before subsequence
        self.assertEqual(local_song_matches(songs, "zz"), ())

    def test_collections_use_the_same_rules(self):
        rows = (LibraryCollection("a", "playlist", "Late Night Drive", ""),)
        self.assertEqual(local_collection_matches(rows, "latenight"), rows)


class HistoryTests(unittest.TestCase):
    def test_history_reports_changes_and_clears(self):
        saved = []
        history = SearchHistory(items=("a", "b"), on_change=saved.append)
        history.remember("a")
        self.assertEqual(history.items, ("b", "a"))
        self.assertEqual(saved[-1], ("b", "a"))
        history.clear()
        self.assertEqual(history.items, ())
        self.assertEqual(saved[-1], ())


class HistoryPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_empty_search_section_lists_saved_searches_and_reruns_one(self):
        store = {"netease": ["晴天", "after hours"]}
        window = MusicLibraryWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.set_search_history_store(lambda source: store.get(source, ()),
                                        lambda source, items: store.__setitem__(source, list(items)))
        window.set_account(AccountSnapshot("netease", True, "fixture", "账号"))
        window.show()
        window._variant_view.select_section("search")
        self.app.processEvents()
        panel = window.search_history_panel
        self.assertTrue(panel.isVisible())
        self.assertEqual(panel.items, ("after hours", "晴天"))          # newest first
        requested = []
        window.searchRequested.connect(requested.append)
        panel.queryChosen.emit("晴天")
        self.assertEqual(window.search.text(), "晴天")
        self.assertIn("晴天", requested)
        self.assertFalse(panel.isVisible())
        self.assertEqual(store["netease"][-1], "晴天")
        window.search.clear()                      # back to the catalog section
        window._variant_view.select_section("search")
        self.app.processEvents()
        self.assertTrue(panel.isVisible())
        panel.clearRequested.emit()
        self.assertFalse(panel.isVisible())
        self.assertEqual(store["netease"], [])


if __name__ == "__main__":
    unittest.main()
