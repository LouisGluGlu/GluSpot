from __future__ import annotations

import unittest

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from api.online_music import AccountSnapshot, OnlineSong
from local.music_library_window import LibraryCollection, MusicLibraryWindow


class LibraryHorizontalCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.window=MusicLibraryWindow()
        self.window.set_account(AccountSnapshot('netease',True,'horizontal-account','横向歌单'))
        self.catalog=(LibraryCollection('fm','fm','私人漫游',''),
            LibraryCollection('daily','daily','每日推荐',''),
            *(LibraryCollection(f'rec-{i}','playlist',f'推荐歌单{i}','',str(i),group='recommended') for i in range(8)),
            LibraryCollection('mine','playlist','个人歌单','','mine'))
        self.window.set_collections(self.catalog)
        self.window.show();self.app.processEvents()
        self.played=QSignalSpy(self.window.collectionRequested)
        self.browsed=QSignalSpy(self.window.collectionBrowseRequested)

    def tearDown(self):
        self.window.close();self.window.deleteLater();self.app.processEvents()

    def settle(self):
        self.window._section_motion.setCurrentTime(self.window._section_motion.duration())
        self.app.processEvents()

    def test_folded_groups_only_expand_collection_covers_without_browsing_songs(self):
        self.assertEqual(len(self.window._groups),3)
        self.assertEqual(self.window._shelves,[])
        self.window._expand_group('recommended');self.settle()
        self.assertEqual(len(self.window._shelves),1)
        shelf=self.window._shelves[0]
        self.assertEqual(shelf.group_key,'recommended')
        self.assertEqual(tuple(item.kind for item,_,_ in shelf.tiles),('playlist',)*8)
        self.assertTrue(all(item.song is None and queue is None for item,_,queue in shelf.tiles))
        self.assertEqual(self.browsed.count(),0)
        self.assertEqual(self.played.count(),0)
        peers=self.window._groups
        self.assertEqual(peers[0].y(),peers[1].y())
        self.assertLess(shelf.y(),peers[0].y())
        self.assertLessEqual(peers[0].geometry().right(),peers[1].geometry().left())

    def test_horizontal_cover_plays_collection_default_with_global_cover_rect(self):
        self.window._expand_group('recommended');self.settle()
        collection,cover,_=self.window._shelves[0].tiles[0]
        QTest.mouseClick(cover,Qt.MouseButton.LeftButton)
        self.assertEqual(self.played.count(),1)
        payload=self.played.at(0)
        self.assertEqual(payload[:2],[collection,'default'])
        self.assertGreater(payload[2].width(),100)
        self.assertEqual(self.browsed.count(),0)

    def test_daily_and_fm_are_horizontal_collection_targets_too(self):
        self.window._expand_group('for_you');self.settle()
        shelf=self.window._shelves[0]
        self.assertEqual(tuple(item.kind for item,_,_ in shelf.tiles),('fm','daily'))
        QTest.mouseClick(shelf.tiles[1][1],Qt.MouseButton.LeftButton)
        self.assertEqual(self.played.at(0)[:2],[self.catalog[1],'default'])
        self.assertEqual(self.browsed.count(),0)

    def test_expand_all_only_builds_three_collection_shelves_and_emits_saved_preference(self):
        persisted=QSignalSpy(self.window.libraryExpandChanged)
        self.window._toggle_library_expanded(True);self.settle()
        self.assertEqual(len(self.window._shelves),3)
        self.assertEqual(self.window._groups,[])
        self.assertEqual(sum(len(shelf.tiles) for shelf in self.window._shelves),len(self.catalog))
        self.assertEqual(self.browsed.count(),0)
        self.assertEqual(persisted.at(0),[True])
        self.window.set_library_expanded(False);self.settle()
        self.assertEqual(len(self.window._groups),3)

    def test_switch_is_two_stage_and_repeated_click_cannot_play_a_collection(self):
        self.window._expand_group('recommended');self.settle()
        self.window._expand_group('personal')
        overlay=self.window._section_overlay
        self.assertIsNotNone(overlay)
        overlay.fraction=.21
        old_rect,old_blend=overlay.pose('recommended')
        new_rect,new_blend=overlay.pose('personal')
        self.assertEqual(new_blend,0.)
        self.assertGreater(old_blend,0.)
        self.assertGreater(old_rect.top(),4.)
        self.assertEqual(new_rect,overlay.frames['personal'][2])
        self.window._expand_group('for_you');self.settle()
        self.assertIsNone(self.window._section_overlay)
        self.assertEqual(self.window._shelves[0].group_key,'for_you')
        self.assertEqual(self.played.count(),0)

    def test_resize_search_hide_and_account_change_cancel_motion_without_playback(self):
        for action in ('resize','search','hide','account'):
            self.window._expand_group('recommended')
            self.assertIsNotNone(self.window._section_overlay)
            if action=='resize':self.window.resize(680,610)
            elif action=='search':self.window.search.setText('query')
            elif action=='hide':self.window.hide()
            else:self.window.set_account(AccountSnapshot('qq',True,'other-account','其他账号'))
            self.app.processEvents()
            self.assertIsNone(self.window._section_overlay)
            self.assertEqual(self.played.count(),0)
            if action=='search':self.window.search.clear();self.window._search_timer.stop()
            if action=='hide':self.window.show();self.app.processEvents()
            if action=='account':self.window.set_collections(self.catalog)
            self.window._selected_group_key=None;self.window._rebuild_rows()

    def test_selecting_either_peer_rotates_adjacent_into_clicked_slot_then_old_top_into_adjacent_slot(self):
        for clicked in ('for_you','personal'):
            with self.subTest(clicked=clicked):
                self.window._selected_group_key=None;self.window._peer_order=[];self.window._rebuild_rows()
                self.window._expand_group('recommended');self.settle()
                starts={item.group_key:QRectF(item.geometry()) for item in self.window._groups}
                adjacent='personal' if clicked=='for_you' else 'for_you'
                self.window._expand_group(clicked)
                overlay=self.window._section_overlay
                expected_order=['personal','recommended'] if clicked=='for_you' else ['recommended','for_you']
                self.assertEqual(self.window._peer_order,expected_order)
                self.assertEqual(overlay.frames[adjacent][3],starts[clicked])
                self.assertEqual(overlay.frames['recommended'][3],starts[adjacent])
                # Phase one collapses only the old top above the clicked
                # peer; phase two moves all three continuously to their slots.
                overlay.fraction=.21
                self.assertEqual(overlay.pose(adjacent)[0],starts[adjacent])
                self.assertEqual(overlay.pose(clicked)[0],starts[clicked])
                overlay.fraction=.42
                baseline=overlay.pose('recommended')[0]
                self.assertEqual(baseline.x(),starts[clicked].x())
                self.assertEqual(baseline.bottom(),starts[clicked].top()-12.)
                overlay.fraction=.71
                mid,blend=overlay.pose(adjacent)
                self.assertAlmostEqual(blend,.5)
                self.assertAlmostEqual(mid.x(),(starts[adjacent].x()+starts[clicked].x())/2.)
                self.settle()
                final={item.group_key:QRectF(item.geometry()) for item in self.window._groups}
                self.assertEqual(final[adjacent],starts[clicked])
                self.assertEqual(final['recommended'],starts[adjacent])
                self.assertEqual(self.played.count(),0)

    def test_legacy_track_result_never_enters_catalog_and_search_queue_stays_available(self):
        song=OnlineSong('netease','song','搜索歌曲',artist='歌手')
        row=LibraryCollection('song','song',song.title,'',song=song)
        self.window.set_playlist_tracks(self.catalog[2],(row,))
        self.assertNotIn(row,self.window.collections)
        self.window.search.setText('搜索');self.window._search_timer.stop()
        self.window.set_search_results((row,))
        queued=QSignalSpy(self.window.queueNextRequested)
        QTest.mouseClick(self.window._rows[0].queue_button,Qt.MouseButton.LeftButton)
        self.assertEqual(queued.at(0),[row])
        self.assertEqual(self.window._shelves,[])

    def test_shift_wheel_scrolls_horizontal_collection_strip(self):
        self.window._expand_group('recommended');self.settle()
        shelf=self.window._shelves[0]
        self.assertGreater(shelf.scroll.horizontalScrollBar().maximum(),0)
        event=QWheelEvent(QPointF(35,35),QPointF(shelf.mapToGlobal(shelf.rect().center())),
            QPoint(),QPoint(0,-120),
            Qt.MouseButton.NoButton,Qt.KeyboardModifier.ShiftModifier,Qt.ScrollPhase.NoScrollPhase,False)
        QApplication.sendEvent(shelf.scroll.viewport(),event)
        self.assertGreater(shelf.scroll.horizontalScrollBar().value(),0)
