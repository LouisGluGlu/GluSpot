from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QMouseEvent, QPalette, QWheelEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

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

    def _finish_sections(self):
        self.window._section_motion.setCurrentTime(self.window._section_motion.duration())
        self.app.processEvents()

    def _show_personal_rows(self):
        self.window._expand_group("personal")
        self._finish_sections()

    def _show_song_results(self, count=2):
        rows = tuple(LibraryCollection(f"song-{i}", "song", f"测试歌曲{i}", "",
            song=OnlineSong("netease", str(i), f"测试歌曲{i}", "测试歌手")) for i in range(count))
        self.window.search.setText("测试")
        self.window._search_timer.stop()
        self.window.set_search_results(rows)
        self._finish_sections()
        return rows

    def test_library_collection_is_immutable_and_contains_real_song(self):
        song = OnlineSong("netease", "2", "测试歌曲", "测试艺术家")
        item = LibraryCollection("song-2", "song", song.title, "", song=song)
        self.assertIs(item.song, song)
        with self.assertRaises(FrozenInstanceError):
            item.title = "changed"

    def test_disposing_library_preserves_shared_application_screen_wrapper(self):
        shared = QGuiApplication.primaryScreen()
        library = MusicLibraryWindow()
        library.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertTrue(isValid(shared))
        self.assertIs(QGuiApplication.primaryScreen(), shared)

    def test_initial_layout_has_folded_groups_and_single_visible_platform_action(self):
        self.assertEqual(self.window._expanded, 1.0)
        self.assertFalse(self.window.switch_button.isVisible())
        self.assertFalse(self.window.back_button.isVisible())
        self.assertTrue(self.window.source_button.isVisible())
        self.assertTrue(self.window.logout_button.isVisible())
        self.assertEqual([group.title for group in self.window._groups], ["为你精选", "个人歌单"])
        self.assertEqual(self.window._rows, [])

    def test_legacy_toolbar_fraction_never_changes_horizontal_collection_cards(self):
        self._show_personal_rows()
        shelf=self.window._shelves[0]
        cover=shelf.tiles[0][1]
        geometry=(shelf.geometry(),cover.geometry())
        self.window.set_toolbar_expanded(False);self._finish_toolbar()
        self.assertEqual(self.window._expanded,0.)
        self.assertEqual((shelf.geometry(),cover.geometry()),geometry)
        self.assertEqual(self.window._rows,[])

    def test_toolbar_reversal_starts_from_current_geometry(self):
        self.window.set_toolbar_expanded(False)
        self.window._toolbar.setCurrentTime(180)
        middle = self.window._expanded
        self.window.set_toolbar_expanded(True)
        self.assertAlmostEqual(self.window._toolbar.startValue(), middle)
        self._finish_toolbar()
        self.assertEqual(self.window._expanded, 1.0)

    def test_wheel_keeps_header_and_platform_controls_stationary(self):
        geometry = (self.window.search.geometry(), self.window.source_button.geometry())
        for delta in (-120, 120):
            event = QWheelEvent(QPointF(200, 60), QPointF(200, 220), QPoint(), QPoint(0, delta),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.NoScrollPhase, False)
            self.app.sendEvent(self.window.scroll.viewport(), event)
            self._finish_toolbar()
            self.assertEqual(self.window._expanded, 1.0)
            self.assertEqual((self.window.search.geometry(), self.window.source_button.geometry()), geometry)

    def test_top_hover_keeps_layout_stationary_and_search_focus_still_works(self):
        self.window.set_toolbar_expanded(False)
        self._finish_toolbar()
        self.app.sendEvent(self.window, QMouseEvent(QEvent.Type.MouseMove, QPointF(330, 32),
                           QPointF(self.window.mapToGlobal(QPoint(330, 32))), Qt.MouseButton.NoButton,
                           Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        self._finish_toolbar()
        self.assertEqual(self.window._expanded, 0.0)
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

    def test_collection_cover_explains_first_song_and_song_cover_remembers_previous_surface(self):
        self._show_personal_rows()
        cover=self.window._shelves[0].tiles[0][1]
        self.assertIn("播放",cover.accessibleName())
        self.assertIn("第一首",cover.accessibleDescription())
        self._show_song_results()
        song_row=self.window._rows[0]
        self.assertIn("播放",song_row.cover.toolTip())
        self.assertIn("上次结束播放的界面",song_row.cover.accessibleDescription())
        self.assertGreaterEqual(song_row.queue_button.height(),44)

    def test_search_placeholder_names_current_provider_without_changing_query_contract(self):
        self.assertIn("网易云音乐", self.window.search.placeholderText())
        self.window.set_account(AccountSnapshot("qq", True, "qq-account", "另一个账号", state="verified"))
        self.assertIn("QQ 音乐", self.window.search.placeholderText())
        self.assertNotIn("网易云音乐", self.window.search.placeholderText())

    def test_glass_canvas_is_bounded_and_frozen_across_toolbar_and_catalog_changes(self):
        frame = self.window._background_frame
        self.assertFalse(frame.isNull())
        self.assertLessEqual(max(frame.width(), frame.height()), 256)
        key = frame.cacheKey()
        self.window._set_expanded(.5)
        self.window.set_collections(self.catalog + (LibraryCollection("extra", "playlist", "新歌单", "", "3"),))
        self.assertEqual(self.window._background_frame.cacheKey(), key)
        self.window.set_account(AccountSnapshot("qq", True, "qq-account", "另一个账号", state="verified"))
        self.assertNotEqual(self.window._background_frame.cacheKey(), key)

    def test_clear_search_returns_catalog_once_and_preserves_account(self):
        self.window.search.setText("海")
        self.window.search.settle()
        self.window._search_timer.stop()
        self.window.set_search_results((self.catalog[2],))
        spy = QSignalSpy(self.window.searchRequested)
        back = QSignalSpy(self.window.backRequested)
        # Clearing is the existing controller-owned Back operation. A
        # standalone window fixture supplies only that existing clear slot.
        self.window.backRequested.connect(self.window.search.clear)
        clear = self.window.clear_search_button
        self.assertTrue(clear.isVisible())
        self.assertFalse(clear.geometry().intersects(self.window.search_button.geometry()))
        self.assertGreaterEqual(clear.width(), 44)
        clear.click()
        self.assertEqual(back.count(), 1)
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0), [""])
        self.assertEqual(self.window.collections, self.catalog)
        self.assertEqual(self.window._account, self.account)
        self.assertFalse(clear.isVisible())
        self.assertFalse(self.window._search_timer.isActive())
        self.assertTrue(self.window.search.hasFocus())

    def test_clear_search_delegates_cancellation_without_mutating_standalone_query(self):
        self.window.search.setText("由控制器处理")
        self.window._search_timer.stop()
        back = QSignalSpy(self.window.backRequested)
        self.window.clear_search_button.click()
        self.assertEqual(back.count(), 1)
        self.assertEqual(self.window.search.text(), "由控制器处理")

    def test_find_shortcut_focuses_and_selects_existing_query_without_submitting(self):
        self.window.search.setText("待修改搜索")
        self.window._search_timer.stop()
        self.window.set_search_results((self.catalog[2],))
        self.window._rows[0].cover.setFocus()
        self.window.activateWindow()
        self.app.processEvents()
        spy = QSignalSpy(self.window.searchRequested)
        QTest.keyClick(self.window._rows[0].cover, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        self.app.processEvents()
        self.assertTrue(self.window.search.hasFocus())
        self.assertEqual(self.window.search.selectedText(), "待修改搜索")
        self.assertEqual(spy.count(), 0)

    def test_disabled_search_blocks_clear_and_find_without_modifying_query(self):
        self.window.search.setText("保持查询")
        self.window._search_timer.stop()
        self.window.search.setEnabled(False)
        spy = QSignalSpy(self.window.searchRequested)
        self.window.clear_search_button.click()
        self.window._focus_empty_search()
        self.assertEqual(self.window.search.text(), "保持查询")
        self.assertEqual(spy.count(), 0)

    def test_search_error_recovery_reuses_explicit_search_and_catalog_error_switches_source(self):
        self.window.search.setText("待重试歌曲")
        self.window._search_timer.stop()
        self.window.set_search_results(())
        self.window.set_error("搜索暂时失败")
        search = QSignalSpy(self.window.searchRequested)
        switch = QSignalSpy(self.window.sourceSwitchRequested)
        self.assertTrue(self.window.recovery_action.isVisible())
        self.assertEqual(self.window.recovery_action.text(), "重新搜索")
        self.window.recovery_action.click()
        self.assertEqual(search.count(), 1)
        self.assertEqual(search.at(0), ["待重试歌曲"])
        self.assertEqual(switch.count(), 0)
        self.window.search.clear()
        self.window.set_collections(())
        self.window.set_error("歌单暂时无法加载")
        self.assertEqual(self.window.recovery_action.text(), "切换平台")
        self.window.recovery_action.click()
        self.assertEqual(switch.count(), 1)
        self.assertEqual(self.window._account, self.account)

    def test_empty_error_recovery_fits_short_window_and_stays_out_of_other_states(self):
        self.window.set_collections(())
        self.window.resize(520, 360)
        self.window.set_error("歌单暂时无法加载")
        self.assertTrue(self.window.rect().contains(self.window.recovery_action.geometry()))
        self.assertGreater(self.window.recovery_action.y(), self.window._empty_title_rect.bottom())
        self.assertGreaterEqual(self.window.recovery_action.height(), 44)
        self.window.set_loading(True, "正在加载…")
        self.assertFalse(self.window.recovery_action.isVisible())
        self.window.set_loading(False)
        self.assertFalse(self.window.recovery_action.isVisible())
        self.window.set_collections(self.catalog)
        self.window.set_error("单次播放失败")
        self.assertFalse(self.window.recovery_action.isVisible())

    def test_back_button_and_escape_emit_navigation_without_close_or_account_mutation(self):
        back = QSignalSpy(self.window.backRequested)
        closed = QSignalSpy(self.window.closed)
        self.window.back_button.click()
        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.window.search.setText("待返回的搜索")
        self.assertFalse(self.window.back_button.isVisible())
        QTest.keyClick(self.window.search, Qt.Key.Key_Escape)
        self.assertEqual(back.count(), 3)
        self.assertEqual(closed.count(), 0)
        self.assertEqual(self.window._account, self.account)
        self.assertEqual(self.window.search.text(), "待返回的搜索")
        self.window.search.clear()
        self.assertEqual(self.window.back_button.toolTip(), "返回音乐来源")

    def test_header_search_and_navigation_remain_usable_in_both_sizes(self):
        for width, height in ((760, 720), (520, 360)):
            self.window.resize(width, height)
            for expanded in (False, True):
                self.window.set_toolbar_expanded(expanded)
                self._finish_toolbar()
                controls = [self.window.close_button, self.window.search, self.window.search_button,
                    self.window.source_button, self.window.logout_button, self.window.expand_button]
                for control in controls:
                    self.assertTrue(control.isVisible())
                    self.assertGreaterEqual(control.height(), 44)
                    self.assertGreaterEqual(control.width(), 44)
                    self.assertTrue(control.parentWidget().rect().contains(control.geometry()))
                self.assertFalse(self.window.source_button.geometry().intersects(self.window.expand_button.geometry()))
                self.assertFalse(self.window.back_button.isVisible())
                self.assertFalse(self.window.switch_button.isVisible())

    def test_narrow_account_header_preserves_identity_and_all_existing_actions(self):
        nickname = "完整昵称不应被三个按钮挤压"
        self.window.set_account(AccountSnapshot("netease", True, "fixture-id", nickname, state="verified"))
        for width in (520, 639, 640, 760):
            self.window.resize(width, 500)
            for step in range(11):
                self.window._set_expanded(step / 10)
                name = self.window.account_name_label
                self.assertTrue(self.window.rect().contains(name.geometry()))
                for button in (self.window.source_button, self.window.switch_button, self.window.logout_button):
                    if button.isVisible():
                        self.assertFalse(button.geometry().intersects(name.geometry()))
                        self.assertFalse(button.geometry().intersects(self.window.close_button.geometry()))
                        self.assertFalse(self.window._header_title_rect.intersects(button.geometry()))
                if step == 10:
                    self.assertEqual(name.text(), name.fontMetrics().elidedText(nickname,
                        Qt.TextElideMode.ElideRight, name.width()))
                    self.assertEqual(name.accessibleName(), nickname)
        self.window.resize(520, 500)
        self.assertGreaterEqual(self.window.account_name_label.width(), 260)
        source = QSignalSpy(self.window.sourceSwitchRequested)
        logout = QSignalSpy(self.window.logoutRequested)
        self.window.source_button.click()
        self.window.switch_button.click()
        self.window.logout_button.click()
        self.assertEqual(source.count(), 2)
        self.assertEqual(logout.count(), 1)

    def test_elided_nickname_retains_full_plain_text_tooltip_and_accessible_name(self):
        nickname = "<完整账号昵称>" * 16
        self.window.set_account(AccountSnapshot("qq", True, "other", nickname, state="verified"))
        self.window.resize(520, 500)
        label = self.window.account_name_label
        self.assertNotEqual(label.text(), nickname)
        self.assertIn("&lt;完整账号昵称&gt;" * 16, label.toolTip())
        self.assertEqual(label.accessibleName(), nickname)
        self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(label.accessibleDescription(), self.window.source_button.source.name)

    def test_search_placeholder_has_explicit_opaque_readable_color(self):
        self.window.search.set_expanded(True)
        self.window.search.settle()
        placeholder = self.window.search.palette().color(QPalette.ColorRole.PlaceholderText)
        self.assertEqual(placeholder.name(), "#b4bac7")
        self.assertEqual(placeholder.alpha(), 255)

    def test_empty_catalog_action_focuses_existing_search_without_submitting_or_switching(self):
        self.window.set_collections(())
        search = QSignalSpy(self.window.searchRequested)
        switch = QSignalSpy(self.window.sourceSwitchRequested)
        for height in (360, 720):
            self.window.resize(520, height)
            self.app.processEvents()
            action = self.window.empty_action
            self.assertTrue(action.isVisible())
            self.assertEqual(action.text(), "搜索歌曲")
            self.assertTrue(self.window.rect().contains(action.geometry()))
            self.assertGreaterEqual(action.height(), 44)
            self.assertGreater(action.y(), self.window._empty_title_rect.bottom())
        self.window.empty_action.setFocus()
        QTest.keyClick(self.window.empty_action, Qt.Key.Key_Space)
        self.assertTrue(self.window.search.hasFocus())
        self.assertEqual(search.count(), 0)
        self.assertEqual(switch.count(), 0)

    def test_empty_search_action_selects_query_and_waits_for_explicit_edit(self):
        self.window.search.setText("未找到的歌曲")
        self.window._search_timer.stop()
        self.window.set_search_results(())
        search = QSignalSpy(self.window.searchRequested)
        self.assertTrue(self.window.empty_action.isVisible())
        self.assertEqual(self.window.empty_action.text(), "修改搜索词")
        self.window.empty_action.click()
        self.assertEqual(self.window.search.selectedText(), "未找到的歌曲")
        self.assertEqual(self.window.search.text(), "未找到的歌曲")
        self.assertEqual(search.count(), 0)

    def test_empty_action_stays_out_of_loading_error_and_disabled_search_states(self):
        self.window.set_collections(())
        self.assertTrue(self.window.empty_action.isVisible())
        self.window.set_loading(True, "正在加载歌单…")
        self.assertFalse(self.window.empty_action.isVisible())
        self.window.set_error("歌单加载失败")
        self.assertFalse(self.window.empty_action.isVisible())
        self.window.set_loading(True)
        self.window.set_loading(False)
        self.assertTrue(self.window.empty_action.isVisible())
        self.window.search.setEnabled(False)
        self.assertFalse(self.window.empty_action.isVisible())
        self.window.search.setEnabled(True)
        self.assertTrue(self.window.empty_action.isVisible())
        self.window.set_collections(self.catalog)
        self.assertFalse(self.window.empty_action.isVisible())

    def test_long_text_and_song_next_action_never_overlap_during_toolbar_motion(self):
        song = OnlineSong("netease", "long", "很长的歌曲名称" * 12, "很长的歌手名字" * 12)
        self.window.search.setText("很长")
        self.window._search_timer.stop()
        self.window.set_search_results((LibraryCollection("long", "song", song.title, "", song=song),))
        self._finish_sections()
        for width in (520, 760):
            self.window.resize(width, 500)
            for step in range(11):
                self.window._set_expanded(step / 10)
                row = self.window._rows[0]
                self.assertFalse(row._group_rect.intersects(row._title_rect))
                self.assertFalse(row._group_rect.intersects(row._subtitle_rect))
                self.assertTrue(row.rect().contains(row.cover.geometry()))

    def test_error_status_has_its_own_space_below_scrolling_content(self):
        self.window.set_error("布局测试 · 播放失败")
        self.assertLess(self.window.scroll.geometry().bottom(), self.window.height() - 36)
        self.assertEqual(self.window._error, "布局测试 · 播放失败")
        self.assertFalse(self.window.grab().isNull())

    def test_collection_cover_emits_default_surface_and_global_artwork_rectangle_directly(self):
        spy=QSignalSpy(self.window.collectionRequested)
        browse=QSignalSpy(self.window.collectionBrowseRequested)
        self.window._expand_group("for_you");self._finish_sections()
        collection,cover,_=self.window._shelves[0].tiles[0]
        cover.click()
        self.assertEqual(spy.count(),1)
        self.assertEqual(browse.count(),0)
        item,mode,rect=spy.at(0)
        self.assertEqual(item,collection)
        self.assertEqual(mode,"default")
        self.assertTrue(rect.isValid())
        self.assertEqual(rect.topLeft(),cover.mapToGlobal(cover.artwork_rect().toAlignedRect().topLeft()))

    def test_opening_rejects_duplicate_selection_until_finished(self):
        self._show_song_results()
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
        self.assertEqual(self.window._rows, [])
        self.assertEqual(len(self.window._groups), 2)

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
        self.assertFalse(self.window._groups[0].images["daily"].isNull())
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

    def test_minimum_window_and_long_titles_keep_collection_cards_inside(self):
        self.window.set_collections((LibraryCollection("long","playlist","很长的歌单名称"*12,"","2"),))
        self._show_personal_rows();self.window.resize(520,360)
        for expanded in (False,True):
            self.window.set_toolbar_expanded(expanded);self._finish_toolbar()
            shelf=self.window._shelves[0];cover=shelf.tiles[0][1];label=shelf.labels[0]
            self.assertTrue(shelf.body.rect().contains(cover.geometry()))
            self.assertTrue(shelf.body.rect().contains(label.geometry()))
            self.assertLessEqual(len(label.text()),len(label.toolTip()))
            self.assertFalse(self.window.grab().isNull())

    def test_paginated_catalog_preserves_horizontal_scroll_and_requests_once_explicitly(self):
        catalog=tuple(LibraryCollection(str(i),"playlist",f"歌单{i}","",str(i)) for i in range(12))
        self.window.set_collections(catalog);self._show_personal_rows();self.app.processEvents()
        self.window._shelves[0].scroll.horizontalScrollBar().setValue(360)
        self.window.set_collections(catalog+(LibraryCollection("more","playlist","新歌单","","more"),))
        self._finish_sections()
        self.assertEqual(self.window._shelves[0].scroll.horizontalScrollBar().value(),360)
        spy=QSignalSpy(self.window.moreRequested)
        self.window.set_has_more(True);self.window.more_button.click()
        self.assertEqual(spy.count(),1)
        self.window.more_button.click();self.assertEqual(spy.count(),1)
        self.window.set_has_more(False);self.window.more_button.click();self.assertEqual(spy.count(),1)

    def test_evicted_artwork_releases_row_reference_and_keeps_account(self):
        self.window.ARTWORK_MAX_ENTRIES = 48
        catalog = tuple(LibraryCollection(str(i), "playlist", f"歌单{i}", "", str(i)) for i in range(52))
        self.window.set_collections(catalog)
        self._show_personal_rows()
        image = QImage(64, 64, QImage.Format.Format_RGB32)
        image.fill(QColor("#187456"))
        self.window.set_artwork("account", image)
        for item in catalog:
            self.window.set_artwork(item.key, image)
        self.assertEqual(len(self.window._artwork), 48)
        self.assertIn("account", self.window._artwork)
        self.assertTrue(self.window._shelves[0].tiles[0][1].image.isNull())
        self.assertFalse(self.window._shelves[0].tiles[-1][1].image.isNull())

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
        self._show_personal_rows()
        self.window._shelves[0].scroll.horizontalScrollBar().setValue(self.window._shelves[0].scroll.horizontalScrollBar().maximum())
        self.assertIn("19", {spy.at(i)[0] for i in range(spy.count())})

    def test_hidden_library_does_not_enqueue_artwork(self):
        self.window.hide()
        spy = QSignalSpy(self.window.artworkNeeded)
        self.window.set_collections((LibraryCollection("hidden", "playlist", "歌单", "https://example.invalid/hidden.jpg", "1"),))
        self.assertEqual(spy.count(), 0)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(spy.count(), 1)

    def test_removed_search_artwork_completion_allows_request_when_song_returns(self):
        song = OnlineSong("netease", "search-first", "第一首搜索歌曲", "测试歌手")
        first = LibraryCollection("search-first", "song", song.title,
                                  "https://example.invalid/search-first.jpg", song=song)
        spy = QSignalSpy(self.window.artworkNeeded)
        self.window.search.setText("第一首")
        self.window._search_timer.stop()
        self.window.set_search_results((first,))
        self.assertEqual(spy.count(), 1)
        self.assertIn(first.key, self.window._artwork_pending)

        self.window.search.setText("第二首")
        self.window._search_timer.stop()
        self.window.set_search_results(())
        image = QImage(64, 64, QImage.Format.Format_RGB32)
        image.fill(QColor("#125578"))
        self.window.set_artwork(first.key, image)
        self.assertNotIn(first.key, self.window._artwork)
        self.assertNotIn(first.key, self.window._artwork_pending)

        self.window.search.setText("第一首")
        self.window._search_timer.stop()
        self.window.set_search_results((first,))
        self.assertEqual(spy.count(), 2)
        self.assertEqual(spy.at(1), [first.key, first.artwork_url])
        self.window.set_artwork(first.key, image)
        self.assertFalse(self.window._rows[0].cover.image.isNull())
        self.assertNotIn(first.key, self.window._artwork_pending)

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
