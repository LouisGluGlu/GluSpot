from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.models import ColorToken, IslandVisualData, TrackData
from api.online_music import AccountSnapshot, CatalogPage, MusicSource, OnlineMusicError, OnlinePlaylist, OnlineSong, StreamInfo
from local.music_library_controller import MusicLibraryController
from local.music_library_window import LibraryCollection, MusicLibraryWindow
from local.owned_music_player import OwnedMusicPlayer
from local.playback_entry_controller import PlaybackEntryController
from local.playback_entry_window import PlaybackEntryWindow
from tests.test_music_palette import _png
from tests.test_owned_playback import Player as MediaPlayer
from tests.test_playback_entry_controller import FakeIsland as EntryIsland, FakeSettings, FakeSystem, FakeTasks


class Service:
    def __init__(self):
        self.accounts = {source: AccountSnapshot(source, True, source + "-user", source + " account", state="verified")
                         for source in ("netease", "qq", "kugou")}
        self.pages = {}
        self.search_pages = {}
        self.recommended_pages = {}
        self.track_pages = {}
        self.recommendation_covers = {kind: f"https://example.test/{kind}.png" for kind in ("daily", "fm")}
        self.calls = []
        self.logout_error = False

    def account_status(self, source, refresh=False):
        self.calls.append(("account", source, refresh))
        return self.accounts[source]

    def playlists(self, source, *, limit, offset):
        self.calls.append(("playlists", source, limit, offset))
        return self.pages.get((source, offset), CatalogPage((), offset, False))

    def search(self, source, query, *, kind, limit, offset):
        self.calls.append(("search", source, query, kind, limit, offset))
        value = self.search_pages.get((source, query, kind, offset), CatalogPage((), offset, False))
        if isinstance(value, Exception):
            raise value
        return value

    def recommended_playlists(self, source, *, limit, offset):
        self.calls.append(("recommendations", source, limit, offset))
        value = self.recommended_pages.get((source, offset), CatalogPage((), offset, False))
        if isinstance(value, Exception):
            raise value
        return value

    def playlist_tracks(self, source, playlist_id, *, limit, offset):
        self.calls.append(("tracks", source, playlist_id, limit, offset))
        value = self.track_pages.get((source, playlist_id, offset), CatalogPage((), offset, False))
        if isinstance(value, Exception):
            raise value
        return value

    def daily_recommendations(self, source):
        self.calls.append(("daily", source))
        return (OnlineSong(source, "daily-song", "Daily song", artwork_url="https://example.test/daily.png"),)

    def personal_fm(self, source):
        self.calls.append(("fm", source))
        return (OnlineSong(source, "fm-song", "FM song", artwork_url="https://example.test/fm.png"),)

    def recommendation_artwork(self, kind, source):
        self.calls.append(("recommendation_cover", source, kind))
        value = self.recommendation_covers.get(kind, "")
        if isinstance(value, Exception):
            raise value
        return value

    def logout(self, source):
        self.calls.append(("logout", source))
        if self.logout_error:
            raise OnlineMusicError("credential", source, "synthetic-secret-error")
        self.accounts[source] = AccountSnapshot(source)

    def list_sources(self):
        return tuple(MusicSource(source, source, True) for source in self.accounts)

    def invalidate_login(self, session): pass
    def invalidate_source_login(self, source): pass
    def cancel_login(self, session): pass


def track(song, *, position=0):
    return TrackData(song.title, song.artist, ("暂无歌词",), ColorToken(58, 68, 88), ColorToken(34, 44, 62), (),
                     track_id="owned:" + song.source_id + ":" + song.song_id, position_seconds=position)


class Player(QObject):
    changed = Signal()
    statusChanged = Signal(str)

    def __init__(self):
        super().__init__()
        self.track = None
        self.queue = ()
        self.index = -1
        self.status = "idle"
        self._epoch = 0
        self.calls = []
        self.accepted = True
        self.emit_old_before_new = False

    def _set_status(self, status):
        self.status = status
        self.statusChanged.emit(status)

    def play_queue(self, songs, index=0):
        self.calls.append(("queue", tuple(songs)))
        if not self.accepted:
            return False
        if self.track is not None and self.emit_old_before_new:
            self.track = replace(self.track, position_seconds=self.track.position_seconds + 1)
            self.changed.emit()
        self.queue, self.index = tuple(songs), index
        self._epoch += 1
        self.track = replace(track(self.queue[index]), is_playing=True)
        self._set_status("loading_stream")
        self._set_status("playing")
        self.changed.emit()
        return True

    def _catalog(self, kind, *args):
        self.calls.append((kind,) + args)
        if self.accepted:
            self._set_status("loading_catalog")
        return self.accepted

    def play_daily(self, source): return self._catalog("daily", source)
    def play_personal_fm(self, source): return self._catalog("fm", source)
    def play_personal_dj(self, source): return self._catalog("dj", source)
    def play_playlist(self, source, playlist): return self._catalog("playlist", source, playlist)
    def enqueue_next(self, song):
        self.calls.append(("enqueue", song))
        if not self.accepted or self.track is None:
            return False
        self.queue = self.queue[:self.index + 1] + (song,) + self.queue[self.index + 1:]
        return True

    def visual_data(self):
        return IslandVisualData(self.track, (), ()) if self.track else None

    def stop(self):
        self.calls.append(("stop",))
        self._epoch += 1
        self.track, self.queue, self.index = None, (), -1
        self._set_status("idle")
        self.changed.emit()


class Island:
    def __init__(self):
        self.calls = []
        self._music_surface_active = False

    def _apply_visual_data(self, visual): self.calls.append(("visual", visual.track.track_id))
    def _restore_island_from_music_surface(self):
        self.calls.append(("restore",))
        self._music_surface_active = False
    def show(self): self.calls.append(("show",))
    def hide(self): self.calls.append(("hide",))
    def _show_music_fullscreen(self, **kwargs):
        self.calls.append(("surface", kwargs))
        self._music_surface_active = True


class Entry:
    def __init__(self): self.calls = []
    def show_login(self, source=""): self.calls.append(("login", source))
    def _show_island(self): self.calls.append(("island",))


class MusicLibraryControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = FakeSettings(completed=True, owned=True, system=False)
        self.service, self.player = Service(), Player()
        self.tasks, self.art_tasks = FakeTasks(), FakeTasks()
        self.window, self.island, self.entry = MusicLibraryWindow(), Island(), Entry()
        self.controller = MusicLibraryController(self.settings, self.service, self.player, self.island, self.entry,
            window=self.window, tasks=self.tasks, art_tasks=self.art_tasks)
        self.song = OnlineSong("netease", "song-1", "synthetic song")
        self.playlist = OnlinePlaylist("netease", "list-1", "synthetic playlist")
        self.service.pages["netease", 0] = CatalogPage((self.playlist,), 50, False)

    def tearDown(self):
        self.controller.close()
        self.window.deleteLater()
        self.app.processEvents()

    def open(self, source="netease"):
        self.controller.open_source(source)
        self.tasks.run(self.tasks.latest("open"))
        if self.controller._catalog_pending:
            self.tasks.run(self.tasks.latest("initial_catalog"))
        self.window.set_library_expanded(True)
        self.island.calls.clear()

    def test_playlist_cover_reuse_requires_successful_url_provenance(self):
        playlist = replace(self.playlist, artwork_url="https://example.test/original.png")
        self.service.pages["netease", 0] = CatalogPage((playlist,), 50, False)
        self.open()
        key = "playlist:netease:list-1"
        image = QImage(20, 20, QImage.Format.Format_ARGB32)
        image.fill(QColor("red"))
        self.controller._art_completed(("art", self.controller._epoch, key, playlist.artwork_url), image, None)
        self.assertFalse(self.controller.cached_playlist_artwork(playlist).isNull())
        newer = replace(playlist, artwork_url="https://example.test/updated.png")
        self.controller._search_results = self.controller._rows((newer,))
        self.window.set_search_results(self.controller._search_results)
        self.assertTrue(self.controller.cached_playlist_artwork(newer).isNull())

    def test_playlist_cover_reuse_rejects_unproven_replacement_pixels(self):
        playlist = replace(self.playlist, artwork_url="https://example.test/original.png")
        self.service.pages["netease", 0] = CatalogPage((playlist,), 50, False)
        self.open()
        key = "playlist:netease:list-1"
        image = QImage(20, 20, QImage.Format.Format_ARGB32)
        image.fill(QColor("red"))
        self.controller._art_completed(("art", self.controller._epoch, key, playlist.artwork_url), image, None)
        image.fill(QColor("blue"))
        self.window.set_artwork(key, image)
        self.assertTrue(self.controller.cached_playlist_artwork(playlist).isNull())

    def search(self, query):
        blocked = self.window.search.blockSignals(True)
        self.window.search.setText(query)
        self.window.search.blockSignals(blocked)
        self.controller.search(query)

    def test_open_loads_only_current_account_and_real_catalog_with_netease_collections(self):
        self.open()
        self.assertEqual([row.kind for row in self.window.collections], ["daily", "fm", "playlist"])
        self.assertEqual(self.window._account.user_id, "netease-user")
        self.assertEqual(self.service.calls, [("account", "netease", False), ("playlists", "netease", 50, 0)])
        self.assertEqual(self.settings.values["v2_online_music_source"], "netease")

    def test_recommendations_arrive_independently_and_do_not_replace_personal_catalog_or_search(self):
        recommended = OnlinePlaylist("netease", "recommended-1", "推荐歌单")
        self.service.recommended_pages["netease", 0] = CatalogPage((recommended,), 1, False)
        self.open()
        job = self.tasks.latest("recommended_catalog")
        self.search("query")
        self.tasks.run(job)
        self.assertEqual(self.window._results, ())
        self.assertEqual(self.window.search.text(), "query")
        rows = {row.key: row for row in self.controller._collections}
        self.assertEqual(rows["playlist:netease:recommended-1"].group, "recommended")
        self.assertEqual(rows["playlist:netease:list-1"].group, "personal")
        self.window.search.clear()
        self.assertIn(recommended.playlist_id, {row.playlist_id for row in self.window.collections})

    def test_recommendation_failure_and_old_source_reply_preserve_current_personal_rows(self):
        self.open()
        token, action = self.tasks.latest("recommended_catalog")
        previous = self.controller._collections
        self.tasks.completed.emit(token, None, "network")
        self.assertEqual(self.controller._collections, previous)
        self.assertEqual(self.window._notice, "")
        self.assertIn("recommended", self.controller._catalog_retry_at)
        self.assertIn("recommended", self.window._catalog_loading)
        self.open("qq")
        self.assertEqual(self.window._notice, "")
        self.assertIsNone(action())
        self.tasks.completed.emit(token, CatalogPage((self.playlist,), 1, False), None)
        self.assertEqual(self.controller._collections, ())

    def test_recommendation_failure_does_not_replace_current_search_pagination(self):
        self.open()
        recommendation, _ = self.tasks.latest("recommended_catalog")
        self.service.search_pages["netease", "query", "songs", 0] = CatalogPage((self.song,), 30, True)
        self.search("query")
        self.tasks.run(self.tasks.latest("search"))
        self.assertTrue(self.window._has_more)
        self.tasks.completed.emit(recommendation, None, "network")
        self.assertTrue(self.window._has_more)
        self.window.more_button.click()
        self.assertEqual(self.tasks.latest("search")[0][2], self.controller._request)
        self.assertTrue(self.controller._pending)

    def test_recommended_next_page_failure_releases_explicit_retry_at_same_offset(self):
        self.service.recommended_pages['netease',0] = CatalogPage((
            OnlinePlaylist('netease','rec-first','推荐一'),),30,True)
        self.open()
        self.tasks.run(self.tasks.latest('recommended_catalog'))
        self.window.more_button.click()
        failed = self.tasks.latest('recommended_catalog')
        self.assertEqual(failed[0][-1],30)
        self.tasks.completed.emit(failed[0],None,'network')
        self.assertFalse(self.window._more_requested)
        self.window.more_button.click()
        self.assertEqual(self.tasks.latest('recommended_catalog')[0][-1],30)
        self.assertTrue(self.window._more_requested)

    def test_recommended_first_page_retry_keeps_personal_rows_and_real_pagination(self):
        self.service.pages["netease", 0] = CatalogPage((self.playlist,), 50, True)
        self.open()
        token, _ = self.tasks.latest("recommended_catalog")
        self.tasks.completed.emit(token, None, "network")
        self.assertFalse(self.controller._recommended_has_more)
        self.assertTrue(self.controller._recommended_retry)
        self.assertEqual(self.controller._recommended_offset, 0)
        self.assertIn(self.playlist.playlist_id, {row.playlist_id for row in self.window.collections})
        jobs = len(self.tasks.jobs)
        self.window.more_button.click()
        self.assertEqual(len(self.tasks.jobs), jobs + 1)
        retry = self.tasks.jobs[-1]
        self.assertEqual(retry[0], ("recommended_catalog", self.controller._epoch, 0))
        recommended = OnlinePlaylist("netease", "retry-success", "重试推荐")
        self.tasks.completed.emit(retry[0], CatalogPage((recommended,), 1, False), None)
        self.assertFalse(self.controller._recommended_retry)
        self.assertFalse(self.controller._recommended_has_more)
        self.assertEqual(self.window._notice, "")
        self.assertTrue(self.window._has_more)  # Personal catalog still has a real next page.
        self.assertEqual({row.playlist_id for row in self.window.collections if row.kind == "playlist"},
                         {self.playlist.playlist_id, recommended.playlist_id})
        self.window.more_button.click()
        self.assertEqual(self.tasks.jobs[-1][0][0], "catalog")

    def test_recommendation_retry_busy_and_success_keep_search_cursor_and_other_notice(self):
        self.open()
        token, _ = self.tasks.latest("recommended_catalog")
        self.tasks.completed.emit(token, None, "network")
        self.tasks.accepting = False
        self.window.more_button.click()
        self.assertTrue(self.controller._recommended_retry)
        self.assertFalse(self.window._more_requested)
        self.assertEqual(self.window._notice, "")
        self.assertIn("recommended", self.controller._catalog_retry_at)
        self.tasks.accepting = True
        self.window.more_button.click()
        retry = self.tasks.latest("recommended_catalog")
        self.assertEqual(retry[0][-1], 0)
        self.service.search_pages["netease", "query", "songs", 0] = CatalogPage((self.song,), 30, True)
        self.search("query")
        self.tasks.run(self.tasks.latest("search"))
        self.window.set_notice("已加入播放列表，将在下一首播放")
        self.tasks.completed.emit(retry[0], CatalogPage((), 0, False), None)
        self.assertFalse(self.controller._recommended_retry)
        self.assertTrue(self.window._has_more)
        self.assertEqual(tuple(row.song for row in self.window._results), (self.song,))
        self.assertIn("下一首", self.window._notice)

    def test_personal_next_page_failure_releases_explicit_retry(self):
        self.service.pages['netease',0]=CatalogPage((self.playlist,),50,True)
        self.open()
        self.window.more_button.click()
        failed=self.tasks.latest('catalog')
        self.tasks.completed.emit(failed[0],None,'network')
        self.assertFalse(self.window._more_requested)
        self.window.more_button.click()
        self.assertEqual(self.tasks.latest('catalog')[0],failed[0])
        self.assertTrue(self.window._more_requested)

    def test_folded_group_only_exposes_playlist_covers_without_loading_tracks_or_changing_playback(self):
        self.open()
        self.player.play_queue((OnlineSong("netease", "current", "current"),))
        current, queue = self.player.track, self.player.queue
        self.window.set_library_expanded(False)
        jobs, calls = tuple(self.tasks.jobs), tuple(self.service.calls)
        group = next(group for group in self.window._groups if group.group_key == "personal")
        group.click()
        self.assertEqual([shelf.group_key for shelf in self.window._shelves], ["personal"])
        self.assertEqual([item.playlist_id for item in self.window._shelves[0].items], ["list-1"])
        self.assertEqual(self.window._rows, [])
        self.assertEqual(tuple(self.tasks.jobs), jobs)
        self.assertEqual(tuple(self.service.calls), calls)
        self.assertIs(self.player.track, current)
        self.assertIs(self.player.queue, queue)

    def test_expand_all_and_late_catalog_rebuild_keep_playlist_covers_without_track_requests(self):
        playlists = tuple(OnlinePlaylist("netease", str(i), f"playlist {i}") for i in range(30))
        self.service.pages["netease", 0] = CatalogPage(playlists, 30, False)
        self.open()
        self.player.play_queue((self.song,))
        current, queue = self.player.track, self.player.queue
        self.window.set_library_expanded(False)
        jobs = tuple(self.tasks.jobs)
        self.window.set_library_expanded(True)
        recommended = OnlinePlaylist("netease", "recommended", "recommended")
        self.tasks.completed.emit(self.tasks.latest("recommended_catalog")[0],
            CatalogPage((recommended,), 1, False), None)
        self.assertEqual([shelf.group_key for shelf in self.window._shelves],
                         ["for_you", "recommended", "personal"])
        self.assertEqual(sum(len(shelf.items) for shelf in self.window._shelves), 33)
        self.assertEqual(self.window._rows, [])
        self.assertEqual(tuple(self.tasks.jobs), jobs)
        self.assertFalse(any(call[0] == "tracks" for call in self.service.calls))
        self.assertIs(self.player.track, current)
        self.assertIs(self.player.queue, queue)

    def test_playlist_cover_dispatches_direct_playback_with_remembered_surface(self):
        self.open()
        self.settings.set("v2_library_last_playback_surface", "window")
        shelf = next(shelf for shelf in self.window._shelves if shelf.group_key == "personal")
        shelf.tiles[0][1].click()
        self.assertEqual(self.player.calls, [("playlist", "netease", "list-1")])
        self.assertEqual(self.controller._opening[0], "window")
        self.assertTrue(self.controller._opening[1].isValid())
        self.assertFalse(any(call[0] == "tracks" for call in self.service.calls))
        self.assertFalse(any(token[0] == "browse" for token, _action in self.tasks.jobs))

    def test_queue_next_uses_visible_authorized_song_and_keeps_current_playback(self):
        self.open()
        self.player.play_queue((OnlineSong("netease", "current", "current"),))
        current = self.player.track
        self.search("query")
        song_row = self.controller._rows((self.song,))[0]
        self.window.set_search_results((song_row,))
        self.window.queueNextRequested.emit(song_row)
        self.assertIs(self.player.track, current)
        self.assertEqual(self.player.queue[1], self.song)
        self.assertIn("下一首", self.window._notice)
        hidden = replace(song_row, key="song:netease:unavailable", song=replace(self.song, song_id="unavailable"))
        self.controller.queue_next(hidden)
        self.assertEqual(len(self.player.queue), 2)

    def test_search_song_selection_keeps_current_search_song_queue_and_selected_index(self):
        self.open()
        second = OnlineSong("netease", "song-2", "second")
        self.service.search_pages["netease", "query", "songs", 0] = CatalogPage((self.song, second), 2, False)
        self.search("query")
        self.tasks.run(self.tasks.latest("search"))
        self.window._rows[1].cover.click()
        self.assertEqual(self.player.queue, (self.song, second))
        self.assertEqual(self.player.index, 1)
        self.assertEqual(self.player.track.track_id, "owned:netease:song-2")

    def test_last_closed_surface_controls_default_click_and_expand_toggle_is_persisted(self):
        from types import SimpleNamespace
        self.open()
        self.player.play_queue((self.song,))
        self.island._music_surface_active = True
        self.island.music_fullscreen_overlay = SimpleNamespace(_pending_fullscreen=False, _fullscreen=True)
        self.assertTrue(self.controller.return_from_music_surface())
        self.assertEqual(self.settings.get("v2_library_last_playback_surface"), "window")
        row = self.controller._rows((self.song,))[0]
        self.window.set_collections((row,))
        self.island.calls.clear()
        self.controller.play_collection(row, "default", QRect())
        surface = next(call for call in self.island.calls if call[0] == "surface")
        self.assertFalse(surface[1]["fullscreen"])
        self.window.libraryExpandChanged.emit(True)
        self.assertTrue(self.settings.get("v2_library_expanded"))

    def test_library_claims_exclusive_island_visibility_and_updates_now_playing(self):
        self.controller.open_source("netease")
        self.assertTrue(self.island._music_library_active)
        self.assertIn(("hide",), self.island.calls)
        self.player.play_queue((self.song,))
        self.assertIs(self.window._now_playing, self.player.track)
        self.window.close()
        self.assertFalse(self.island._music_library_active)

    def test_library_state_events_sync_minimum_without_releasing_navigation_ownership(self):
        self.island._sync_minimum_surface = Mock()
        self.controller.open_source("netease")
        self.app.processEvents()
        self.island._sync_minimum_surface.reset_mock()
        self.window.showMinimized()
        self.app.processEvents()
        self.assertTrue(self.window.isMinimized())
        self.assertTrue(self.island._music_library_active)
        self.island._sync_minimum_surface.assert_called()
        self.island._sync_minimum_surface.reset_mock()
        self.window.showNormal()
        self.app.processEvents()
        self.island._sync_minimum_surface.assert_called()
        self.assertTrue(self.island._music_library_active)

    def test_entry_window_state_events_share_the_same_minimum_surface_policy(self):
        entry_window = PlaybackEntryWindow()
        entry = Entry()
        entry.window = entry_window
        island = Island()
        island._sync_minimum_surface = Mock()
        window = MusicLibraryWindow()
        controller = MusicLibraryController(self.settings, self.service, self.player, island, entry,
            window=window, tasks=FakeTasks(), art_tasks=FakeTasks())
        try:
            island._music_library_active = True
            entry_window.show()
            self.app.processEvents()
            self.assertEqual(island._music_library_windows, (window, entry_window))
            island._sync_minimum_surface.reset_mock()
            entry_window.showMinimized()
            self.app.processEvents()
            self.assertTrue(entry_window.isMinimized())
            island._sync_minimum_surface.assert_called()
            self.assertTrue(island._music_library_active)
            island._sync_minimum_surface.reset_mock()
            entry_window.showNormal()
            self.app.processEvents()
            island._sync_minimum_surface.assert_called()
            self.assertTrue(island._music_library_active)
        finally:
            controller.close()
            entry_window.close()
            entry_window.deleteLater()
            window.deleteLater()

    def test_open_failure_does_not_invent_account_and_signed_out_returns_to_source_login(self):
        self.service.accounts["qq"] = AccountSnapshot("qq")
        self.open("qq")
        self.assertEqual(self.entry.calls, [("login", "qq")])
        self.assertFalse(self.window.isVisible())
        self.assertFalse(any(call[0] == "playlists" for call in self.service.calls))

    def test_account_avatar_appears_before_the_initial_catalog_request_runs(self):
        self.service.accounts["qq"] = replace(self.service.accounts["qq"], avatar_url="https://example.test/avatar.png")
        self.controller.open_source("qq")
        self.tasks.run(self.tasks.latest("open"))
        self.assertTrue(self.window._account.logged_in)
        self.assertEqual(self.window._account.avatar_url, "https://example.test/avatar.png")
        self.assertEqual(self.service.calls, [("account", "qq", False)])
        self.assertTrue(self.controller._catalog_pending)
        self.tasks.run(self.tasks.latest("initial_catalog"))
        self.assertFalse(self.window._loading)

    def test_initial_catalog_retains_current_search_and_is_available_on_return(self):
        self.service.pages["qq", 0] = CatalogPage((OnlinePlaylist("qq", "fixture", "fixture"),), 1, False)
        self.controller.open_source("qq")
        self.tasks.run(self.tasks.latest("open"))
        catalog = self.tasks.latest("initial_catalog")
        self.search("query")
        self.service.search_pages["qq", "query", "songs", 0] = CatalogPage((OnlineSong("qq", "song", "song"),), 1, False)
        self.tasks.run(self.tasks.latest("search"))
        self.tasks.run(catalog)
        self.assertEqual([row.kind for row in self.window.collections], ["song"])
        self.window.search.clear()
        self.assertEqual([row.playlist_id for row in self.window.collections], ["fixture"])

    def test_catalog_failure_retains_profile_without_claiming_unsupported_recommendations(self):
        self.controller.open_source("qq")
        self.tasks.run(self.tasks.latest("open"))
        token, _ = self.tasks.latest("initial_catalog")
        self.tasks.completed.emit(token, None, "synthetic-private-error")
        self.assertTrue(self.window._account.logged_in)
        self.assertFalse(self.window._loading)
        self.assertEqual(self.window._error, "")
        self.assertIn("initial_catalog", self.controller._catalog_retry_at)
        self.assertIn("personal", self.window._catalog_loading)

    def test_initial_catalog_source_switch_action_and_reply_are_fenced(self):
        self.controller.open_source("netease")
        self.tasks.run(self.tasks.latest("open"))
        token, action = self.tasks.latest("initial_catalog")
        self.controller.open_source("qq")
        self.assertIsNone(action())
        self.tasks.completed.emit(token, self.service.pages["netease", 0], None)
        self.assertFalse(self.window._account.logged_in)
        self.assertEqual(self.window.collections, ())

    def test_catalog_cover_preview_is_independent_and_does_not_use_discarded_song_requests(self):
        self.open()
        jobs = [job for job in self.tasks.jobs if job[0][0] == "recommendation_cover"]
        self.assertEqual([token[2] for token, _ in jobs], ["daily", "fm"])
        self.assertFalse(self.window._loading)
        self.search("query")
        for job in jobs:
            self.tasks.run(job)
        rows = {row.key: row for row in self.controller._collections}
        self.assertEqual(rows["daily"].artwork_url, "https://example.test/daily.png")
        self.assertEqual(rows["fm"].artwork_url, "https://example.test/fm.png")
        self.assertEqual(self.window.collections, ())  # The active search is not replaced.
        art_keys = [token[2] for token, _ in self.art_tasks.jobs]
        self.assertIn("daily", art_keys)
        self.assertIn("fm", art_keys)
        self.assertFalse(any(call[0] in {"daily", "fm", "tracks"} for call in self.service.calls))
        self.assertEqual(self.window.search.text(), "query")
        self.window.search.clear()
        self.assertEqual(self.window.collections[0].key, "daily")

    def test_recommendation_cover_jobs_are_queued_after_account_catalog_and_failure_stays_local(self):
        self.open()
        kinds = [token[0] for token, _ in self.tasks.jobs]
        self.assertLess(kinds.index("initial_catalog"), kinds.index("recommendation_cover"))
        self.service.recommendation_covers["daily"] = OnlineMusicError("network", message="private-secret")
        job = next(job for job in self.tasks.jobs if job[0][0:1] == ("recommendation_cover",) and job[0][2] == "daily")
        self.tasks.run(job)
        self.assertFalse(self.window._loading)
        self.assertEqual(self.window._error, "")
        self.assertEqual(self.controller._recommendation_art_retries, {"daily": 1})
        self.assertIn("daily", self.controller._recommendation_art_retry_at)
        for _ in range(2):
            self.controller._recommendation_art_retry_at["daily"] = 0
            self.controller._retry_art()
            self.tasks.run(self.tasks.latest("recommendation_cover"))
        self.assertEqual(self.controller._recommendation_art_retries["daily"], 2)
        self.assertIn("daily", self.controller._recommendation_art_retry_at)
        self.assertGreater(self.controller._art_retry_timer.interval(), 29_000)
        self.assertEqual(len([token for token, _ in self.tasks.jobs
                              if token[0] == "recommendation_cover" and token[2] == "daily"]), 3)
        self.assertEqual(self.window.collections[-1].playlist_id, self.playlist.playlist_id)

    def test_recommendation_cover_retry_success_uses_existing_thumbnail_retry_path(self):
        self.open()
        job = next(job for job in self.tasks.jobs if job[0][0] == "recommendation_cover" and job[0][2] == "fm")
        self.tasks.completed.emit(job[0], "", None)
        self.controller._recommendation_art_retry_at["fm"] = 0
        self.controller._retry_art()
        self.tasks.run(self.tasks.latest("recommendation_cover"))
        row = next(row for row in self.window.collections if row.key == "fm")
        self.assertEqual(row.artwork_url, self.service.recommendation_covers["fm"])
        self.assertNotIn("fm", self.controller._recommendation_art_retry_at)
        token = next(token for token, _ in self.art_tasks.jobs if token[2] == "fm")
        image = QImage(32, 32, QImage.Format.Format_ARGB32)
        image.fill(QColor("#8a6351"))
        self.controller._art_completed(token, image, None)
        self.assertFalse(self.window._artwork["fm"].isNull())

    def test_back_leaves_search_then_source_without_logging_out_or_stopping_playback(self):
        self.open()
        self.player.play_queue((self.song,))
        self.search("query")
        old = self.tasks.latest("search")
        self.window.backRequested.emit()
        self.assertEqual(self.window.search.text(), "")
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.window.collections, self.controller._collections)
        self.window.backRequested.emit()
        self.assertFalse(self.window.isVisible())
        self.assertEqual(self.entry.calls, [("login", "")])
        self.assertNotIn(("stop",), self.player.calls)
        self.assertFalse(any(call[0] == "logout" for call in self.service.calls))
        self.assertTrue(self.service.accounts["netease"].logged_in)
        self.assertIsNone(old[1]())
        self.controller.search("late")
        self.assertEqual(self.controller._source, "")

    def test_song_surface_back_reuses_catalog_and_keeps_playback_and_search(self):
        self.open()
        self.player.play_queue((self.song,))
        self.search("query")
        self.window.hide()
        self.island._music_surface_active = True
        track_before = self.player.track
        calls_before = tuple(self.service.calls)
        self.assertTrue(self.controller.return_from_music_surface())
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.window.search.text(), "query")
        self.assertIs(self.player.track, track_before)
        self.assertEqual(tuple(self.service.calls), calls_before)
        self.assertIn(("restore",), self.island.calls)
        self.assertIn(("hide",), self.island.calls)
        self.assertNotIn(("stop",), self.player.calls)
        self.assertEqual(self.entry.calls, [])

    def test_song_surface_back_reopens_playing_source_after_source_switch(self):
        self.open()
        self.player.play_queue((self.song,))
        self.controller.switch_source()
        self.assertTrue(self.controller.return_from_music_surface())
        self.assertEqual(self.controller._source, "netease")
        self.assertIsNotNone(self.player.track)
        self.assertNotIn(("stop",), self.player.calls)
        self.tasks.run(self.tasks.latest("open"))
        self.assertEqual(self.window.collections, self.controller._collections)

    def test_song_surface_back_rejects_system_and_disabled_owned_playback(self):
        self.assertFalse(self.controller.return_from_music_surface())
        self.player.play_queue((self.song,))
        self.player.track = replace(self.player.track, track_id="external-song")
        self.assertFalse(self.controller.return_from_music_surface())
        self.player.track = replace(self.player.track, track_id="owned:netease:song-1")
        self.settings.set("v2_owned_playback_enabled", False)
        self.assertFalse(self.controller.return_from_music_surface())

    def test_source_switch_fences_recommendation_cover_work_and_results(self):
        self.open()
        old = [job for job in self.tasks.jobs if job[0][0] == "recommendation_cover"]
        self.open("qq")
        self.assertEqual(len([token for token, _ in self.tasks.jobs if token[0] == "recommendation_cover"]), 2)
        for token, action in old:
            self.assertEqual(action(), "")
            self.tasks.completed.emit(token, "https://example.test/stale.png", None)
        self.assertFalse(any(call[0] in {"daily", "fm", "tracks"} for call in self.service.calls))
        self.assertFalse(self.controller._recommendation_art_pending)
        self.assertEqual(self.window._account.source_id, "qq")
        self.assertEqual(self.controller._collections, ())
        self.assertEqual(self.window.collections, ())

    def test_back_during_song_request_keeps_late_completion_from_opening_surface(self):
        self.open()
        self.controller.play_collection(self.window.collections[0], "default", QRect())
        self.window.backRequested.emit()
        self.assertIsNone(self.controller._opening)
        self.assertNotIn(("visual", "owned:netease:song-1"), self.island.calls)
        calls_after_back = tuple(self.island.calls)
        self.player.play_queue((self.song,))
        self.assertEqual(tuple(self.island.calls), calls_after_back)
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        self.assertFalse(self.window._opening)

    def test_stale_open_action_and_completion_do_not_replace_new_source(self):
        self.controller.open_source("netease")
        old_token, old_action = self.tasks.latest("open")
        self.controller.open_source("qq")
        before = len(self.service.calls)
        self.assertIsNone(old_action())
        self.assertEqual(len(self.service.calls), before)
        self.tasks.completed.emit(old_token, (self.service.accounts["netease"], self.service.pages["netease", 0]), None)
        self.assertEqual(self.window._account.source_id, "qq")
        self.assertFalse(self.window._account.logged_in)
        self.assertEqual(self.window._account.user_id, "")
        self.tasks.run(self.tasks.latest("open"))
        self.assertEqual(self.window._account.source_id, "qq")
        self.assertEqual(self.window.collections, ())

    def test_catalog_pagination_advances_and_deduplicates_provider_ids(self):
        self.service.pages["netease", 0] = CatalogPage((self.playlist,), 50, True)
        second = OnlinePlaylist("netease", "list-2", "second")
        self.service.pages["netease", 50] = CatalogPage((self.playlist, second), 100, False)
        self.open()
        self.controller.load_more()
        self.tasks.run(self.tasks.latest("catalog"))
        self.assertEqual([row.playlist_id for row in self.window.collections if row.kind == "playlist"], ["list-1", "list-2"])
        self.assertFalse(self.window._has_more)
        self.assertIn(("playlists", "netease", 50, 50), self.service.calls)

    def test_search_both_kinds_paginates_independent_cursors_and_retains_only_current_platform(self):
        self.open()
        self.service.search_pages["netease", "query", "songs", 0] = CatalogPage((self.song,), 30, True)
        self.service.search_pages["netease", "query", "playlists", 0] = CatalogPage((self.playlist,), 30, False)
        second = OnlineSong("netease", "song-2", "second song")
        self.service.search_pages["netease", "query", "songs", 30] = CatalogPage((self.song, second), 60, False)
        self.search("query")
        self.tasks.run(self.tasks.latest("search"))
        self.assertEqual([row.kind for row in self.window.collections], ["song", "playlist"])
        self.controller.load_more()
        self.tasks.run(self.tasks.latest("search"))
        calls = [call for call in self.service.calls if call[0] == "search"]
        self.assertEqual(calls, [("search", "netease", "query", "songs", 30, 0),
                                ("search", "netease", "query", "playlists", 30, 0),
                                ("search", "netease", "query", "songs", 30, 30)])
        self.assertEqual([row.key for row in self.window.collections], ["song:netease:song-1", "playlist:netease:list-1", "song:netease:song-2"])

    def test_editing_search_fences_old_results_before_debounce_without_network(self):
        self.open()
        self.search("first")
        token, action = self.tasks.latest("search")
        previous_request = self.controller._request
        jobs = len(self.tasks.jobs)
        self.window.search.setText("second")
        self.assertTrue(self.window._search_timer.isActive())
        self.assertGreater(self.controller._request, previous_request)
        self.assertEqual(len(self.tasks.jobs), jobs)
        self.assertIsNone(action())
        self.tasks.completed.emit(token, ({"songs": CatalogPage((self.song,), 1, False)}, []), None)
        self.assertEqual(self.window._results, ())
        self.assertEqual(self.controller._search_results, ())
        stale_row = self.controller._rows((self.song,))[0]
        self.controller.queue_next(stale_row)
        self.assertEqual(self.player.calls, [])
        second = OnlineSong("netease", "second", "second result")
        self.service.search_pages["netease", "second", "songs", 0] = CatalogPage((second,), 1, False)
        self.window._submit_search()
        self.tasks.run(self.tasks.latest("search"))
        self.assertEqual(tuple(row.song for row in self.window._results), (second,))

    def test_search_paginates_while_initial_personal_catalog_is_still_pending(self):
        self.controller.open_source("qq")
        self.tasks.run(self.tasks.latest("open"))
        catalog = self.tasks.latest("initial_catalog")
        first = OnlineSong("qq", "first", "first result")
        second = OnlineSong("qq", "second", "second result")
        self.service.search_pages["qq", "query", "songs", 0] = CatalogPage((first,), 30, True)
        self.service.search_pages["qq", "query", "songs", 30] = CatalogPage((second,), 60, False)
        self.search("query")
        self.tasks.run(self.tasks.latest("search"))
        self.assertTrue(self.controller._catalog_pending)
        self.window.more_button.click()
        self.assertTrue(self.controller._pending)
        self.tasks.run(self.tasks.latest("search"))
        self.assertFalse(self.window._more_requested)
        self.assertEqual(tuple(row.song for row in self.window._results), (first, second))
        self.tasks.run(catalog)
        self.assertEqual(tuple(row.song for row in self.window._results), (first, second))

    def test_stale_search_query_and_source_action_and_reply_are_ignored(self):
        self.open()
        self.search("first")
        first_token, first_action = self.tasks.latest("search")
        self.search("second")
        self.assertIsNone(first_action())
        self.tasks.completed.emit(first_token, ({"songs": CatalogPage((self.song,), 30, False)}, []), None)
        self.assertEqual(self.window._results, ())
        second_token, second_action = self.tasks.latest("search")
        self.controller.open_source("qq")
        self.assertIsNone(second_action())
        self.tasks.completed.emit(second_token, ({"songs": CatalogPage((self.song,), 30, False)}, []), None)
        self.assertEqual(self.window._results, ())

    def test_partial_search_error_keeps_other_kind_and_does_not_expose_error_text(self):
        self.open()
        self.service.search_pages["netease", "q", "songs", 0] = CatalogPage((self.song,), 30, False)
        self.service.search_pages["netease", "q", "playlists", 0] = RuntimeError("synthetic-secret")
        self.search("q")
        self.tasks.run(self.tasks.latest("search"))
        self.assertEqual([row.kind for row in self.window.collections], ["song"])
        self.assertEqual(self.window._error, "")
        self.assertIn("search", self.controller._catalog_retry_at)
        self.assertTrue(self.window._catalog_skeleton.isVisible())

    def test_clearing_search_invalidates_reply_and_restores_catalog(self):
        self.open()
        self.search("q")
        token, action = self.tasks.latest("search")
        self.search("")
        self.tasks.completed.emit(token, ({"songs": CatalogPage((self.song,), 30, True)}, []), None)
        self.assertEqual([row.kind for row in self.window.collections], ["daily", "fm", "playlist"])
        self.assertFalse(self.controller._pending)

    def test_artwork_source_switch_discards_old_avatar_and_cover_and_pumps_new_jobs(self):
        self.service.accounts["netease"] = replace(self.service.accounts["netease"], avatar_url="https://example.test/old-avatar.png")
        self.service.pages["netease", 0] = CatalogPage((replace(self.playlist, artwork_url="https://example.test/old-cover.png"),), 50, False)
        self.open()
        old_jobs = list(self.art_tasks.jobs)
        self.assertEqual(len(old_jobs), 2)
        self.service.accounts["qq"] = replace(self.service.accounts["qq"], avatar_url="https://example.test/new-avatar.png")
        self.open("qq")
        image = QImage.fromData(_png(QColor("#345678")))
        for token, _ in old_jobs:
            self.art_tasks.completed.emit(token, image, None)
        self.assertNotIn("account", self.window._artwork)
        token, _ = self.art_tasks.latest("art")
        self.art_tasks.completed.emit(token, image, None)
        self.assertIn("account", self.window._artwork)
        self.assertEqual(self.window._account.source_id, "qq")

    def test_logout_is_scoped_and_stops_only_matching_owned_source(self):
        self.open()
        self.player.play_queue((self.song,))
        self.window.logout_button.click()
        self.tasks.run(self.tasks.latest("logout"))
        self.assertIn(("stop",), self.player.calls)
        self.assertIn(("logout", "netease"), self.service.calls)
        self.assertFalse(self.service.accounts["netease"].logged_in)
        self.assertTrue(self.service.accounts["qq"].logged_in)
        self.assertEqual(self.entry.calls, [("login", "netease")])

    def test_other_source_playback_survives_logout_and_failure_stays_reviewable(self):
        self.open()
        self.player.play_queue((OnlineSong("qq", "q-song", "QQ song"),))
        self.service.logout_error = True
        self.controller.logout()
        self.tasks.run(self.tasks.latest("logout"))
        self.assertNotIn(("stop",), self.player.calls)
        self.assertIsNotNone(self.player.track)
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.entry.calls, [])
        self.assertNotIn("secret", self.window._error)

    def test_stale_logout_reply_does_not_redirect_new_source(self):
        self.open()
        self.controller.logout()
        token, _ = self.tasks.latest("logout")
        self.open("qq")
        self.tasks.completed.emit(token, None, None)
        self.assertEqual(self.entry.calls, [])
        self.assertTrue(self.window.isVisible())

    def test_search_event_during_logout_cannot_invalidate_logout_completion(self):
        self.open()
        self.controller.logout()
        self.search("late debounce")
        self.tasks.run(self.tasks.latest("logout"))
        self.assertEqual(self.entry.calls, [("login", "netease")])
        self.assertFalse(self.controller._logout_pending)
        self.assertFalse(any(job[0][0] == "search" for job in self.tasks.jobs))

    def test_collection_event_during_logout_cannot_restart_owned_playback(self):
        self.open()
        row = self.window.collections[0]
        self.controller.logout()
        self.controller.play_collection(row, "window", QRect())
        self.assertEqual(self.player.calls, [])
        self.assertIsNone(self.controller._opening)

    def test_netease_dj_has_no_standalone_entry_and_fm_keeps_its_existing_dispatch(self):
        self.open()
        self.assertFalse(any(row.kind == "dj" for row in self.window.collections))
        row = next(row for row in self.window.collections if row.kind == "fm")
        self.controller.play_collection(row, "window", QRect())
        self.assertEqual(self.player.calls[-1], ("fm", "netease"))
        self.assertIsNotNone(self.controller._opening)

    def test_stale_standalone_dj_collection_cannot_start_an_independent_mode(self):
        self.open()
        stale = LibraryCollection("dj", "dj", "私人DJ", "")
        self.controller.play_collection(stale, "window", QRect())
        self.assertEqual(self.player.calls, [])
        self.assertIsNone(self.controller._opening)

    def test_fm_real_introduction_opens_paused_song_surface_without_waiting_for_music(self):
        self.open()
        row = next(row for row in self.window.collections if row.kind == "fm")
        self.controller.play_collection(row, "window", QRect())
        self.player.track = replace(track(self.song), is_playing=False)
        self.player.is_personal_dj_speaking = lambda: True
        self.player._set_status("loading_stream")
        self.player._set_status("playing")
        self.player.changed.emit()
        self.assertIsNone(self.controller._opening)
        self.assertIn(("visual", "owned:netease:song-1"), self.island.calls)
        self.assertTrue(any(call[0] == "surface" for call in self.island.calls))
        self.assertFalse(self.player.track.is_playing)

    def test_paused_song_and_unestablished_intro_keeps_loading_surface_without_applying_track(self):
        self.open()
        row = next(row for row in self.window.collections if row.kind == "fm")
        self.controller.play_collection(row, "window", QRect())
        self.player.track = replace(track(self.song), is_playing=False)
        self.player.is_personal_dj_speaking = lambda: False
        self.player._set_status("loading_stream")
        self.player._set_status("playing")
        self.player.changed.emit()
        self.assertIsNotNone(self.controller._opening)
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        self.assertFalse(any(call[0] == "visual" for call in self.island.calls))
        self.assertFalse(self.window.isVisible())

    def test_buffered_failure_reports_actual_network_expiry_or_storage_reason(self):
        for reason, message in (("incomplete", "下载未完成"), ("expired", "授权已过期"), ("storage", "磁盘")):
            self.open()
            row = self.window.collections[0]
            self.controller.play_collection(row, "window", QRect())
            self.player.failure_reason = reason
            self.player._set_status("stream_unavailable")
            self.assertIn(message, self.window._error)
            self.assertIsNone(self.controller._opening)

    def test_failed_catalog_selection_explicitly_keeps_the_existing_song(self):
        self.open()
        self.player.play_queue((self.song,))
        playing = self.player.track
        self.controller.play_collection(self.window.collections[0], "window", QRect())
        self.player._set_status("catalog_unavailable")
        self.assertIn("当前歌曲继续播放", self.window._error)
        self.assertIs(self.player.track, playing)
        self.assertTrue(self.window.isVisible())
        self.assertIsNone(self.controller._opening)
        self.assertNotIn(("stop",), self.player.calls)
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        self.assertFalse(any(call[0] == "visual" for call in self.island.calls))

    def test_failed_new_stream_does_not_claim_previous_song_continues(self):
        self.open()
        self.controller.play_collection(self.window.collections[0], "window", QRect())
        self.player.track = replace(track(self.song), is_playing=False)
        self.player._set_status("loading_stream")
        self.player._set_status("stream_unavailable")
        self.assertNotIn("当前歌曲继续播放", self.window._error)
        self.assertIsNone(self.controller._opening)

    def test_refused_and_failed_thumbnail_jobs_release_visible_slot_for_scroll_retry(self):
        self.open()
        for rejected in (False, True):
            with self.subTest(rejected=rejected):
                key = f"playlist:netease:art-{rejected}"
                row = LibraryCollection(key, "playlist", "cover retry", "https://example.test/art.png", "art")
                self.art_tasks.accepting = not rejected
                self.window.set_collections((row,))
                self.window._request_visible_artwork()
                if not rejected:
                    token, _ = self.art_tasks.latest("art")
                    self.art_tasks.completed.emit(token, QImage(), "request_failed")
                self.assertNotIn(row.key, self.window._artwork_pending)
                self.assertNotIn(row.key, self.controller._art_keys)
                self.window._artwork.clear()

    def test_visible_artwork_pump_caps_workers_and_backlog_and_retains_account(self):
        self.open()
        rows = tuple(LibraryCollection(f"playlist:netease:art-{i}", "playlist", f"cover {i}",
            f"https://example.test/art-{i}.png", str(i)) for i in range(100))
        self.window.set_collections(rows)
        for row in rows:
            self.controller._queue_art(row.key, row.artwork_url)
        self.assertEqual(self.controller._art_inflight, 2)
        self.assertLessEqual(len(self.controller._art_queue), 32)
        self.assertLessEqual(len(self.controller._art_keys), 34)
        image = QImage(8, 8, QImage.Format.Format_RGB32)
        image.fill(QColor("#345678"))
        self.window.set_artwork("account", image)
        for row in rows:
            self.window.set_artwork(row.key, image)
        self.assertLessEqual(len(self.window._artwork), self.window.ARTWORK_MAX_ENTRIES)
        self.assertIn("account", self.window._artwork)

    def test_default_and_three_modes_handoff_new_song_with_cover_origin(self):
        for mode, fullscreen in (("default", False), ("fullscreen", True), ("window", False), ("island", "island")):
            with self.subTest(mode=mode):
                self.open()
                selected = replace(self.song, song_id="song-" + mode)
                row = LibraryCollection("song:netease:" + selected.song_id, "song", selected.title, "", song=selected)
                self.window.set_collections((row,))
                origin = QRect(self.window.mapToGlobal(self.window.rect().topLeft()), self.window.size())
                self.controller.play_collection(row, mode, origin)
                self.assertIn(("visual", "owned:netease:" + selected.song_id), self.island.calls)
                if mode == "island":
                    self.assertEqual(self.entry.calls[-1], ("island",))
                else:
                    call = next(call for call in reversed(self.island.calls) if call[0] == "surface")
                    self.assertIs(call[1]["fullscreen"], fullscreen)
                    self.assertFalse(call[1]["origin_geometry"].isValid())
                    self.assertTrue(call[1]["origin_snapshot"].isNull())

    def test_old_position_during_catalog_wait_cannot_replace_loading_surface(self):
        self.open()
        self.player.play_queue((self.song,))
        row = next(row for row in self.window.collections if row.kind == "playlist")
        self.controller.play_collection(row, "window", QRect())
        calls_after_present = tuple(self.island.calls)
        self.player.track = replace(self.player.track, position_seconds=5)
        self.player.changed.emit()
        self.assertEqual(tuple(self.island.calls), calls_after_present)
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        self.assertFalse(any(call[0] == "visual" for call in self.island.calls))
        self.player.play_queue((OnlineSong("netease", "new", "new song"),))
        self.assertIn(("visual", "owned:netease:new"), self.island.calls)

    def test_synchronous_old_changed_during_song_selection_cannot_handoff(self):
        self.open()
        self.player.play_queue((OnlineSong("netease", "old", "old song"),))
        self.player.emit_old_before_new = True
        row = LibraryCollection("song:netease:song-1", "song", self.song.title, "", song=self.song)
        self.window.set_collections((row,))
        self.controller.play_collection(row, "window", QRect())
        self.assertEqual([call for call in self.island.calls if call[0] == "visual"], [("visual", "owned:netease:song-1")])

    def test_failed_selection_reenables_real_window_collections_for_retry(self):
        for immediate in (False, True):
            with self.subTest(immediate=immediate):
                self.open()
                self.player.accepted = not immediate
                self.controller.play_collection(self.window.collections[0], "default", QRect())
                if not immediate:
                    self.player._set_status("catalog_unavailable")
                self.assertFalse(self.window._opening)
                self.assertTrue(self.window._shelves)
                self.assertTrue(all(shelf.isEnabled() for shelf in self.window._shelves))

    def test_user_close_invalidates_late_open_art_and_player_handoff(self):
        self.open()
        self.controller.play_collection(self.window.collections[0], "default", QRect())
        self.window.close()
        calls_after_close = tuple(self.island.calls)
        self.player.play_queue((self.song,))
        self.assertEqual(tuple(self.island.calls), calls_after_close)
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        self.controller.open_source("qq")
        token, _ = self.tasks.latest("open")
        self.window.close()
        self.tasks.completed.emit(token, (self.service.accounts["qq"], CatalogPage((), 0, False)), None)
        self.assertEqual(self.window._account.source_id, "qq")
        self.assertFalse(self.window._account.logged_in)
        self.assertEqual(self.window._account.user_id, "")
        self.assertFalse(self.window.isVisible())

    def test_controller_close_makes_late_reply_and_selection_inert(self):
        self.controller.open_source("netease")
        token, action = self.tasks.latest("open")
        self.island.calls.clear()
        self.controller.close()
        self.assertIsNone(action())
        self.tasks.completed.emit(token, (self.service.accounts["netease"], self.service.pages["netease", 0]), None)
        self.controller.search("q")
        self.controller.open_source("qq")
        self.assertEqual(self.service.calls, [])
        self.assertEqual(self.island.calls, [])
        self.assertFalse(self.window.isVisible())

    def test_queue_refusal_and_disabled_owned_mode_do_not_play_or_leak_loading(self):
        self.tasks.accepting = False
        self.controller.open_source("netease")
        self.assertFalse(self.controller._pending)
        self.assertEqual(self.window._error, "")
        self.assertIn("account", self.controller._catalog_retry_at)
        self.assertTrue(self.window._catalog_skeleton.isVisible())
        self.tasks.accepting = True
        self.open()
        self.settings.set("v2_owned_playback_enabled", False)
        self.controller.play_collection(self.window.collections[0], "window", QRect())
        self.assertEqual(self.player.calls, [])
        self.assertIn("开启", self.window._error)


class EntryLibraryRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = FakeSettings(completed=True, owned=True, system=False)
        self.service, self.tasks = Service(), FakeTasks()
        self.window = PlaybackEntryWindow()
        self.callbacks = []
        self.controller = PlaybackEntryController(self.settings, FakeSystem(), EntryIsland(), self.service,
            window=self.window, tasks=self.tasks, login_callback=self.callbacks.append)

    def tearDown(self):
        self.controller.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_saved_source_auto_enter_reuses_current_verified_account_and_callback(self):
        self.controller.show_login("qq", enter_saved=True)
        self.tasks.run(self.tasks.latest("sources"))
        self.tasks.run(self.tasks.latest("account"))
        self.assertEqual(self.callbacks, ["qq"])
        self.assertFalse(self.window.isVisible())
        self.assertIn(("account", "qq", False), self.service.calls)
        self.assertNotIn(("account", "qq", True), self.service.calls)

    def test_saved_source_with_unverified_snapshot_requires_live_account_validation(self):
        verified = self.service.accounts["qq"]
        self.service.accounts["qq"] = AccountSnapshot("qq")
        original = self.service.account_status
        def status(source, refresh=False):
            if refresh:
                self.service.accounts[source] = verified
            return original(source, refresh)
        self.service.account_status = status
        self.controller.show_login("qq", enter_saved=True)
        self.tasks.run(self.tasks.latest("sources"))
        self.tasks.run(self.tasks.latest("account"))
        self.assertEqual(self.callbacks, ["qq"])
        self.assertIn(("account", "qq", True), self.service.calls)

    def test_source_switch_ignores_old_account_and_avatar_reply(self):
        self.service.accounts["netease"] = replace(self.service.accounts["netease"], avatar_url="https://example.test/avatar.png")
        self.controller.show_login()
        self.tasks.run(self.tasks.latest("sources"))
        self.window.select_source("netease")
        old_token, _ = self.tasks.latest("account")
        self.window.select_source("qq")
        self.tasks.completed.emit(old_token, self.service.accounts["netease"], None)
        self.assertEqual(self.callbacks, [])
        self.tasks.run(self.tasks.latest("account"))
        self.assertEqual(self.window.selected_source, "qq")

    def test_account_error_never_enters_library_or_exposes_raw_error(self):
        self.controller.show_login("qq")
        self.tasks.run(self.tasks.latest("sources"))
        token, _ = self.tasks.latest("account")
        self.tasks.completed.emit(token, None, "synthetic-secret")
        self.assertEqual(self.callbacks, [])
        self.assertEqual(self.window.login_state, "error")


