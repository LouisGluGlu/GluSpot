import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QWidget

from api.lyrics import LocalLyricsStore
from api.online_music import OnlineSong
from local.music_library_window import LibraryCollection
from local.music_playlist_page import PlaylistPage
from local.owned_music_player import OwnedMusicPlayer


def _rows(count, source="netease"):
    return tuple(LibraryCollection(f"song:{source}:s{i}", "song", f"歌曲{i}", "",
                                   song=OnlineSong(source, f"s{i}", f"歌曲{i}", "歌手", duration_seconds=180))
                 for i in range(count))


class PlaylistPageWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.host = QWidget()
        self.host.resize(700, 600)
        self.page = PlaylistPage(self.host, artwork_lookup=lambda key: None)
        self.page.setGeometry(0, 0, 700, 600)
        self.collection = LibraryCollection("playlist:netease:1", "playlist", "我的歌单", "", "1")
        self.host.show()
        self.page.open(self.collection)
        self.page.set_rows(self.collection, _rows(5))
        self.app.processEvents()

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()

    def click(self, rect, double=False):
        point = rect.center().toPoint()
        if double:
            QTest.mouseDClick(self.page.canvas, Qt.MouseButton.LeftButton, pos=point)
        else:
            QTest.mouseClick(self.page.canvas, Qt.MouseButton.LeftButton, pos=point)

    def test_sync_button_only_on_own_playlists_and_emits_the_collection(self):
        canvas = self.page.canvas
        self.assertFalse(self.page.can_sync(), "no account platform yet")
        self.page.sync_source = "netease"
        self.assertTrue(self.page.can_sync())
        self.assertGreater(canvas.sync_rect().left(), canvas.play_all_rect().right())
        spy = QSignalSpy(self.page.syncRequested)
        self.click(canvas.sync_rect())
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0)[0], self.collection)
        recommended = LibraryCollection("playlist:netease:2", "playlist", "推荐", "", "2", group="recommended")
        self.page.open(recommended)
        self.assertFalse(self.page.can_sync())
        self.page.sync_source = "qq"
        self.page.open(self.collection)
        self.assertFalse(self.page.can_sync())

    def test_header_and_every_song_row(self):
        canvas = self.page.canvas
        self.assertEqual(len(self.page.rows), 5)
        self.assertGreaterEqual(canvas.height(), canvas.header_height() + 5 * 58)
        # The cover is left-aligned; the details sit to its right.
        self.assertLess(canvas.cover_rect().left(), 40)
        self.assertGreater(canvas.play_all_rect().left(), canvas.cover_rect().right())
        self.assertIn("5 首歌曲", self.page.summary())
        self.assertIn("15 分钟", self.page.summary())
        image = QImage(canvas.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        canvas.render(image)
        self.assertFalse(image.isNull())

    def test_cover_and_double_click_play_one_song_now(self):
        spy = QSignalSpy(self.page.songRequested)
        self.click(self.page.canvas.row_cover_rect(2))
        self.assertEqual(spy.count(), 1)
        collection, rows, index, rect, how = spy.at(0)
        self.assertEqual((collection, index, how), (self.collection, 2, "single"))
        self.assertTrue(rect.isValid())
        blank = self.page.canvas.row_rect(3)
        blank.setLeft(self.page.canvas.row_cover_rect(3).right() + 30)
        blank.setWidth(60)
        self.click(blank, double=True)
        self.assertEqual(spy.at(spy.count() - 1)[2:5:2], [3, "single"])

    def test_play_this_shrinks_the_list_then_plays_the_list_from_that_row(self):
        spy = QSignalSpy(self.page.songRequested)
        self.click(self.page.canvas.action_rect(1, "play"))
        self.assertEqual(spy.count(), 0)          # the list shrinks first
        self.page._shrink_animation.setCurrentTime(self.page._shrink_animation.duration())
        self.app.processEvents()
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0)[2], 1)
        self.assertEqual(spy.at(0)[4], "list")
        scale, _shift, opacity = self.page.shrink_transform_values()
        self.assertLess(scale, 0.7)
        self.assertLess(opacity, 0.3)
        self.page.reset_shrink()
        self.assertEqual(self.page.shrink_transform_values()[0], 1.0)

    def test_next_and_add_actions(self):
        queued, added = QSignalSpy(self.page.queueNextRequested), QSignalSpy(self.page.addRequested)
        self.click(self.page.canvas.action_rect(4, "next"))
        self.click(self.page.canvas.action_rect(0, "add"))
        self.assertEqual(queued.at(0)[0], self.page.rows[4])
        self.assertEqual(added.at(0)[0], self.page.rows[0])

    def test_rows_for_another_playlist_are_ignored(self):
        other = LibraryCollection("playlist:netease:2", "playlist", "别的", "", "2")
        self.page.set_rows(other, _rows(2))
        self.assertEqual(len(self.page.rows), 5)


class LibrarySidebarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_a_sidebar_section_leaves_the_playlist_page(self):
        from api.online_music import AccountSnapshot
        from local.music_library_window import MusicLibraryWindow
        window = MusicLibraryWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.set_account(AccountSnapshot("netease", True, "fixture", "账号"))
        playlist = LibraryCollection("playlist:netease:1", "playlist", "推荐", "", "1", group="recommended")
        window.set_collections((playlist,))
        window.show()
        self.app.processEvents()
        window.open_playlist_page(playlist)
        self.assertTrue(window.playlist_page_open)
        self.assertFalse(window.scroll.isVisible())
        window._variant_view.buttons["personal"].click()
        self.app.processEvents()
        self.assertFalse(window.playlist_page_open)
        self.assertTrue(window.scroll.isVisible())


class _Tasks(QObject):
    completed = Signal(object, object, object)

    def __init__(self):
        super().__init__()
        self.jobs = []

    def submit(self, token, action):
        self.jobs.append((token, action))
        return True

    def close(self):
        self.jobs.clear()


class _MediaPlayer(QObject):
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object, str)

    def __init__(self):
        super().__init__()
        self.state = QMediaPlayer.PlaybackState.StoppedState

    def setAudioOutput(self, _audio): pass
    def stop(self):
        self.state = QMediaPlayer.PlaybackState.StoppedState
    def setSource(self, _url): pass
    def play(self): pass
    def pause(self): pass
    def playbackState(self): return self.state
    def isSeekable(self): return False
    def setPosition(self, _value): pass


class InsertAndPlayTests(unittest.TestCase):
    """Apple songs play on the companion page, so no stream is needed here."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.plays = []
        service = type("Service", (), {})()

        def transport(_source, action, params=None):
            if action == "play":
                self.plays.append(params["song_id"])
            return {"playing": action == "play", "position": 0, "duration": 200,
                    "song_id": (params or {}).get("song_id", ""), "preview": False}
        service.companion_transport = transport
        service.apple_timed_lyrics = lambda *_args: ""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tasks = _Tasks()
        self.player = OwnedMusicPlayer(service, LocalLyricsStore(root=Path(folder.name)), player=_MediaPlayer(),
                                       audio=object(), dj_player=_MediaPlayer(), dj_audio=object(), tasks=self.tasks,
                                       artwork_tasks=_Tasks(), prefetch_tasks=_Tasks(), lyric_tasks=_Tasks(),
                                       stream_cache=None)
        self.addCleanup(self.player.close)

    def songs(self, *ids):
        return tuple(OnlineSong("apple_music", song_id, song_id, "singer", duration_seconds=200) for song_id in ids)

    def start(self):
        token, action = next(job for job in reversed(self.tasks.jobs) if job[0][0] == "companion")
        self.tasks.completed.emit(token, action(), None)

    def end_song(self):
        self.player._companion_status_ready(self.player._epoch,
                                            {"playing": True, "position": 60.0, "duration": 200}, None)
        self.player._companion_status_ready(self.player._epoch,
                                            {"playing": False, "position": 0.0, "duration": 200, "ended": True}, None)
        self.start()

    def test_a_list_plays_from_the_chosen_row_then_the_old_queue_continues(self):
        self.player.playback_mode = "sequential"
        self.assertTrue(self.player.play_queue(self.songs("a", "b", "c"), index=0))
        self.start()
        self.assertTrue(self.player.insert_and_play(self.songs("x", "y", "z"), 1))
        self.start()
        self.assertEqual([s.song_id for s in self.player.queue], ["a", "x", "y", "z", "b", "c"])
        self.end_song()
        self.end_song()
        self.assertEqual(self.plays, ["a", "y", "z", "b"])

    def test_one_song_now_then_back_to_the_queue(self):
        self.player.playback_mode = "sequential"
        self.player.play_queue(self.songs("a", "b"), index=0)
        self.start()
        self.assertTrue(self.player.insert_and_play(self.songs("x"), 0))
        self.start()
        self.end_song()
        self.assertEqual(self.plays, ["a", "x", "b"])

    def test_nothing_playing_makes_the_songs_the_queue(self):
        self.assertTrue(self.player.insert_and_play(self.songs("x", "y"), 1))
        self.start()
        self.assertEqual([s.song_id for s in self.player.queue], ["x", "y"])
        self.assertEqual(self.plays, ["y"])

    def test_rejects_bad_input(self):
        self.assertFalse(self.player.insert_and_play((), 0))
        self.assertFalse(self.player.insert_and_play(self.songs("x"), 3))


if __name__ == "__main__":
    unittest.main()
