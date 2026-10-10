from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

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

    def daily_recommendations(self, source):
        self.calls.append(("daily", source))
        return (OnlineSong(source, "daily-song", "Daily song", artwork_url="https://example.test/daily.png"),)

    def personal_fm(self, source):
        self.calls.append(("fm", source))
        return (OnlineSong(source, "fm-song", "FM song", artwork_url="https://example.test/fm.png"),)

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

    def play_queue(self, songs):
        self.calls.append(("queue", tuple(songs)))
        if not self.accepted:
            return False
        if self.track is not None and self.emit_old_before_new:
            self.track = replace(self.track, position_seconds=self.track.position_seconds + 1)
            self.changed.emit()
        self.queue, self.index = tuple(songs), 0
        self._epoch += 1
        self.track = replace(track(self.queue[0]), is_playing=True)
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
    def play_playlist(self, source, playlist): return self._catalog("playlist", source, playlist)

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

    def test_open_failure_does_not_invent_account_and_signed_out_returns_to_source_login(self):
        self.service.accounts["qq"] = AccountSnapshot("qq")
        self.open("qq")
        self.assertEqual(self.entry.calls, [("login", "qq")])
        self.assertFalse(self.window.isVisible())
        self.assertFalse(any(call[0] == "playlists" for call in self.service.calls))

    def test_recommendation_covers_arrive_without_blocking_catalog_or_resetting_search(self):
        self.open()
        jobs = [job for job in self.tasks.jobs if job[0][0] == "recommendation_art"]
        self.assertEqual(len(jobs), 2)
        self.assertFalse(self.window._loading)
        self.search("query")
        for job in jobs:
            self.tasks.run(job)
        rows = {row.key: row for row in self.controller._collections}
        self.assertEqual(rows["daily"].artwork_url, "https://example.test/daily.png")
        self.assertEqual(rows["fm"].artwork_url, "https://example.test/fm.png")
        self.assertEqual(self.window.search.text(), "query")
        self.assertEqual(self.window.collections, ())
        self.window.search.clear()
        self.assertEqual(self.window.collections[0].artwork_url, "https://example.test/daily.png")

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

    def test_recommendation_art_from_previous_source_cannot_replace_new_library(self):
        self.open()
        jobs = [job for job in self.tasks.jobs if job[0][0] == "recommendation_art"]
        self.open("qq")
        for token, action in jobs:
            self.assertEqual(action(), ())
            self.tasks.completed.emit(token, self.service.daily_recommendations("netease"), None)
        self.assertEqual(self.window._account.source_id, "qq")
        self.assertEqual(self.controller._collections, ())
        self.assertEqual(self.window.collections, ())

    def test_back_during_song_request_keeps_late_completion_from_opening_surface(self):
        self.open()
        self.window._rows[0].cover.click()
        self.window.backRequested.emit()
        self.assertIsNone(self.controller._opening)
        self.assertNotIn(("visual", "owned:netease:song-1"), self.island.calls)
        self.player.play_queue((self.song,))
        self.assertEqual(self.island.calls, [])
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
        self.assertIn("部分", self.window._error)
        self.assertNotIn("secret", self.window._error)

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
        for mode, fullscreen in (("default", None), ("fullscreen", True), ("window", False), ("island", "island")):
            with self.subTest(mode=mode):
                self.open()
                row = LibraryCollection("song:netease:song-1", "song", self.song.title, "", song=self.song)
                self.window.set_collections((row,))
                origin = QRect(self.window.mapToGlobal(self.window.rect().topLeft()), self.window.size())
                self.controller.play_collection(row, mode, origin)
                self.assertIn(("visual", "owned:netease:song-1"), self.island.calls)
                if mode == "island":
                    self.assertEqual(self.entry.calls[-1], ("island",))
                else:
                    call = next(call for call in reversed(self.island.calls) if call[0] == "surface")
                    self.assertIs(call[1]["fullscreen"], fullscreen)
                    self.assertEqual(call[1]["origin_geometry"], origin)
                    self.assertFalse(call[1]["origin_snapshot"].isNull())

    def test_old_position_during_catalog_wait_cannot_handoff(self):
        self.open()
        self.player.play_queue((self.song,))
        row = next(row for row in self.window.collections if row.kind == "playlist")
        self.controller.play_collection(row, "window", QRect())
        self.player.track = replace(self.player.track, position_seconds=5)
        self.player.changed.emit()
        self.assertEqual(self.island.calls, [])
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

    def test_failed_selection_reenables_real_window_rows_for_retry(self):
        for immediate in (False, True):
            with self.subTest(immediate=immediate):
                self.open()
                self.player.accepted = not immediate
                row = self.window._rows[0]
                row.cover.click()
                if not immediate:
                    self.player._set_status("catalog_unavailable")
                self.assertFalse(self.window._opening)
                self.assertTrue(all(row.isEnabled() for row in self.window._rows))

    def test_user_close_invalidates_late_open_art_and_player_handoff(self):
        self.open()
        row = self.window._rows[0]
        row.cover.click()
        self.window.close()
        self.player.play_queue((self.song,))
        self.assertEqual(self.island.calls, [])
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
        self.assertTrue(self.window._error)
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

    def test_saved_source_auto_enter_uses_account_validation_and_callback(self):
        self.controller.show_login("qq", enter_saved=True)
        self.tasks.run(self.tasks.latest("sources"))
        self.tasks.run(self.tasks.latest("account"))
        self.assertEqual(self.callbacks, ["qq"])
        self.assertFalse(self.window.isVisible())
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
        self.play_tasks, self.play_art_tasks = FakeTasks(), FakeTasks()
        self.player = OwnedMusicPlayer(self.service, LocalLyricsStore(root=Path(self.temp.name)),
            player=SignalingMediaPlayer(), audio=object(), tasks=self.play_tasks, artwork_tasks=self.play_art_tasks)
        self.window, self.island, self.entry = MusicLibraryWindow(), Island(), Entry()
        self.controller = MusicLibraryController(self.settings, self.service, self.player, self.island, self.entry,
            window=self.window, tasks=self.tasks, art_tasks=self.art_tasks)
        self.controller.open_source("netease")
        self.tasks.run(self.tasks.latest("open"))
        self.song = OnlineSong("netease", "new", "new song")
        self.row = LibraryCollection("song:netease:new", "song", self.song.title, "", song=self.song)
        self.window.set_collections((self.row,))

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
        self.assertTrue(self.window.isVisible())
        token, _ = self.play_tasks.latest("stream")
        self.play_tasks.completed.emit(token, StreamInfo("netease", "new", "https://example.test/audio.mp3", True), None)
        self.assertEqual([call for call in self.island.calls if call[0] == "visual"], [("visual", "owned:netease:new")])
        self.assertFalse(self.window.isVisible())

    def test_unavailable_stream_keeps_visible_library_and_releases_controls(self):
        self.window._rows[0].cover.click()
        token, _ = self.play_tasks.latest("stream")
        self.play_tasks.completed.emit(token, StreamInfo("netease", "new"), None)
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.island.calls, [])
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
        self.play_tasks.completed.emit(token, StreamInfo("netease", "new", "https://example.test/audio.mp3", True), None)
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.island.calls, [])
        self.assertFalse(self.window._opening)
        self.assertFalse(self.window._loading)
        self.assertEqual(self.window.collections, self.controller._collections)

    def test_delayed_decoder_failure_does_not_hide_error_in_music_surface(self):
        self.window._rows[0].cover.click()
        self.player.failure_reason = "network"
        self.player._set_status("playback_failed")
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.island.calls, [])
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

    def test_real_owned_catalog_to_rejected_stream_does_not_handoff(self):
        playlist = LibraryCollection("playlist:netease:one", "playlist", "one", "", "one")
        self.window.set_collections((playlist,))
        self.window._rows[0].cover.click()
        token, _ = self.play_tasks.latest("catalog")
        self.play_tasks.accepting = False
        self.play_tasks.completed.emit(token, (self.song,), None)
        self.assertEqual(self.island.calls, [])
        self.assertIsNone(self.controller._opening)
        self.assertFalse(self.window._opening)
        self.assertTrue(self.window._rows[0].isEnabled())


if __name__ == "__main__":
    unittest.main()