class SignalingMediaPlayer(MediaPlayer):
    """Model Qt's synchronous stop/source signals without opening real audio."""
    def stop(self):
        super().stop()
        self.positionChanged.emit(0)
        self.playbackStateChanged.emit(QMediaPlayer.PlaybackState.StoppedState)

    def setSource(self, url):
        super().setSource(url)
        self.positionChanged.emit(0)


class OwnedControllerHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = FakeSettings(completed=True, owned=True, system=False)
        self.service, self.tasks, self.art_tasks = Service(), FakeTasks(), FakeTasks()
        self.play_tasks, self.play_art_tasks, self.play_prefetch_tasks = FakeTasks(), FakeTasks(), FakeTasks()
        self.player = OwnedMusicPlayer(self.service, LocalLyricsStore(root=Path(self.temp.name)),
            player=SignalingMediaPlayer(), audio=object(), tasks=self.play_tasks, artwork_tasks=self.play_art_tasks,
            prefetch_tasks=self.play_prefetch_tasks, stream_cache=None)
        self.window, self.island, self.entry = MusicLibraryWindow(), Island(), Entry()
        self.controller = MusicLibraryController(self.settings, self.service, self.player, self.island, self.entry,
            window=self.window, tasks=self.tasks, art_tasks=self.art_tasks)
        self.controller.open_source("netease")
        self.tasks.run(self.tasks.latest("open"))
        self.island.calls.clear()
        self.song = OnlineSong("netease", "new", "new song")
        self.row = LibraryCollection("song:netease:new", "song", self.song.title, "", song=self.song)
        self.window.search.setText("new song")
        self.window.set_search_results((self.row,))

    def tearDown(self):
        self.controller.close()
        self.player.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_real_owned_stop_signals_handoff_only_new_track(self):
        self.player.play_queue((OnlineSong("netease", "old", "old song"),))
        self.controller.play_collection(self.row, "window", QRect())
        visuals = [call for call in self.island.calls if call[0] == "visual"]
        self.assertEqual(visuals, [])
        self.assertEqual(self.player.status, "loading_stream")
        self.assertFalse(self.window.isVisible())
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        token, _ = self.play_tasks.latest("stream")
        self.play_tasks.completed.emit(token, StreamInfo("netease", "new", "https://example.test/audio.mp3", True), None)
        self.assertEqual([call for call in self.island.calls if call[0] == "visual"], [("visual", "owned:netease:new")])
        self.assertFalse(self.window.isVisible())

    def test_unavailable_stream_returns_from_loading_surface_and_releases_library_controls(self):
        self.window._rows[0].cover.click()
        token, _ = self.play_tasks.latest("stream")
        self.play_tasks.completed.emit(token, StreamInfo("netease", "new"), None)
        self.assertTrue(self.window.isVisible())
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        self.assertFalse(any(call[0] == "visual" for call in self.island.calls))
        self.assertFalse(self.window._opening)
        self.assertTrue(self.window._rows[0].isEnabled())
        self.assertIn("播放地址", self.window._error)

    def test_back_from_search_cancels_pending_stream_before_restoring_catalog(self):
        self.window.search.setText("query")
        self.window.set_search_results((self.row,))
        self.window._rows[0].cover.click()
        token, action = self.play_tasks.latest("stream")
        self.assertIsNotNone(self.controller._opening)
        self.window.backRequested.emit()
        self.assertEqual(self.window.search.text(), "")
        self.assertIsNone(self.controller._opening)
        self.assertIsNone(self.player.track)
        self.assertIsNone(action())
        calls_after_cancel = tuple(self.island.calls)
        self.play_tasks.completed.emit(token, StreamInfo("netease", "new", "https://example.test/audio.mp3", True), None)
        self.assertFalse(self.window.isVisible())
        self.assertEqual(tuple(self.island.calls), calls_after_cancel)
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        self.assertFalse(self.window._opening)
        self.assertFalse(self.window._loading)
        self.assertEqual(self.window.collections, self.controller._collections)

    def test_editing_search_cancels_pending_stream_before_debounce(self):
        self.window.search.setText("query")
        self.window.set_search_results((self.row,))
        self.window._rows[0].cover.click()
        token, action = self.play_tasks.latest("stream")
        self.assertIsNotNone(self.controller._opening)
        jobs = len(self.tasks.jobs)
        self.window.search.setText("next query")
        self.assertEqual(len(self.tasks.jobs), jobs)
        self.assertTrue(self.window._search_timer.isActive())
        self.assertIsNone(self.controller._opening)
        self.assertIsNone(self.player.track)
        self.assertIsNone(action())
        calls_after_cancel = tuple(self.island.calls)
        self.play_tasks.completed.emit(token, StreamInfo("netease", "new", "https://example.test/audio.mp3", True), None)
        self.assertFalse(self.window.isVisible())
        self.assertEqual(tuple(self.island.calls), calls_after_cancel)
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        self.assertFalse(self.window._opening)

    def test_clear_search_button_cancels_pending_stream_before_restoring_catalog(self):
        self.window.search.setText("query")
        self.window.set_search_results((self.row,))
        self.window._rows[0].cover.click()
        token, action = self.play_tasks.latest("stream")
        self.assertIsNotNone(self.controller._opening)
        self.window.clear_search_button.click()
        self.assertEqual(self.window.search.text(), "")
        self.assertIsNone(self.controller._opening)
        self.assertIsNone(self.player.track)
        self.assertIsNone(action())
        calls_after_cancel = tuple(self.island.calls)
        self.play_tasks.completed.emit(token, StreamInfo("netease", "new", "https://example.test/audio.mp3", True), None)
        self.assertFalse(self.window.isVisible())
        self.assertEqual(tuple(self.island.calls), calls_after_cancel)
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        self.assertFalse(self.window._opening)
        self.assertFalse(self.window._loading)
        self.assertEqual(self.window.collections, self.controller._collections)

    def test_delayed_decoder_failure_does_not_hide_error_in_music_surface(self):
        self.window._rows[0].cover.click()
        self.player.failure_reason = "network"
        self.player._set_status("playback_failed")
        self.assertTrue(self.window.isVisible())
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        self.assertFalse(any(call[0] == "visual" for call in self.island.calls))
        self.assertIn("连接失败", self.window._error)
        self.assertFalse(self.window._loading)

    def test_real_owned_rejected_stream_does_not_handoff_or_disable_rows(self):
        self.player.play_queue((OnlineSong("netease", "old", "old song"),))
        self.play_tasks.accepting = False
        self.window._rows[0].cover.click()
        self.assertEqual(self.island.calls, [])
        self.assertIsNone(self.controller._opening)
        self.assertFalse(self.window._opening)
        self.assertTrue(self.window._rows[0].isEnabled())
        self.assertEqual(self.player.status, "busy")

    def test_real_owned_refused_catalog_keeps_current_audio_and_specific_failure(self):
        old = OnlineSong("netease", "old", "old song")
        self.player.play_queue((old,))
        token, _ = self.play_tasks.latest("stream")
        self.play_tasks.completed.emit(token, StreamInfo("netease", "old", "https://example.test/old.mp3", True), None)
        current = self.player.track
        self.assertTrue(current.is_playing)
        self.play_tasks.accepting = False
        self.window.search.clear()
        self.window.set_collections((LibraryCollection("fm", "fm", "私人漫游", ""),))
        self.controller.play_collection(self.window.collections[0], "default", QRect())
        self.assertIs(self.player.track, current)
        self.assertTrue(current.is_playing)
        self.assertIn("服务繁忙", self.window._error)
        self.assertIn("当前歌曲继续播放", self.window._error)
        self.assertIsNone(self.controller._opening)
        self.assertEqual(self.island.calls, [])

    def test_real_owned_catalog_to_rejected_stream_returns_from_loading_surface(self):
        playlist = LibraryCollection("playlist:netease:one", "playlist", "one", "", "one")
        self.window.search.clear()
        self.window.set_library_expanded(True)
        self.window.set_collections((playlist,))
        self.controller.play_collection(playlist, "default", QRect())
        token, _ = self.play_tasks.latest("catalog")
        self.play_tasks.accepting = False
        self.play_tasks.completed.emit(token, (self.song,), None)
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        self.assertFalse(any(call[0] == "visual" for call in self.island.calls))
        self.assertTrue(self.window.isVisible())
        self.assertIsNone(self.controller._opening)
        self.assertFalse(self.window._opening)
        self.assertTrue(self.window._shelves[0].isEnabled())

    def test_real_playlist_opens_window_before_first_page_and_keeps_background_page_authority(self):
        first = OnlineSong("netease", "first", "first song")
        second = OnlineSong("netease", "second", "second song")
        playlist = LibraryCollection("playlist:netease:one", "playlist", "one", "", "one")
        self.service.track_pages["netease", "one", 0] = CatalogPage((first,), 100, True)
        self.service.track_pages["netease", "one", 100] = CatalogPage((second,), 200, False)
        self.settings.set("v2_library_last_playback_surface", "window")
        self.window.search.clear()
        self.window.set_library_expanded(True)
        self.window.set_collections((playlist,))
        self.window._shelves[0].tiles[0][1].click()
        self.assertFalse(any(call[0] == "tracks" for call in self.service.calls))
        self.assertEqual(self.player.status, "loading_catalog")
        self.assertFalse(self.window.isVisible())
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        self.play_tasks.run(self.play_tasks.latest("catalog"))
        self.assertEqual([call for call in self.service.calls if call[0] == "tracks"],
                         [("tracks", "netease", "one", 100, 0)])
        self.assertEqual(self.player.queue, (first,))
        self.assertEqual(self.player.index, 0)
        self.assertEqual(self.player.track.track_id, "owned:netease:first")
        self.assertEqual(self.player.status, "loading_stream")
        self.assertFalse(self.window.isVisible())
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        self.assertFalse(any(call[0] == "visual" for call in self.island.calls))
        token, _ = self.play_tasks.latest("stream")
        self.play_tasks.completed.emit(token, StreamInfo("netease", "first", "https://example.test/first.mp3", True), None)
        surface = next(call for call in self.island.calls if call[0] == "surface")
        self.assertFalse(surface[1]["fullscreen"])
        self.assertFalse(self.window.isVisible())
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        self.play_prefetch_tasks.run(self.play_prefetch_tasks.latest("playlist_page"))
        self.assertEqual(self.player.queue, (first, second))
        self.assertEqual(self.player.index, 0)
        self.assertEqual(self.player.track.track_id, "owned:netease:first")
        self.assertEqual([call for call in self.service.calls if call[0] == "tracks"],
                         [("tracks", "netease", "one", 100, 0), ("tracks", "netease", "one", 100, 100)])

    def test_real_playlist_cover_prioritizes_immediate_window_over_remembered_island(self):
        playlist = LibraryCollection("playlist:netease:one", "playlist", "one", "", "one")
        self.service.track_pages["netease", "one", 0] = CatalogPage((self.song,), 1, False)
        self.settings.set("v2_library_last_playback_surface", "island")
        self.window.search.clear()
        self.window.set_library_expanded(True)
        self.window.set_collections((playlist,))
        self.window._shelves[0].tiles[0][1].click()
        self.play_tasks.run(self.play_tasks.latest("catalog"))
        self.assertEqual(self.player.index, 0)
        self.assertFalse(self.window.isVisible())
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        token, _ = self.play_tasks.latest("stream")
        self.play_tasks.completed.emit(token, StreamInfo("netease", "new", "https://example.test/new.mp3", True), None)
        self.assertFalse(self.window.isVisible())
        self.assertEqual(self.entry.calls, [])
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        surface = next(call for call in self.island.calls if call[0] == "surface")
        self.assertFalse(surface[1]["fullscreen"])

    def test_real_playlist_catalog_failure_retains_healthy_old_audio_and_queue(self):
        old = OnlineSong("netease", "old", "old song")
        self.player.play_queue((old,))
        token, _ = self.play_tasks.latest("stream")
        self.play_tasks.completed.emit(token, StreamInfo("netease", "old", "https://example.test/old.mp3", True), None)
        current, queue, source = self.player.track, self.player.queue, self.player.player.source()
        playlist = LibraryCollection("playlist:netease:unavailable", "playlist", "unavailable", "", "unavailable")
        self.service.track_pages["netease", "unavailable", 0] = RuntimeError("synthetic-private-catalog-error")
        self.window.search.clear()
        self.window.set_library_expanded(True)
        self.window.set_collections((playlist,))
        self.window._shelves[0].tiles[0][1].click()
        self.assertIs(self.player.track, current)
        self.assertTrue(current.is_playing)
        self.assertEqual(self.player.player.source(), source)
        self.play_tasks.run(self.play_tasks.latest("catalog"))
        self.assertIs(self.player.track, current)
        self.assertIs(self.player.queue, queue)
        self.assertEqual(self.player.index, 0)
        self.assertEqual(self.player.player.source(), source)
        self.assertEqual(self.player.player.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(self.player.status, "catalog_unavailable")
        self.assertIn("当前歌曲继续播放", self.window._error)
        self.assertNotIn("private", self.window._error)
        self.assertTrue(self.window.isVisible())
        self.assertIsNone(self.controller._opening)
        self.assertTrue(self.window._shelves[0].isEnabled())
        self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)
        self.assertFalse(any(call[0] == "visual" for call in self.island.calls))

    def test_canceling_pending_playlist_by_back_edit_or_source_keeps_healthy_audio(self):
        for action in ("back", "edit", "source"):
            with self.subTest(action=action):
                if not self.window.isVisible():
                    self.controller.return_from_music_surface()
                old = OnlineSong("netease", "old-" + action, "old song")
                self.player.play_queue((old,))
                token, _ = self.play_tasks.latest("stream")
                self.play_tasks.completed.emit(token, StreamInfo("netease", old.song_id,
                    "https://example.test/old.mp3", True), None)
                self.window.search.setText("playlist " + action)
                playlist = LibraryCollection("playlist:netease:one", "playlist", "one", "", "one")
                self.window.set_search_results((playlist,))
                current, queue, source = self.player.track, self.player.queue, self.player.player.source()
                self.island.calls.clear()
                self.window._rows[0].cover.click()
                token, catalog_action = self.play_tasks.latest("catalog")
                self.assertIsNotNone(self.controller._opening)
                if action == "back":
                    self.controller.back()
                elif action == "edit":
                    self.window.search.setText("edited query")
                else:
                    self.controller.switch_source()
                self.assertIsNone(self.controller._opening)
                self.assertIs(self.player.track, current)
                self.assertIs(self.player.queue, queue)
                self.assertEqual(self.player.player.source(), source)
                self.assertEqual(self.player.player.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
                self.assertFalse(catalog_action())
                calls_after_cancel = tuple(self.island.calls)
                self.play_tasks.completed.emit(token, (self.song,), None)
                self.assertIs(self.player.track, current)
                self.assertEqual(tuple(self.island.calls), calls_after_cancel)
                self.assertEqual(sum(call[0] == "surface" for call in self.island.calls), 1)


if __name__ == "__main__":
    unittest.main()
