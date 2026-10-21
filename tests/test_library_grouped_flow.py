from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from api.online_music import AccountSnapshot, OnlineSong
from local.music_library_window import LibraryCollection, MusicLibraryWindow


class GroupedLibraryFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MusicLibraryWindow()
        self.window.set_account(AccountSnapshot('netease', True, 'fixture', '测试账号'))
        self.catalog = (
            LibraryCollection('fm', 'fm', '私人漫游', '', group='for_you'),
            LibraryCollection('rec', 'playlist', '推荐歌单', '', '1', group='recommended'),
            LibraryCollection('own', 'playlist', '个人歌单', '', '2'),
        )
        self.window.set_collections(self.catalog)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def songs(self, parent):
        return tuple(LibraryCollection(f'{parent.key}/song:{i}', 'song', f'歌曲{i}', '',
            song=OnlineSong('netease', str(i), f'歌曲{i}', '歌手')) for i in range(10))

    def settle(self):
        self.window._section_motion.setCurrentTime(self.window._section_motion.duration())
        self.app.processEvents()

    def test_folded_groups_expand_without_starting_playback(self):
        played=QSignalSpy(self.window.collectionRequested);browsed=QSignalSpy(self.window.collectionBrowseRequested)
        self.assertEqual(len(self.window._groups),3)
        self.window._groups[1].click();self.settle()
        self.assertEqual(played.count(),0);self.assertEqual(browsed.count(),0)
        shelf=self.window._shelves[0]
        self.assertEqual(shelf.items,(self.catalog[1],))
        shelf.tiles[0][1].click()
        self.assertEqual(played.at(0)[:2],[self.catalog[1],'default'])
        self.assertEqual(browsed.count(),0)

    def test_search_song_click_plays_default_and_queue_is_separate(self):
        songs=self.songs(self.catalog[1])
        self.window.search.setText('搜索');self.window._search_timer.stop();self.window.set_search_results(songs)
        played,queued=QSignalSpy(self.window.collectionRequested),QSignalSpy(self.window.queueNextRequested)
        self.window._rows[2].queue_button.click()
        self.assertEqual(queued.at(0),[songs[2]]);self.assertEqual(played.count(),0)
        self.window._rows[2].cover.click()
        item,mode,rect=played.at(0)
        self.assertEqual((item,mode),(songs[2],'default'));self.assertTrue(rect.isValid())
        self.window._rows[3].cover.click();self.assertEqual(played.count(),1)

    def test_all_expand_fetches_no_songs_and_collapsing_restores_groups(self):
        browse=QSignalSpy(self.window.collectionBrowseRequested);changed=QSignalSpy(self.window.libraryExpandChanged)
        self.window.expand_button.click();self.settle()
        self.assertEqual(browse.count(),0);self.assertEqual(len(self.window._shelves),3)
        self.assertEqual(sum(len(shelf.items) for shelf in self.window._shelves),len(self.catalog))
        self.window.expand_button.click();self.settle()
        self.assertEqual(changed.count(),2);self.assertEqual(len(self.window._groups),3)
        self.assertEqual(self.window._shelves,[])

    def test_legacy_browse_reply_cannot_rebuild_a_collection_shelf(self):
        self.window.set_library_expanded(True)
        original=self.window._shelves[0]
        with patch.object(self.window,'_rebuild_rows',wraps=self.window._rebuild_rows) as rebuild:
            self.window.set_playlist_tracks(self.catalog[1],self.songs(self.catalog[1]))
            rebuild.assert_not_called()
        self.assertIs(self.window._shelves[0],original)
        self.assertNotIn(self.songs(self.catalog[1])[0],self.window.collections)

    def test_selecting_second_group_replaces_top_and_keeps_folded_peers_below(self):
        self.window._expand_group('recommended');self.settle()
        self.window._expand_group('personal');self.settle()
        self.assertEqual([s.group_key for s in self.window._shelves],['personal'])
        self.assertEqual({g.group_key for g in self.window._groups},{'recommended','for_you'})
        self.assertEqual(len({g.y() for g in self.window._groups}),1)
        self.assertTrue(all(g.y()>self.window._shelves[0].y() for g in self.window._groups))

    def test_other_group_click_only_expands_and_never_starts_playback(self):
        self.window._expand_group('recommended');self.settle()
        played=QSignalSpy(self.window.collectionRequested)
        next(group for group in self.window._groups if group.title=='个人歌单').click();self.settle()
        self.assertEqual(played.count(),0)
        self.assertEqual(self.window._shelves[0].items,(self.catalog[2],))
        self.window._shelves[0].tiles[0][1].click();self.assertEqual(played.count(),1)

    def test_horizontal_wheel_moves_collections_without_moving_title_or_vertical_page(self):
        more=tuple(LibraryCollection(f'rec-{i}','playlist',f'推荐{i}','','id-'+str(i),group='recommended') for i in range(10))
        self.window.set_collections(self.catalog+more);self.window._expand_group('recommended');self.settle()
        shelf=self.window._shelves[0];title_pos=shelf.pos();vertical=self.window.scroll.verticalScrollBar().value()
        for target in (shelf.scroll.viewport(),shelf.body,shelf.tiles[0][1],shelf.labels[0]):
            before=shelf.scroll.horizontalScrollBar().value()
            event=QWheelEvent(QPointF(20,20),QPointF(100,260),QPoint(0,-35),QPoint(),Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.ShiftModifier,Qt.ScrollPhase.NoScrollPhase,False)
            self.app.sendEvent(target,event);self.assertGreater(shelf.scroll.horizontalScrollBar().value(),before)
        self.assertEqual(shelf.pos(),title_pos)
        self.assertEqual(self.window.scroll.verticalScrollBar().value(),vertical)

    def test_horizontal_trackpad_wheel_moves_collection_strip_without_shift(self):
        more=tuple(LibraryCollection(f'rec-{i}','playlist',f'推荐{i}','','id-'+str(i),group='recommended') for i in range(10))
        self.window.set_collections(self.catalog+more);self.window._expand_group('recommended');self.settle()
        shelf=self.window._shelves[0]
        before=shelf.scroll.horizontalScrollBar().value()
        event=QWheelEvent(QPointF(20,20),QPointF(100,260),QPoint(-35,0),QPoint(),Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
        self.app.sendEvent(shelf.scroll.viewport(),event)
        self.assertGreater(shelf.scroll.horizontalScrollBar().value(),before)

    def test_unmodified_vertical_wheel_from_shelf_children_scrolls_library_page(self):
        more=tuple(LibraryCollection(f'rec-{i}','playlist',f'推荐{i}','','id-'+str(i),group='recommended') for i in range(10))
        self.window.resize(520,300)
        self.window.set_collections(self.catalog+more);self.window._expand_group('recommended');self.settle()
        shelf=self.window._shelves[0]
        page_bar=self.window.scroll.verticalScrollBar()
        self.assertGreater(page_bar.maximum(),0)
        targets=(shelf.scroll.viewport(),shelf.body,shelf.tiles[0][1],shelf.labels[0])
        deltas=((QPoint(0,-35),QPoint()),(QPoint(),QPoint(0,-120)))
        for target in targets:
            for pixels,angles in deltas:
                page_bar.setValue(0)
                horizontal_before=shelf.scroll.horizontalScrollBar().value()
                position=QPointF(target.rect().center())
                event=QWheelEvent(position,QPointF(target.mapToGlobal(position.toPoint())),pixels,angles,
                    Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
                self.app.sendEvent(target,event)
                self.assertGreater(page_bar.value(),0,(type(target).__name__,pixels,angles))
                self.assertEqual(shelf.scroll.horizontalScrollBar().value(),horizontal_before)

    def test_only_one_visible_platform_action_and_compact_targets_stay_contained(self):
        self.window.resize(520,360)
        self.assertFalse(self.window.back_button.isVisible());self.assertFalse(self.window.switch_button.isVisible())
        switch=QSignalSpy(self.window.sourceSwitchRequested);self.window.source_button.click()
        self.assertEqual(switch.count(),1)
        for button in (self.window.source_button,self.window.close_button,self.window.logout_button,self.window.expand_button):
            self.assertTrue(self.window.rect().contains(button.geometry()));self.assertGreaterEqual(button.height(),44)

    def test_catalog_refresh_preserves_horizontal_scroll_and_existing_artwork(self):
        more=tuple(LibraryCollection(f'rec-{i}','playlist',f'推荐{i}','','id-'+str(i),group='recommended') for i in range(10))
        self.window.set_collections(self.catalog+more);self.window._expand_group('recommended');self.settle()
        self.window._shelves[0].scroll.horizontalScrollBar().setValue(200)
        self.window.set_collections(self.catalog+more)
        self.assertEqual(self.window._shelves[0].scroll.horizontalScrollBar().value(),200)
        self.assertTrue(all(item.song is None for item in self.window._shelves[0].items))

    def test_search_hides_collection_strip_and_clear_restores_selected_group(self):
        self.window._expand_group('recommended');self.settle()
        songs=self.songs(self.catalog[1]);self.window.search.setText('搜索');self.window._search_timer.stop()
        self.window.set_search_results((songs[0],))
        self.assertEqual(self.window.collections,(songs[0],));self.assertEqual(len(self.window._rows),1)
        self.assertEqual(self.window._shelves,[])
        self.window.search.clear()
        self.assertNotIn(songs[1],self.window.collections)
        self.assertEqual(self.window._shelves[0].items,(self.catalog[1],))
