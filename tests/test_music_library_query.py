"""Search matching, local preview, history, keyboard and failure copy."""
from __future__ import annotations

import tests.isolate_appdata  # noqa: F401  # before product imports capture %APPDATA%

import logging
import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from api.online_music import CatalogPage, OnlinePlaylist, OnlineSong
from local.music_library_query import SearchHistory, local_collection_matches, match_spans, paint_highlighted_text
from local.music_library_window import LibraryCollection, MusicLibraryWindow
from tests.test_music_library_controller import MusicLibraryControllerTests, run_search


class MatchTests(unittest.TestCase):
    def test_spans_are_case_insensitive_and_do_not_match_empty_query(self):
        self.assertEqual(match_spans("海阔天空", "阔"), ((1, 2),))
        self.assertEqual(match_spans("AbC Ab", "ab"), ((0, 2), (4, 6)))
        self.assertEqual(match_spans("海阔天空", ""), ())
        self.assertEqual(match_spans("海阔天空", "   "), ())
        self.assertEqual(match_spans("", "海"), ())

    def test_local_matches_prefer_prefix_and_keep_the_original_row(self):
        first = LibraryCollection("a", "playlist", "在海阔边", "", "a")
        second = LibraryCollection("b", "playlist", "海阔天空", "", "b")
        other = LibraryCollection("c", "playlist", "光辉岁月", "", "c")
        ranked = local_collection_matches((first, other, second), "海阔")
        self.assertEqual(tuple(row.key for row in ranked), ("b", "a"))
        self.assertIs(ranked[0], second)

    def test_history_moves_duplicates_and_stops_at_the_limit(self):
        history = SearchHistory(limit=2)
        history.remember("  一  ")
        history.remember("二")
        history.remember("一")
        history.remember("三")
        self.assertEqual(history.items, ("一", "三"))
        history.remember("   ")
        self.assertEqual(history.items, ("一", "三"))


class HighlightPaintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_matched_span_uses_the_accent_and_plain_text_does_not(self):
        def paint(query):
            image = QImage(180, 40, QImage.Format.Format_RGB32)
            image.fill(QColor("#101218"))
            painter = QPainter(image)
            painter.setPen(QColor("#f8f8f8"))
            paint_highlighted_text(painter, image.rect(), "海阔天空", query, QColor("#f8f8f8"), QColor("#ffe7a3"))
            painter.end()
            return image

        plain = paint("")
        marked = paint("阔")
        self.assertNotEqual(plain, marked)
        accent = QColor("#ffe7a3")
        found = False
        for x in range(marked.width()):
            for y in range(marked.height()):
                color = marked.pixelColor(x, y)
                if abs(color.red() - accent.red()) < 30 and color.green() > 180 and color.blue() < 190:
                    found = True
                    break
            if found:
                break
        self.assertTrue(found, "matched text did not pick up the accent")


class SearchWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MusicLibraryWindow()
        self.window.set_library_variant("classic")
        self.window.set_collections((
            LibraryCollection("keep", "playlist", "海阔天空", "", "1"),
            LibraryCollection("other", "playlist", "光辉岁月", "", "2"),
        ))
        self.window.resize(960, 640)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_typing_shows_local_rows_before_the_timer_submits(self):
        spy = QSignalSpy(self.window.searchRequested)
        self.window.search.setText("海阔")
        self.assertTrue(self.window._search_timer.isActive())
        self.assertEqual(spy.count(), 0)
        self.assertEqual([row.title for row in self.window.collections], ["海阔天空"])
        self.assertEqual(self.window._search_status_message(), "")
        self.assertFalse(self.window.empty_action.isVisible())

    def test_same_local_rows_are_reused_and_a_miss_says_searching(self):
        self.window.search.setText("海阔")
        row = self.window._rows[0]
        self.window.search.setText("海阔天空")
        self.assertIs(self.window._rows[0], row)
        self.window.search.setText("没有这首")
        self.assertEqual(self.window.collections, ())
        self.assertEqual(self.window._search_status_message(), "正在搜索当前平台…")
        self.assertFalse(self.window.empty_action.isVisible())
        self.window._search_timer.stop()
        self.window.set_search_results(())
        self.assertTrue(self.window.empty_action.isVisible())
        self.assertEqual(self.window.empty_action.text(), "修改搜索词")
        self.assertIn("没有这首", self.window.empty_action.accessibleDescription())

    def test_arrows_move_through_results_and_enter_plays_without_a_second_search(self):
        song = OnlineSong("netease", "1", "海阔天空", "Beyond")
        other = OnlineSong("netease", "2", "光辉岁月", "Beyond")
        rows = (
            LibraryCollection("s1", "song", song.title, "", song=song),
            LibraryCollection("s2", "song", other.title, "", song=other),
        )
        self.window.search.setText("Beyond")
        self.window._search_timer.stop()
        self.window.set_search_results(rows)
        search = QSignalSpy(self.window.searchRequested)
        chosen = QSignalSpy(self.window.collectionRequested)
        QTest.keyClick(self.window.search, Qt.Key.Key_Down)
        self.assertTrue(self.window._rows[0].hasFocus())
        QTest.keyClick(self.window._rows[0], Qt.Key.Key_Down)
        self.assertTrue(self.window._rows[1].hasFocus())
        QTest.keyClick(self.window._rows[1], Qt.Key.Key_Up)
        self.assertTrue(self.window._rows[0].hasFocus())
        QTest.keyClick(self.window._rows[0], Qt.Key.Key_Return)
        self.assertEqual(chosen.count(), 1)
        self.assertEqual(chosen.at(0)[0].key, "s1")
        self.assertEqual(search.count(), 0)

    def test_up_and_down_walk_submitted_history_without_logging_the_text(self):
        messages = []
        handler = logging.Handler()
        handler.emit = lambda record: messages.append(record.getMessage())
        logger = logging.getLogger("GluSpot.search")
        logger.addHandler(handler)
        try:
            self.window.search.setText("第一首")
            self.window._submit_search()
            self.window.search.setText("第二首")
            self.window._submit_search()
            self.window.search.setText("正在输入")
            QTest.keyClick(self.window.search, Qt.Key.Key_Up)
            self.assertEqual(self.window.search.text(), "第二首")
            QTest.keyClick(self.window.search, Qt.Key.Key_Up)
            self.assertEqual(self.window.search.text(), "第一首")
            QTest.keyClick(self.window.search, Qt.Key.Key_Down)
            self.assertEqual(self.window.search.text(), "第二首")
        finally:
            logger.removeHandler(handler)
        self.assertTrue(messages)
        self.assertNotIn("第一首", " ".join(messages))
        self.assertNotIn("第二首", " ".join(messages))


class SearchControllerTests(MusicLibraryControllerTests):
    def test_local_catalog_stays_visible_until_network_results_join_it(self):
        playlist = OnlinePlaylist("netease", "list-1", "query live")
        self.service.pages["netease", 0] = CatalogPage((playlist,), 50, False)
        self.open()
        self.service.search_pages["netease", "query", "songs", 0] = CatalogPage((self.song,), 1, False)
        self.window.search.setText("query")
        self.assertEqual([row.title for row in self.window.collections], ["query live"])
        self.assertEqual(self.controller._search_results, ())
        self.window._submit_search()
        self.assertEqual([row.title for row in self.window.collections], ["query live"])
        run_search(self.tasks)
        self.assertEqual([row.title for row in self.window.collections], ["synthetic song", "query live"])

    def test_queue_song_is_an_instant_local_result(self):
        self.open()
        self.player.queue = (OnlineSong("netease", "queued", "query track", "Singer"),)
        self.window.search.setText("query track")
        self.assertEqual(self.window.collections[0].song.song_id, "queued")
        self.assertEqual(self.controller._search_results, ())

    def test_editing_discards_a_queued_search_request(self):
        def discard(predicate):
            kept = [job for job in self.tasks.jobs if not predicate(job[0])]
            removed = len(self.tasks.jobs) - len(kept)
            self.tasks.jobs[:] = kept
            return removed

        self.tasks.discard = discard
        self.open()
        self.search("one")
        self.assertTrue(any(job[0][0] == "search" for job in self.tasks.jobs))
        self.window.search.setText("two")
        self.assertFalse(any(job[0][0] == "search" for job in self.tasks.jobs))

    def test_total_failure_without_local_rows_has_a_retry_message(self):
        self.open()
        self.service.search_pages["netease", "zzzz", "songs", 0] = RuntimeError("down")
        self.service.search_pages["netease", "zzzz", "playlists", 0] = RuntimeError("down")
        self.search("zzzz")
        run_search(self.tasks)
        self.assertEqual(self.window._error, "搜索没有完成，请检查网络后重试")
        self.assertFalse(self.window._catalog_skeleton.isVisible())
        self.assertTrue(self.window.recovery_action.isVisible())
        self.assertEqual(self.window.recovery_action.text(), "重新搜索")


if __name__ == "__main__":
    unittest.main()
