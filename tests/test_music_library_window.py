from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QWheelEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from api.online_music import AccountSnapshot, OnlineSong
from local.music_library_window import LibraryCollection, MusicLibraryWindow


class MusicLibraryWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MusicLibraryWindow()
        self.account = AccountSnapshot("netease", True, "fixture-id", "布局测试账号", state="verified")
        self.window.set_account(self.account)
        self.catalog = (
            LibraryCollection("daily", "daily", "每日推荐", ""),
            LibraryCollection("fm", "fm", "私人漫游", ""),
            LibraryCollection("playlist-1", "playlist", "测试歌单", "", "1"),
        )
        self.window.set_collections(self.catalog)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def _finish_toolbar(self):
        self.window._toolbar.setCurrentTime(self.window.TOOLBAR_ANIMATION_MS)
        self.app.processEvents()

    def test_library_collection_is_immutable_and_contains_real_song(self):
        song = OnlineSong("netease", "2", "测试歌曲", "测试艺术家")
        item = LibraryCollection("song-2", "song", song.title, "", song=song)
        self.assertIs(item.song, song)
        with self.assertRaises(FrozenInstanceError):
            item.title = "changed"

    def test_initial_expanded_layout_has_inline_small_modes(self):
        row = self.window._rows[0]
        self.assertEqual(self.window._expanded, 1.0)
        self.assertTrue(self.window.switch_button.isVisible())
        self.assertTrue(self.window.logout_button.isVisible())
        self.assertEqual(row.mode_buttons["fullscreen"].height(), 35)
        self.assertGreater(row.mode_buttons["fullscreen"].x(), row._title_rect.right())

    def test_compact_moves_larger_modes_below_title_and_enlarges_cover(self):
        row = self.window._rows[0]
        old_cover = row.cover.width()
        self.window.set_toolbar_expanded(False)
        self._finish_toolbar()
        self.assertEqual(self.window._expanded, 0.0)
        self.assertFalse(self.window.switch_button.isVisible())
        self.assertEqual(row.mode_buttons["window"].height(), 54)
        self.assertGreater(row.mode_buttons["window"].y(), row._title_rect.bottom())
        self.assertGreater(row.cover.width(), old_cover)

    def test_toolbar_reversal_starts_from_current_geometry(self):
        self.window.set_toolbar_expanded(False)
        self.window._toolbar.setCurrentTime(180)
        middle = self.window._expanded
        self.window.set_toolbar_expanded(True)
        self.assertAlmostEqual(self.window._toolbar.startValue(), middle)
        self._finish_toolbar()
        self.assertEqual(self.window._expanded, 1.0)

    def test_wheel_down_compacts_and_wheel_up_expands(self):
        for delta, expanded in [(-120, False), (120, True)]:
            event = QWheelEvent(QPointF(200, 60), QPointF(200, 220), QPoint(), QPoint(0, delta),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.NoScrollPhase, False)
            self.app.sendEvent(self.window.scroll.viewport(), event)
            self._finish_toolbar()
            self.assertEqual(self.window._expanded, float(expanded))

    def test_hover_top_and_focus_search_restore_toolbar(self):
        self.window.set_toolbar_expanded(False)
        self._finish_toolbar()
        self.app.sendEvent(self.window, QMouseEvent(QEvent.Type.MouseMove, QPointF(330, 32),
                           QPointF(self.window.mapToGlobal(QPoint(330, 32))), Qt.MouseButton.NoButton,
                           Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        self._finish_toolbar()
        self.assertEqual(self.window._expanded, 1.0)
        self.window.set_toolbar_expanded(False)
        self._finish_toolbar()
        self.window.search.clearFocus()
        self.window.search.setFocus()
        self.app.processEvents()
        self._finish_toolbar()
        self.assertEqual(self.window._expanded, 1.0)

    def test_source_switch_and_logout_emit_without_mutating_account(self):
        switch = QSignalSpy(self.window.sourceSwitchRequested)
        logout = QSignalSpy(self.window.logoutRequested)
        self.window.switch_button.click()
        self.window.logout_button.click()
        self.assertEqual(switch.count(), 1)
        self.assertEqual(logout.count(), 1)
        self.assertEqual(self.window._account, self.account)

    def test_cover_default_and_three_modes_emit_global_artwork_rectangle(self):
        spy = QSignalSpy(self.window.collectionRequested)
        row = self.window._rows[0]
        for index, mode in enumerate(("default", "fullscreen", "window", "island")):
            (row.cover if mode == "default" else row.mode_buttons[mode]).click()
            self.assertEqual(spy.count(), index + 1)
            item, emitted_mode, rect = spy.at(index)
            self.assertEqual(item, self.catalog[0])
            self.assertEqual(emitted_mode, mode)
            self.assertTrue(rect.isValid())
            self.assertEqual(rect.topLeft(), row.cover.mapToGlobal(row.cover.artwork_rect().toAlignedRect().topLeft()))
            self.window.finish_open_transition()

    def test_opening_rejects_duplicate_selection_until_finished(self):
        spy = QSignalSpy(self.window.collectionRequested)
        self.window._rows[0].cover.click()
        self.window._rows[1].cover.click()
        self.assertEqual(spy.count(), 1)
        self.window.finish_open_transition()
        self.window._rows[1].cover.click()
        self.assertEqual(spy.count(), 2)

    def test_platform_search_emits_query_and_renders_supplied_song_and_playlist(self):
        spy = QSignalSpy(self.window.searchRequested)
        self.window.search.setText("  海  ")
        self.assertEqual(self.window.collections, ())
        self.window._submit_search()
        self.assertEqual(spy.at(0), ["海"])
        song = OnlineSong("netease", "2", "海", "测试歌手")
        results = (LibraryCollection("search-song", "song", song.title, "", song=song), self.catalog[2])
        self.window.set_search_results(results)
        self.assertEqual(self.window.collections, results)
        self.assertEqual(len(self.window._rows), 2)
        self.window.search.clear()
        self.assertEqual(spy.at(1), [""])
        self.assertEqual(self.window.collections, self.catalog)
        self.assertEqual(len(self.window._rows), 3)

    def test_search_debounce_restarts_and_return_cancels_pending_duplicate(self):
        spy = QSignalSpy(self.window.searchRequested)
        self.window.search.setText("a")
        self.window.search.setText("ab")
        self.assertTrue(self.window._search_timer.isActive())
        QTest.keyClick(self.window.search, Qt.Key.Key_Return)
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0), ["ab"])
        self.assertFalse(self.window._search_timer.isActive())

    def test_unknown_artwork_is_rejected_and_known_artwork_is_bounded(self):
        image = QImage(1200, 900, QImage.Format.Format_RGB32)
        image.fill(QColor("#346578"))
        self.window.set_artwork("unknown", image)
        self.assertNotIn("unknown", self.window._artwork)
        self.window.set_artwork("daily", image)
        self.assertLessEqual(self.window._artwork["daily"].width(), 256)
        self.assertFalse(self.window._rows[0].cover.image.isNull())
        self.window.set_artwork("account", image)
        self.assertIn("account", self.window._artwork)

    def test_account_change_clears_other_platform_catalog_query_and_artwork(self):
        image = QImage(30, 30, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        self.window.set_artwork("account", image)
        self.window.search.setText("old query")
        self.window.set_account(AccountSnapshot("qq", True, "other", "第二账号", state="verified"))
        self.assertEqual(self.window.search.text(), "")
        self.assertEqual(self.window.collections, ())
        self.assertEqual(len(self.window._rows), 0)
        self.assertEqual(len(self.window._artwork), 0)
        self.assertEqual(self.window.source_button.source.source_id, "qq")

    def test_close_releases_pending_timers_and_emits_once(self):
        spy = QSignalSpy(self.window.closed)
        self.window.search.setText("query")
        self.window.set_toolbar_expanded(False)
        self.window.close()
        self.assertEqual(spy.count(), 1)
        self.assertFalse(self.window._toolbar.state().value == 2)
        self.assertFalse(self.window._search_timer.isActive())

    def test_minimum_window_and_long_titles_keep_controls_inside(self):
        self.window.set_collections((LibraryCollection("long", "playlist", "很长的歌单名称" * 12, "", "2"),))
        self.window.resize(520, 360)
        for expanded in (False, True):
            self.window.set_toolbar_expanded(expanded)
            self._finish_toolbar()
            row = self.window._rows[0]
            for button in row.mode_buttons.values():
                self.assertTrue(row.rect().contains(button.geometry()))
            self.assertFalse(self.window.grab().isNull())

    def test_paginated_catalog_preserves_scroll_and_requests_once_near_bottom(self):
        catalog = tuple(LibraryCollection(str(i), "playlist", f"歌单{i}", "", str(i)) for i in range(12))
        self.window.set_collections(catalog)
        self.app.processEvents()
        bar = self.window.scroll.verticalScrollBar()
        bar.setValue(360)
        self.window.set_collections(catalog + (LibraryCollection("more", "playlist", "新歌单", "", "more"),))
        self.assertEqual(bar.value(), 360)
        spy = QSignalSpy(self.window.moreRequested)
        self.window.set_has_more(True)
        bar.setValue(bar.maximum())
        self.assertEqual(spy.count(), 1)
        bar.setValue(bar.maximum() - 10)
        self.assertEqual(spy.count(), 1)
        self.window.set_has_more(False)
        bar.setValue(bar.maximum())
        self.assertEqual(spy.count(), 1)

    def test_evicted_artwork_releases_row_reference_and_keeps_account(self):
        catalog = tuple(LibraryCollection(str(i), "playlist", f"歌单{i}", "", str(i)) for i in range(52))
        self.window.set_collections(catalog)
        image = QImage(64, 64, QImage.Format.Format_RGB32)
        image.fill(QColor("#187456"))
        self.window.set_artwork("account", image)
        for item in catalog:
            self.window.set_artwork(item.key, image)
        self.assertEqual(len(self.window._artwork), 48)
        self.assertIn("account", self.window._artwork)
        self.assertTrue(self.window._rows[0].cover.image.isNull())
        self.assertFalse(self.window._rows[-1].cover.image.isNull())

    def test_artwork_requests_only_visible_neighbours_and_deduplicates_until_result(self):
        catalog = tuple(LibraryCollection(str(i), "playlist", f"歌单{i}", f"https://example.invalid/{i}.jpg", str(i)) for i in range(20))
        spy = QSignalSpy(self.window.artworkNeeded)
        self.window.set_collections(catalog)
        visible_keys = {spy.at(i)[0] for i in range(spy.count())}
        self.assertIn("0", visible_keys)
        self.assertLess(len(visible_keys), 20)
        self.assertNotIn("19", visible_keys)
        count = spy.count()
        self.window._request_visible_artwork()
        self.assertEqual(spy.count(), count)
        image = QImage(64, 64, QImage.Format.Format_RGB32)
        image.fill(QColor("#124578"))
        self.window.set_artwork("0", image)
        self.window._request_visible_artwork()
        self.assertEqual(spy.count(), count)
        self.window.scroll.verticalScrollBar().setValue(self.window.scroll.verticalScrollBar().maximum())
        self.assertIn("19", {spy.at(i)[0] for i in range(spy.count())})

    def test_hidden_library_does_not_enqueue_artwork(self):
        self.window.hide()
        spy = QSignalSpy(self.window.artworkNeeded)
        self.window.set_collections((LibraryCollection("hidden", "playlist", "歌单", "https://example.invalid/hidden.jpg", "1"),))
        self.assertEqual(spy.count(), 0)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(spy.count(), 1)

    def test_reopen_can_retry_ignored_artwork_and_pending_search(self):
        spy = QSignalSpy(self.window.artworkNeeded)
        self.window.set_collections((LibraryCollection("cover", "playlist", "歌单", "https://example.invalid/cover.jpg", "1"),))
        self.assertEqual(spy.count(), 1)
        self.window.hide()
        self.window.show()
        self.app.processEvents()
        self.assertEqual(spy.count(), 2)
        self.window.search.setText("尚未提交的查询")
        self.window.hide()
        self.assertFalse(self.window._search_timer.isActive())
        self.window.show()
        self.app.processEvents()
        self.assertTrue(self.window._search_timer.isActive())


if __name__ == "__main__":
    unittest.main()
