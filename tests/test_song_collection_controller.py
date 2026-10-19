from __future__ import annotations

from dataclasses import replace
import unittest

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from api.models import ColorToken, TrackData
from api.online_music import AccountSnapshot, CatalogPage, OnlineMusicError, OnlinePlaylist, OnlineSong
from local.song_collection_controller import SongCollectionController


class Tasks(QObject):
    completed = Signal(object, object, object)

    def __init__(self):
        super().__init__()
        self.items = []
        self.accepted = True
        self.closed = False

    def submit(self, token, action):
        if self.accepted:
            self.items.append((token, action))
        return self.accepted

    def run(self, index=0):
        token, action = self.items.pop(index)
        try:
            result, error = action(), None
        except Exception as exc:
            result, error = None, getattr(exc, "code", "request_failed")
        self.completed.emit(token, result, error)

    def close(self):
        self.closed = True


class Player(QObject):
    changed = Signal()

    def __init__(self, songs=()):
        super().__init__()
        self.queue = songs
        self.track = None
        if songs:
            self.select(songs[0])

    def select(self, song):
        self.track = TrackData(song.title, song.artist, (), ColorToken(1, 2, 3), ColorToken(4, 5, 6), (),
                               track_id=f"owned:{song.source_id}:{song.song_id}")
        self.changed.emit()


class Service:
    def __init__(self):
        self.account = AccountSnapshot("netease", True, "42")
        self.calls = []
        self.error = None
        self.liked = False
        self.page = CatalogPage((OnlinePlaylist("netease", "12", "own"),), 1, False)

    def account_status(self, source, refresh=False):
        self.calls.append(("account", source, refresh))
        return self.account

    def favorite_status(self, source, song_id, *, expected_user_id):
        self.calls.append(("status", source, song_id, expected_user_id))
        if self.error:
            raise self.error
        return self.liked

    def set_favorite(self, source, song_id, liked, *, expected_user_id):
        self.calls.append(("favorite", source, song_id, liked, expected_user_id))
        if self.error:
            raise self.error
        self.liked = liked
        return liked

    def writable_playlists(self, source, *, limit, offset, expected_user_id):
        self.calls.append(("playlists", source, limit, offset, expected_user_id))
        if self.error:
            raise self.error
        return self.page

    def add_song_to_playlist(self, source, song_id, playlist_id, *, expected_user_id):
        self.calls.append(("add", source, song_id, playlist_id, expected_user_id))
        if self.error:
            raise self.error


class SongCollectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.songs = (OnlineSong("netease", "9", "one"), OnlineSong("netease", "10", "two"))
        self.player = Player(self.songs)
        self.service = Service()
        self.tasks = Tasks()
        self.controller = SongCollectionController(self.service, self.player, tasks=self.tasks)
        self.addCleanup(self.controller.close)
        self.track_id = "owned:netease:9"

    def ready(self):
        self.tasks.run()
        state = self.controller.context(self.track_id)
        self.assertTrue(state.available)
        self.assertFalse(state.busy)
        self.assertEqual(state.user_id, "42")

    def writes(self):
        return [call for call in self.service.calls if call[0] in {"favorite", "add"}]

    def test_status_is_async_and_repeated_player_ticks_do_not_refetch(self):
        self.assertTrue(self.controller.context(self.track_id).busy)
        self.assertFalse(any(call[0] == "status" for call in self.service.calls))
        self.ready()
        self.assertIs(self.controller.context(self.track_id).liked, False)
        for _ in range(10):
            self.player.changed.emit()
        self.assertFalse(self.tasks.items)
        self.assertEqual(len([call for call in self.service.calls if call[0] == "status"]), 1)

    def test_current_visual_song_during_dj_voice_is_the_ordinary_song(self):
        voice = OnlineSong("netease", "dj-audio:1", "voice", media_kind="dj_voice", introduced_song_id="9")
        self.player.queue = (voice, *self.songs)
        self.player.index = 0
        self.ready()
        self.assertTrue(self.controller.toggle_favorite(self.track_id))
        self.tasks.run()
        self.assertEqual(self.writes(), [("favorite", "netease", "9", True, "42")])

    def test_system_or_unmatched_track_has_no_collection_context(self):
        self.ready()
        self.assertFalse(self.controller.context("system:9").track_id)
        self.player.track = replace(self.player.track, track_id="owned:netease:999")
        self.player.changed.emit()
        self.assertFalse(self.controller.context(self.track_id).track_id)
        self.assertFalse(self.controller.toggle_favorite(self.track_id))

    def test_explicit_toggle_waits_for_confirmation_and_suppresses_double_click(self):
        self.ready()
        self.assertTrue(self.controller.toggle_favorite(self.track_id))
        self.assertFalse(self.controller.toggle_favorite(self.track_id))
        state = self.controller.context(self.track_id)
        self.assertIs(state.liked, False)
        self.assertTrue(state.busy)
        self.tasks.run()
        self.assertTrue(self.controller.context(self.track_id).liked)
        self.assertEqual(len(self.writes()), 1)

    def test_unknown_like_never_becomes_implicit_unlike_or_like(self):
        self.service.error = OnlineMusicError("timeout", "netease", "signed-private-url")
        self.tasks.run()
        self.assertIsNone(self.controller.context(self.track_id).liked)
        self.assertFalse(self.controller.toggle_favorite(self.track_id))
        self.assertEqual(self.writes(), [])
        self.assertNotIn("signed-private-url", self.controller.context(self.track_id).message)
        self.assertEqual(len(self.tasks.items), 1, "only explicit status refresh")

    def test_queued_write_is_cancelled_before_dispatch_after_song_change(self):
        self.ready()
        self.controller.toggle_favorite(self.track_id)
        self.player.select(self.songs[1])
        self.tasks.run(0)
        self.assertFalse(self.writes())
        self.assertFalse(self.controller.context(self.track_id).track_id)
        self.tasks.run()
        self.assertEqual(self.controller.context("owned:netease:10").liked, False)

    def test_inflight_old_success_cannot_color_the_new_song_heart(self):
        self.ready()
        self.controller.toggle_favorite(self.track_id)
        token, action = self.tasks.items.pop()
        result = action()
        self.player.select(self.songs[1])
        self.tasks.completed.emit(token, result, None)
        new = self.controller.context("owned:netease:10")
        self.assertIsNone(new.liked)
        self.assertTrue(new.busy)

    def test_account_change_without_player_signal_blocks_context_and_queued_writes(self):
        self.ready()
        self.controller.toggle_favorite(self.track_id)
        self.service.account = replace(self.service.account, user_id="99")
        self.assertFalse(self.controller.context(self.track_id).available)
        self.tasks.run()
        self.assertFalse(self.writes())
        self.assertFalse(self.controller.context(self.track_id).available)

    def test_same_user_relogin_replaces_authority_and_cancels_old_queued_write(self):
        self.ready()
        self.controller.toggle_favorite(self.track_id)
        self.service.account = replace(self.service.account)
        self.assertFalse(self.controller.context(self.track_id).available)
        self.tasks.run()
        self.assertFalse(self.writes())
        self.assertFalse(self.controller.context(self.track_id).available)

    def test_press_authority_survives_status_completion_and_repeated_player_ticks(self):
        token = self.controller.capture_authority(self.track_id)
        self.assertIsNotNone(token)
        self.ready()
        for _ in range(5):
            self.player.changed.emit()
        self.assertTrue(self.controller.authority_matches(self.track_id, token))
        self.assertTrue(self.controller.toggle_favorite(self.track_id, expected_authority=token))
        self.tasks.run()
        self.assertEqual(len(self.writes()), 1)

    def test_held_press_cannot_write_to_a_new_account_even_after_new_status_is_ready(self):
        for next_user in ("99", "42"):
            with self.subTest(next_user=next_user):
                if self.tasks.items:
                    self.tasks.run()
                token = self.controller.capture_authority(self.track_id)
                self.service.account = replace(self.service.account, user_id=next_user)
                self.player.changed.emit()
                self.tasks.run()
                self.assertTrue(self.controller.context(self.track_id).available)
                self.assertFalse(self.controller.authority_matches(self.track_id, token))
                self.assertFalse(self.controller.toggle_favorite(self.track_id, expected_authority=token))
                self.assertFalse(self.controller.load_playlists(self.track_id, expected_authority=token))
                self.assertFalse(self.controller.add_to_playlist("12", self.track_id, expected_authority=token))
                self.assertFalse(self.tasks.items)
        self.assertFalse(self.writes())

    def test_same_user_relogin_invalidates_held_press_after_status_read(self):
        self.ready()
        token = self.controller.capture_authority(self.track_id)
        self.service.account = replace(self.service.account)
        self.player.changed.emit()
        self.tasks.run()
        self.assertFalse(self.controller.authority_matches(self.track_id, token))
        self.assertFalse(self.controller.toggle_favorite(self.track_id, expected_authority=token))
        self.assertFalse(self.writes())

    def test_returning_to_the_same_song_does_not_restore_an_old_press_authority(self):
        self.ready()
        token = self.controller.capture_authority(self.track_id)
        self.player.select(self.songs[1])
        self.player.select(self.songs[0])
        self.tasks.run()
        self.tasks.run()
        self.assertFalse(self.controller.authority_matches(self.track_id, token))
        self.assertFalse(self.controller.toggle_favorite(self.track_id, expected_authority=token))
        self.assertFalse(self.controller.toggle_favorite(self.track_id, expected_authority=None))
        self.assertFalse(self.writes())

    def test_playlist_actions_require_a_loaded_destination_from_same_source(self):
        self.ready()
        self.assertFalse(self.controller.add_to_playlist("12", self.track_id))
        self.assertTrue(self.controller.load_playlists(self.track_id))
        self.tasks.run()
        state = self.controller.context(self.track_id)
        self.assertTrue(state.playlists_loaded)
        self.assertEqual([p.playlist_id for p in state.playlists], ["12"])
        self.assertTrue(self.controller.add_to_playlist("12", self.track_id))
        self.tasks.run()
        self.assertEqual(self.writes(), [("add", "netease", "9", "12", "42")])
        self.assertEqual(self.controller.context(self.track_id).message, "已添加到歌单")

    def test_paging_consumes_filtered_page_cursor_and_deduplicates_rows(self):
        self.ready()
        self.service.page = CatalogPage((), 100, True)
        self.controller.load_playlists(self.track_id)
        self.tasks.run()
        self.assertTrue(self.controller.context(self.track_id).playlist_has_more)
        self.service.page = CatalogPage((OnlinePlaylist("netease", "12", "one"),
                                         OnlinePlaylist("netease", "12", "duplicate"),
                                         OnlinePlaylist("qq", "33", "wrong source")), 103, False)
        self.controller.load_playlists(self.track_id, more=True)
        self.tasks.run()
        self.assertEqual([p.playlist_id for p in self.controller.context(self.track_id).playlists], ["12"])
        self.assertIn(("playlists", "netease", 100, 100, "42"), self.service.calls)

    def test_write_timeout_clears_confirmed_heart_until_explicit_refresh_without_retry(self):
        self.ready()
        self.service.error = OnlineMusicError("timeout", "netease", "synthetic-secret")
        self.controller.toggle_favorite(self.track_id)
        self.tasks.run()
        state = self.controller.context(self.track_id)
        self.assertIsNone(state.liked)
        self.assertFalse(state.busy)
        self.assertEqual(len(self.writes()), 1)
        self.assertFalse(self.tasks.items)
        self.assertNotIn("synthetic-secret", state.message)

    def test_worker_rejection_and_close_clear_pending_without_dispatch(self):
        self.ready()
        self.tasks.accepted = False
        self.assertFalse(self.controller.toggle_favorite(self.track_id))
        self.assertFalse(self.controller.context(self.track_id).busy)
        self.assertFalse(self.writes())
        self.controller.close()
        self.assertFalse(self.controller.context(self.track_id).available)
        self.assertTrue(self.tasks.closed)

    def test_unverified_adapter_has_truthful_unsupported_context(self):
        self.tasks.run()
        song = OnlineSong("qq", "qq-mid", "qq")
        self.player.queue = (song,)
        self.player.select(song)
        state = self.controller.context("owned:qq:qq-mid")
        self.assertFalse(state.available)
        self.assertIn("暂不支持", state.message)
        self.assertFalse(self.tasks.items)


if __name__ == "__main__":
    unittest.main()
