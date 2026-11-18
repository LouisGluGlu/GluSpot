"""Apple Music companion, matching and playlist sync. No real account or Windows host."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from api.apple_music_backend import AppleMusicBackend
from api.apple_music_catalog import (
    SongIdentity, build_preview, classify_song, playlists_from_payload, service_song, song_from_resource,
    songs_from_payload,
)
from api.apple_music_companion import MemoryAppleMusicCompanion, ProcessAppleMusicCompanion, availability
from api.models import ColorToken
from api.online_music import (
    AccountSnapshot, CatalogPage, LoginSnapshot, OnlineMusicError, OnlineMusicService, OnlinePlaylist, OnlineSong,
)
from api.online_music_credentials import ProtectedCredentialStore
from api.online_music_router import RoutingOnlineBackend
from local.owned_music_player import OwnedMusicPlayer
from local.playback_entry_window import PlaybackEntryWindow


PNG_FREE = True


class Protector:
    def protect(self, data):
        return bytes(value ^ 0xA5 for value in data)

    def unprotect(self, data):
        return self.protect(data)


def song(source, song_id, title, artist, duration, album=""):
    return SongIdentity(source, song_id, title, artist, album, duration)


class MatchingTests(unittest.TestCase):
    def test_close_title_artist_and_duration_match(self):
        source = song("netease", "n1", "晴天", "周杰伦", 269)
        row = classify_song(source, [song("apple_music", "a1", "晴天", "周杰伦", 270)])
        self.assertEqual(row.state, "matched")
        self.assertEqual(row.target.song_id, "a1")

    def test_large_duration_gap_is_not_written_as_a_match(self):
        source = song("netease", "n1", "晴天", "周杰伦", 269)
        row = classify_song(source, [song("apple_music", "a1", "晴天", "周杰伦", 30)])
        self.assertEqual(row.state, "unmatched")
        self.assertIsNone(row.target)

    def test_missing_duration_and_two_equal_songs_stay_uncertain(self):
        source = song("netease", "n1", "晴天", "周杰伦", 0)
        missing = classify_song(source, [song("apple_music", "a1", "晴天", "周杰伦", 269)])
        self.assertEqual(missing.state, "uncertain")
        tied = classify_song(
            song("netease", "n1", "晴天", "周杰伦", 269),
            [song("apple_music", "a1", "晴天", "周杰伦", 269), song("apple_music", "a2", "晴天", "周杰伦", 269.4)],
        )
        self.assertEqual(tied.state, "uncertain")
        self.assertIn("两首", tied.reason)

    def test_resource_reader_uses_public_fields_and_drops_blank_rows(self):
        parsed = song_from_resource({
            "id": "a1",
            "attributes": {"name": "晴天", "artistName": "周杰伦", "albumName": "叶惠美", "durationInMillis": 269000},
        })
        self.assertEqual((parsed.song_id, parsed.duration_seconds), ("a1", 269))
        self.assertIsNone(song_from_resource({"id": "", "attributes": {"name": "晴天"}}))
        playlists = playlists_from_payload({"data": [{"id": "p1", "attributes": {"name": "我的", "trackCount": 2}}]})
        self.assertEqual(playlists[0]["name"], "我的")

    def test_library_document_reads_names_from_the_resources_map(self):
        # music.apple.com returns the root folder's children like this: data rows
        # are id/type only, and the name sits under resources. A folder is not a playlist.
        wrapped = {
            "data": {
                "data": [
                    {"id": "p.1", "type": "library-playlists", "href": "/v1/me/library/playlists/p.1"},
                    {"id": "p.fold", "type": "library-playlist-folders"},
                ],
                "resources": {
                    "library-playlists": {
                        "p.1": {"id": "p.1", "type": "library-playlists", "attributes": {"name": "午后", "trackCount": 2}},
                    },
                    "library-playlist-folders": {
                        "p.fold": {"id": "p.fold", "type": "library-playlist-folders", "attributes": {"name": "旅行"}},
                    },
                },
                "meta": {"total": 2},
            }
        }
        self.assertEqual([item["name"] for item in playlists_from_payload(wrapped)], ["午后"])
        self.assertEqual(playlists_from_payload(wrapped["data"])[0]["name"], "午后")
        bare = [{"id": "p.9", "type": "library-playlists", "attributes": {"name": "深夜"}}]
        self.assertEqual(playlists_from_payload(bare)[0]["name"], "深夜")
        tracks = songs_from_payload({
            "data": [{"id": "i.1", "type": "library-songs"}],
            "resources": {"library-songs": {"i.1": {
                "id": "i.1", "type": "library-songs",
                "attributes": {"name": "晴天", "artistName": "周杰伦", "durationInMillis": 269000},
            }}},
        })
        self.assertEqual((tracks[0].title, tracks[0].artist, tracks[0].duration_seconds), ("晴天", "周杰伦", 269.0))

    def test_library_song_uses_catalog_artwork_duration_and_album(self):
        song = song_from_resource({
            "id": "i.1",
            "type": "library-songs",
            "attributes": {
                "name": "晴天",
                "playParams": {"catalogId": "12345"},
            },
        }, resources={"songs": {"12345": {"id": "12345", "type": "songs", "attributes": {
            "name": "晴天",
            "artistName": "周杰伦",
            "albumName": "叶惠美",
            "durationInMillis": 269000,
            "artwork": {"url": "https://example.test/image/{w}x{h}bb.{f}"},
        }}}})
        self.assertEqual((song.artist, song.album, song.duration_seconds, song.catalog_id), ("周杰伦", "叶惠美", 269.0, "12345"))
        self.assertEqual(song.artwork_url, "https://example.test/image/500x500bb.jpg")
        self.assertEqual(service_song(song)["picUrl"], song.artwork_url)


class CompanionServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.companion = MemoryAppleMusicCompanion()
        self.companion.playlists = [{"id": "pl.1", "attributes": {"name": "午后", "trackCount": 1}}]
        self.companion.tracks = {"pl.1": [{
            "id": "a1",
            "attributes": {"name": "晴天", "artistName": "周杰伦", "albumName": "叶惠美", "durationInMillis": 269000},
        }]}
        self.companion.catalog = list(self.companion.tracks["pl.1"])
        backend = AppleMusicBackend(self.companion)
        store = ProtectedCredentialStore(Path(self.folder.name), Protector())
        self.service = OnlineMusicService(backend, store, clock=lambda: 1000.0)
        self.addCleanup(self.service.close)

    def test_window_login_library_search_and_transport_do_not_expose_a_stream(self):
        self.assertIn("不支持", availability()[1])
        snapshot = self.service.begin_login("apple_music")
        self.assertEqual(snapshot.login_kind, "window")
        self.assertEqual(snapshot.qr_image_bytes, b"")
        self.assertIn("登录窗口", snapshot.message)
        waiting = self.service.poll_login(snapshot.session_id)
        self.assertEqual(waiting.state, "waiting")
        self.companion.authorized = True
        self.companion.user_id = "abc123"
        confirmed = self.service.poll_login(snapshot.session_id)
        self.assertTrue(confirmed.account.logged_in)
        self.assertTrue(self.service.has_login_record("apple_music"))
        self.assertNotIn("token", json.dumps(self.service._credentials["apple_music"]))
        playlists = self.service.playlists("apple_music")
        self.assertEqual(playlists.items[0].title, "午后")
        tracks = self.service.playlist_tracks("apple_music", "pl.1")
        self.assertEqual((tracks.items[0].title, tracks.items[0].duration_seconds), ("晴天", 269))
        found = self.service.search("apple_music", "晴天 周杰伦")
        self.assertEqual(found.items[0].song_id, "a1")
        stream = self.service.stream_url("apple_music", "a1")
        self.assertFalse(stream.playable)
        with self.assertRaises(OnlineMusicError):
            self.service.companion_transport("apple_music", "play", {"song_id": "a1", "shuffle": True})
        body = self.service.companion_transport("apple_music", "play", {"song_id": "a1"})
        self.assertEqual(body["song_id"], "a1")
        self.assertNotIn("shuffle", self.companion.calls[-1][1])
        self.service.logout("apple_music")
        self.assertFalse(self.service.account_status("apple_music").logged_in)

    def test_featured_recommended_and_playlist_search_use_their_own_shelves(self):
        self.companion.authorized = True
        self.companion.user_id = "abc123"
        self.service.account_status("apple_music", refresh=True)
        mix = {"id": "pl.mix", "type": "playlists", "attributes": {"name": "为你推荐的歌", "artwork": {"url": "https://example.test/{w}x{h}.jpg"}}}
        chart = {"id": "pl.top", "type": "playlists", "attributes": {"name": "热门歌单"}}
        self.companion.responses["for_you_playlists"] = {"items": [mix]}
        self.companion.responses["chart_playlists"] = {"items": [chart, mix]}
        self.companion.responses["find_playlists"] = {"items": [chart]}
        mine = self.service.recommended_playlists("apple_music", shelf="for_you")
        self.assertEqual([(p.playlist_id, p.title) for p in mine.items], [("pl.mix", "为你推荐的歌")])
        self.assertTrue(mine.items[0].artwork_url.startswith("https://"))
        shelf = self.service.recommended_playlists("apple_music", limit=1)
        self.assertEqual([p.playlist_id for p in shelf.items], ["pl.top"])
        self.assertTrue(shelf.has_more)
        self.assertEqual([p.playlist_id for p in self.service.recommended_playlists("apple_music", offset=1).items], ["pl.mix"])
        found = self.service.search("apple_music", "热门", kind="playlists")
        self.assertEqual([p.playlist_id for p in found.items], ["pl.top"])
        self.assertIn(("find_playlists", {"query": "热门", "limit": 30, "offset": 0}), self.companion.calls)
        songs = self.service.search("apple_music", "晴天", kind="songs")
        self.assertEqual(songs.items[0].song_id, "a1")
        with self.assertRaises(OnlineMusicError):
            self.service.recommended_playlists("apple_music", shelf="daily")

    def test_router_keeps_other_sources_on_their_backend(self):
        node = _NodeBackend()
        router = RoutingOnlineBackend(node, AppleMusicBackend(self.companion))
        self.assertTrue(router.available("apple_music")[0])
        router.request("netease", "search", {"query": "晴天"})
        self.assertEqual(node.calls[0][0], "netease")
        account = router.request("apple_music", "account_status", {})
        self.assertFalse(account["data"]["logged_in"])

    def test_process_client_rejects_playback_mode_and_reads_one_json_line(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        script = Path(folder.name) / "companion"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json,sys\n"
            "request=json.loads(sys.stdin.readline())\n"
            "if request.get('op')!='account':\n"
            "    raise SystemExit(2)\n"
            "sys.stdout.write(json.dumps({'v':1,'id':request['id'],'ok':True,'body':{'logged_in':False}})+'\\n')\n"
            "sys.stdout.flush()\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        client = ProcessAppleMusicCompanion(script, timeout=5)
        self.addCleanup(client.close)
        self.assertEqual(client.request("account")["logged_in"], False)
        with self.assertRaises(OnlineMusicError):
            client.request("transport", {"action": "play", "shuffle": True})

    def test_reopen_without_a_saved_record_still_asks_for_the_account(self):
        signed_out = self.service.account_status("apple_music", refresh=True)
        self.assertFalse(signed_out.logged_in)
        self.assertEqual(self.companion.calls[-1][0], "account")
        self.companion.authorized = True
        self.companion.user_id = "abc123"
        connected = self.service.account_status("apple_music", refresh=True)
        self.assertTrue(connected.logged_in)
        self.assertEqual([call[0] for call in self.companion.calls if call[0] == "account"], ["account", "account"])
        self.assertNotIn("token", json.dumps(self.service._credentials["apple_music"]))

    def test_simulated_library_payload_fills_playlists_and_does_not_invent_another_page(self):
        document = {
            "data": [
                {"id": "p.1", "type": "library-playlists"},
                {"id": "p.fold", "type": "library-playlist-folders"},
            ],
            "resources": {
                "library-playlists": {
                    "p.1": {"id": "p.1", "type": "library-playlists", "attributes": {"name": "午后"}},
                },
                "library-playlist-folders": {
                    "p.fold": {"id": "p.fold", "type": "library-playlist-folders", "attributes": {"name": "旅行"}},
                },
            },
            "next": "/v1/me/library/playlist-folders/p.playlistsroot/children?offset=25",
        }
        self.companion.authorized = True
        self.companion.user_id = "abc123"
        self.companion.responses["library_playlists"] = {"payload": {"data": document}, "more": True}
        page = self.service.playlists("apple_music", limit=2)
        self.assertEqual([item.title for item in page.items], ["午后"])
        self.assertNotIn("旅行", [item.title for item in page.items])
        self.assertTrue(page.has_more)
        self.companion.responses["library_playlists"] = {"payload": document, "more": False}
        full = self.service.playlists("apple_music", limit=1)
        self.assertEqual(full.items[0].title, "午后")
        self.assertFalse(full.has_more)
        self.companion.responses["library_tracks"] = {"payload": {
            "data": [{"id": "i.1", "type": "library-songs"}],
            "resources": {"library-songs": {"i.1": {
                "id": "i.1", "type": "library-songs",
                "attributes": {"name": "晴天", "artistName": "周杰伦", "durationInMillis": 269000},
            }}},
        }, "more": False}
        tracks = self.service.playlist_tracks("apple_music", "p.1", limit=1)
        self.assertEqual((tracks.items[0].title, tracks.has_more), ("晴天", False))
        ttml = (
            '<tt xmlns="http://www.w3.org/ns/ttml"><body><p begin="0s" end="2s">'
            '<span begin="0s" end="0.5s">晴</span><span begin="0.5s" end="1s">天</span>'
            "</p></body></tt>"
        )
        self.companion.lyrics["12345"] = ttml
        self.assertIn("<span", self.service.apple_timed_lyrics("i.1", "12345"))
        from api.ttml import parse_ttml
        document = parse_ttml(ttml)
        self.assertTrue(document.originals[0].units)

    def test_catalog_artwork_reaches_the_song_and_timed_lyrics_keep_words(self):
        from api.lyrics import LocalLyricsStore
        from api.online_music import OnlineSong
        from local.owned_music_player import OwnedMusicPlayer

        self.companion.authorized = True
        self.companion.user_id = "abc123"
        self.companion.responses["library_tracks"] = {"payload": {
            "data": [{"id": "i.9", "type": "library-songs", "attributes": {
                "name": "晴天", "playParams": {"catalogId": "12345"},
            }}],
            "resources": {"songs": {"12345": {"id": "12345", "type": "songs", "attributes": {
                "artistName": "周杰伦",
                "albumName": "叶惠美",
                "durationInMillis": 269000,
                "artwork": {"url": "https://example.test/image/{w}x{h}bb.{f}"},
            }}}},
        }, "artwork_lookup": "ok", "more": False}
        page = self.service.playlist_tracks("apple_music", "p.1")
        song = page.items[0]
        self.assertEqual(song.artwork_url, "https://example.test/image/500x500bb.jpg")
        self.assertEqual((song.artist, song.album, song.duration_seconds, song.catalog_id), ("周杰伦", "叶惠美", 269.0, "12345"))
        ttml = (
            '<tt xmlns="http://www.w3.org/ns/ttml"><body><p begin="0s" end="2s">'
            '<span begin="0s" end="0.5s">晴</span><span begin="0.5s" end="1s">天</span>'
            "</p></body></tt>"
        )
        self.companion.lyrics["12345"] = ttml
        store = LocalLyricsStore(root=Path(self.folder.name) / "lyrics")
        player = type("Player", (), {
            "service": self.service,
            "lyrics_store": store,
            "_parse_lyrics_payload": OwnedMusicPlayer._parse_lyrics_payload,
            **{name: getattr(OwnedMusicPlayer, name) for name in (
                "_apple_lyric_cache_key", "_apple_lyric_cache_get", "_apple_lyric_cache_put",
                "APPLE_LYRIC_CACHE_LIMIT", "APPLE_LYRIC_MISS_SECONDS")},
        })()
        payload, match = OwnedMusicPlayer._fetch_apple_lyrics(player, song, {})
        self.assertEqual(payload.source, "apple-ttml")
        self.assertGreaterEqual(len(match.timed_lyrics[0].units), 2)
        bare = OnlineSong("apple_music", "library-row", "晴天")
        calls = []
        player.lyrics_store = type("Store", (), {
            "resolve": lambda *args, **kwargs: calls.append(args) or None,
        })()
        with self.assertLogs("GluSpot.apple", level="INFO") as logs:
            fallback, missed = OwnedMusicPlayer._fetch_apple_lyrics(player, bare, {})
        text = "\n".join(logs.output)
        self.assertEqual(fallback.source, "apple-title-search")
        self.assertIsNone(missed)
        self.assertIn("stage=ttml result=miss reason=empty", text)
        self.assertIn("stage=title-search result=miss reason=no-artist", text)
        self.assertIn("stage=title-search result=miss reason=no-duration", text)
        self.assertIn("stage=title-search result=miss count=0 reason=no-match", text)
        self.assertTrue(calls)
        self.assertNotIn("token", text.casefold())


class _NodeBackend:
    def __init__(self):
        self.calls = []
        self.closed = False

    def available(self, source_id):
        return source_id == "netease", ""

    def request(self, source_id, operation, params=None, credential=None):
        self.calls.append((source_id, operation, params))
        return {"data": {}}

    def close(self):
        self.closed = True


class AppleSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_login_window_copy_and_no_sync_button_on_login_page(self):
        window = PlaybackEntryWindow()
        self.addCleanup(window.close)
        window.show_sources()
        from local.playback_entry_window import ENTRY_SOURCES
        window.set_sources(tuple(type("Source", (), {"source_id": item.source_id, "available": item.login_supported, "reason": ""})()
                                 for item in ENTRY_SOURCES))
        window.select_source("apple_music")
        window._expansion.stop()
        window._expanded = 1
        window.set_login_snapshot(LoginSnapshot(
            "session", "apple_music", "waiting", message="请在弹出的 Apple 登录窗口中登录", login_kind="window",
        ))
        self.assertNotIn("二维码", window.message_label.text())
        self.assertEqual(window.refresh_button.text(), "重新打开登录窗口")
        window.set_account(AccountSnapshot("apple_music", True, "user", "Apple", state="verified"))
        window.resize(960, 540)
        window.show()
        window._layout_children()
        self.assertEqual(window.source_buttons["apple_music"].connection_status, "已连接")
        # 6.52: playlist sync opens from settings and the library, never from the login page.
        self.assertFalse(window.playlist_sync_button.isVisible())
        self.assertLess(window.enter_button.geometry().right(), window.account_logout_button.geometry().left())

    def test_owned_player_uses_companion_play_and_keeps_playback_mode_local(self):
        from api.lyrics import LocalLyricsStore
        from PySide6.QtCore import QObject, Signal
        from PySide6.QtMultimedia import QMediaPlayer

        class Tasks(QObject):
            completed = Signal(object, object, object)
            def __init__(self):
                super().__init__()
                self.jobs = []
            def submit(self, token, action):
                self.jobs.append((token, action))
                return True
            def close(self):
                self.jobs.clear()

        class Player(QObject):
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
                self.playbackStateChanged.emit(self.state)
            def setSource(self, _url): pass
            def play(self): pass
            def pause(self): pass
            def playbackState(self): return self.state
            def isSeekable(self): return False
            def setPosition(self, _value): pass

        calls = []
        service = type("Service", (), {})()
        def transport(_source, action, params=None):
            calls.append((action, dict(params or {})))
            if action == "play":
                return {"playing": True, "position": 0, "duration": 200, "song_id": params["song_id"], "preview": False}
            return {"playing": action == "resume", "position": 1, "duration": 200, "song_id": "a1", "preview": False}
        service.companion_transport = transport
        service.stream_url = lambda *_args: (_ for _ in ()).throw(AssertionError("apple audio must stay on the page"))
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        tasks = Tasks()
        lyric_tasks = Tasks()
        player = OwnedMusicPlayer(service, LocalLyricsStore(root=Path(folder.name)), player=Player(), audio=object(),
                                  dj_player=Player(), dj_audio=object(), tasks=tasks, artwork_tasks=Tasks(),
                                  prefetch_tasks=Tasks(), lyric_tasks=lyric_tasks, stream_cache=None)
        self.addCleanup(player.close)
        player.queue = (
            OnlineSong("apple_music", "a1", "晴天", "周杰伦", duration_seconds=269),
            OnlineSong("apple_music", "a2", "七里香", "周杰伦", duration_seconds=300),
        )
        player.index = 0
        self.assertTrue(player._load_song())
        self.assertFalse(any(job[0][0] == "lyrics" for job in lyric_tasks.jobs))
        player.playback_mode = "shuffle"
        token, action = next(job for job in tasks.jobs if job[0][0] == "companion")
        tasks.completed.emit(token, action(), None)
        self.assertTrue(any(job[0][0] == "lyrics" for job in lyric_tasks.jobs))
        self.assertEqual(calls[0][0], "play")
        self.assertEqual(calls[0][1]["song_id"], "a1")
        self.assertNotIn("shuffle", calls[0][1])
        self.assertEqual(player.playback_mode, "shuffle")
        self.assertEqual(player.status, "playing")
        self.assertIn("apple_music", player.track.source_app)
        self.assertTrue(player.track.is_playing)
        player.playback_mode = "sequential"
        with self.assertLogs("GluSpot.apple", level="INFO") as logs:
            player.send_playback_command("next")
        skip_log = "\n".join(logs.output)
        self.assertIn("operation=skip", skip_log)
        self.assertIn("action=next", skip_log)
        self.assertNotIn("a1", skip_log)
        self.assertNotIn("a2", skip_log)
        next_job = next(job for job in reversed(tasks.jobs) if job[0][0] == "companion")
        tasks.completed.emit(next_job[0], next_job[1](), None)
        self.assertEqual(calls[-1][0], "play")
        self.assertEqual(calls[-1][1].get("song_id"), "a2")
        self.assertTrue(all(call[0] in {"play", "pause", "resume", "seek", "status"} for call in calls))
        calls.clear()
        self.assertTrue(player._load_song(autoplay=False))
        self.assertEqual(calls, [])
        self.assertFalse(player.track.is_playing)
        self.assertTrue(player._toggle_companion())
        paused = next(job for job in reversed(tasks.jobs) if job[0][0] == "companion_pause")
        paused[1]()
        self.assertEqual(calls[-1][0], "play")
        self.assertEqual(calls[-1][1].get("song_id"), "a2")


    def test_companion_song_end_advances_the_queue_once_but_a_pause_does_not(self):
        from api.lyrics import LocalLyricsStore
        from PySide6.QtCore import QObject, Signal
        from PySide6.QtMultimedia import QMediaPlayer

        class Tasks(QObject):
            completed = Signal(object, object, object)
            def __init__(self):
                super().__init__()
                self.jobs = []
            def submit(self, token, action):
                self.jobs.append((token, action))
                return True
            def close(self):
                self.jobs.clear()

        class Player(QObject):
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
                self.playbackStateChanged.emit(self.state)
            def setSource(self, _url): pass
            def play(self): pass
            def pause(self): pass
            def playbackState(self): return self.state
            def isSeekable(self): return False
            def setPosition(self, _value): pass

        plays = []
        service = type("Service", (), {})()
        def transport(_source, action, params=None):
            if action == "play":
                plays.append(params["song_id"])
                return {"playing": True, "position": 0, "duration": 200, "song_id": params["song_id"], "preview": False}
            return {"playing": False, "position": 0, "duration": 200, "song_id": "", "preview": False}
        service.companion_transport = transport
        service.stream_url = lambda *_args: (_ for _ in ()).throw(AssertionError("apple audio must stay on the page"))
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        tasks = Tasks()
        player = OwnedMusicPlayer(service, LocalLyricsStore(root=Path(folder.name)), player=Player(), audio=object(),
                                  dj_player=Player(), dj_audio=object(), tasks=tasks, artwork_tasks=Tasks(),
                                  prefetch_tasks=Tasks(), lyric_tasks=Tasks(), stream_cache=None)
        self.addCleanup(player.close)
        player.queue = (
            OnlineSong("apple_music", "a1", "晴天", "周杰伦", duration_seconds=200),
            OnlineSong("apple_music", "a2", "七里香", "周杰伦", duration_seconds=300),
        )
        player.index = 0
        player.playback_mode = "sequential"
        self.assertTrue(player._load_song())
        token, action = next(job for job in tasks.jobs if job[0][0] == "companion")
        tasks.completed.emit(token, action(), None)
        epoch = player._epoch
        # A pause in the middle of the song is not an end.
        player._companion_status_ready(epoch, {"playing": True, "position": 80.0, "duration": 200}, None)
        player._companion_status_ready(epoch, {"playing": False, "position": 81.0, "duration": 200}, None)
        self.assertEqual(player.index, 0)
        # The page finished the song: GluSpot moves to the next one, once.
        player._companion_status_ready(epoch, {"playing": False, "position": 199.4, "duration": 200}, None)
        self.assertEqual(player.index, 1)
        player._companion_status_ready(epoch, {"playing": False, "position": 0.0, "duration": 200, "ended": True}, None)
        self.assertEqual(player.index, 1)
        next_job = next(job for job in reversed(tasks.jobs) if job[0][0] == "companion")
        tasks.completed.emit(next_job[0], next_job[1](), None)
        self.assertEqual(plays, ["a1", "a2"])
        # An explicit "ended" from MusicKit also advances (repeat-one replays).
        player.playback_mode = "repeat_one"
        second = player._epoch
        player._companion_status_ready(second, {"playing": True, "position": 30.0, "duration": 300}, None)
        player._companion_status_ready(second, {"playing": False, "position": 0.0, "duration": 300, "ended": True}, None)
        replay = next(job for job in reversed(tasks.jobs) if job[0][0] == "companion")
        tasks.completed.emit(replay[0], replay[1](), None)
        self.assertEqual(plays, ["a1", "a2", "a2"])


    def test_next_songs_lyrics_are_fetched_while_this_one_plays(self):
        from api.lyrics import LocalLyricsStore
        from PySide6.QtCore import QObject, Signal
        from PySide6.QtMultimedia import QMediaPlayer

        class Tasks(QObject):
            completed = Signal(object, object, object)
            def __init__(self):
                super().__init__()
                self.jobs = []
            def submit(self, token, action):
                self.jobs.append((token, action))
                return True
            def close(self):
                self.jobs.clear()

        class Player(QObject):
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
                self.playbackStateChanged.emit(self.state)
            def setSource(self, _url): pass
            def play(self): pass
            def pause(self): pass
            def playbackState(self): return self.state
            def isSeekable(self): return False
            def setPosition(self, _value): pass

        plays = []
        service = type("Service", (), {})()
        def transport(_source, action, params=None):
            if action == "play":
                plays.append(params["song_id"])
                return {"playing": True, "position": 0, "duration": 200, "song_id": params["song_id"], "preview": False}
            return {"playing": False, "position": 0, "duration": 200, "song_id": "", "preview": False}
        service.companion_transport = transport
        reads = []
        ttml = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><p begin="0s" end="2s">'
                '<span begin="0s" end="1s">七</span><span begin="1s" end="2s">里</span></p></body></tt>')
        service.apple_timed_lyrics = lambda song_id, catalog_id="": reads.append(song_id) or (ttml if song_id == "a2" else "")
        service.stream_url = lambda *_args: (_ for _ in ()).throw(AssertionError("apple audio must stay on the page"))
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        tasks = Tasks()
        lyric_tasks = Tasks()
        player = OwnedMusicPlayer(service, LocalLyricsStore(root=Path(folder.name)), player=Player(), audio=object(),
                                  dj_player=Player(), dj_audio=object(), tasks=tasks, artwork_tasks=Tasks(),
                                  prefetch_tasks=Tasks(), lyric_tasks=lyric_tasks, stream_cache=None)
        self.addCleanup(player.close)
        player.queue = (
            OnlineSong("apple_music", "a1", "晴天", "周杰伦", duration_seconds=200),
            OnlineSong("apple_music", "a2", "七里香", "周杰伦", duration_seconds=300),
        )
        player.index = 0
        player.playback_mode = "sequential"
        self.assertTrue(player._load_song())
        token, action = next(job for job in tasks.jobs if job[0][0] == "companion")
        tasks.completed.emit(token, action(), None)
        prefetch = [job for job in lyric_tasks.jobs if job[0][0] == "apple_lyric_prefetch"]
        self.assertEqual([job[0][2] for job in prefetch], ["a2"])
        self.assertTrue(prefetch[0][1]())
        self.assertEqual(reads, ["a2"])
        # Next: the skipped-to song reads its lyrics from the cache at once.
        with self.assertLogs("GluSpot.apple", level="INFO") as logs:
            payload, match = player._fetch_apple_lyrics(player.queue[1], {})
        self.assertEqual(reads, ["a2"])
        self.assertEqual(payload.source, "apple-ttml")
        self.assertTrue(match.timed_lyrics)
        self.assertIn("stage=ttml result=cached", " ".join(logs.output))


class AppleLogTests(unittest.TestCase):
    def test_log_keeps_secrets_and_titles_out(self):
        from api.apple_music_log import apple_log
        with self.assertLogs("GluSpot.apple", level="INFO") as logs:
            apple_log("login", stage="begin", result="ok", token="sekret", cookie="a=b", password="hidden")
            apple_log("search", query="晴天 周杰伦", count=2)
            apple_log("not-a-step", result="ok")
            from api.apple_music_log import apple_note
            apple_note("event=login-window-closed token=sekret cookie=a password=hidden")
        blob = "\n".join(logs.output)
        self.assertIn("operation=login stage=begin result=ok", blob)
        self.assertIn("operation=search count=2", blob)
        self.assertIn("operation=companion event=login-window-closed", blob)
        self.assertNotIn("sekret", blob)
        self.assertNotIn("cookie", blob.lower())
        self.assertNotIn("password", blob.lower())
        self.assertNotIn("token", blob.lower())
        self.assertNotIn("晴天", blob)
        self.assertEqual(blob.count("operation="), 3)

    def test_backend_logs_steps_without_ids(self):
        companion = MemoryAppleMusicCompanion()
        companion.authorized = True
        companion.user_id = "user-marker-secret"
        companion.playlists = [{"id": "p1", "attributes": {"name": "午后", "trackCount": 1}}]
        companion.catalog = [{"id": "a9", "attributes": {"name": "晴天", "artistName": "周杰伦", "durationInMillis": 269000}}]
        backend = AppleMusicBackend(companion)
        with self.assertLogs("GluSpot.apple", level="INFO") as logs:
            backend.request("apple_music", "begin_login", {})
            backend.request("apple_music", "poll_login", {})
            backend.request("apple_music", "playlists", {})
            backend.request("apple_music", "search", {"query": "晴天 周杰伦"})
            backend.request("apple_music", "companion_transport", {"action": "status"})
            backend.request("apple_music", "companion_transport", {"action": "play", "song_id": "a9"})
        blob = "\n".join(logs.output)
        self.assertIn("operation=login stage=begin result=ok", blob)
        self.assertIn("operation=login stage=confirmed result=ok", blob)
        self.assertIn("operation=playlists stage=list result=ok count=1", blob)
        self.assertIn("operation=search result=ok count=1", blob)
        self.assertIn("operation=play action=play result=ok preview=no", blob)
        self.assertEqual(blob.count("operation=play "), 1)
        self.assertNotIn("user-marker-secret", blob)
        self.assertNotIn("午后", blob)
        self.assertNotIn("晴天", blob)
        self.assertNotIn("a9", blob)

    def test_protocol_trace_keeps_only_id_op_result_and_duration(self):
        from api.apple_music_log import apple_trace
        with self.assertLogs("GluSpot.apple", level="INFO") as logs:
            apple_trace("send", "abc12345", "playlists")
            apple_trace("reply", "abc12345", "playlists", result="timeout", ms=20)
            apple_trace("send", "not hex", "playlists")
            apple_trace("send", "abc12345", "playlists", result="token=sekret")
        blob = "\n".join(logs.output)
        self.assertIn("operation=protocol stage=send id=abc12345 op=playlists ms=0", blob)
        self.assertIn("operation=protocol stage=reply id=abc12345 op=playlists result=timeout ms=20", blob)
        self.assertNotIn("sekret", blob)
        self.assertNotIn("not hex", blob)
        self.assertEqual(blob.count("operation=protocol"), 2)


class PageBridgeContractTests(unittest.TestCase):
    def test_page_script_does_not_return_tokens_or_change_playback_mode(self):
        root = Path(__file__).resolve().parents[1] / "native" / "apple_music_companion"
        script = (root / "page_bridge.js").read_text(encoding="utf-8")
        host = (root / "Program.cs").read_text(encoding="utf-8")
        self.assertIn("SHA-256", script)
        self.assertIn("/v1/me/library/playlists", script)
        self.assertIn("p.playlistsroot", script)
        self.assertIn("gluspotAppleLibrary", script)
        self.assertNotIn(".data.data", script)
        self.assertIn("/v1/catalog/", script)
        self.assertIn("/v1/me/storefront", script)
        self.assertNotIn("shuffleMode", script)
        self.assertNotIn("repeatMode", script)
        self.assertNotIn('"musicUserToken"', script)
        self.assertNotIn("developerToken", script)
        self.assertNotIn("NonPublic", host)
        self.assertNotIn("GetField", host)
        self.assertIn("music.apple.com", host)
        self.assertIn("apple-music-companion.log", host)
        self.assertIn("Environment.Exit", host)
        self.assertIn("(redacted)", host)
        self.assertLess(host.find('CompanionLog.Write("started")'), host.find("Application.Run"))
        self.assertIn("NavigationCompleted", host)
        self.assertIn("ProcessFailed", host)
        self.assertIn("navigation-failed", host)
        self.assertIn("page_error", host)
        self.assertIn("event=login-window-closed", host)
        self.assertIn("queued", host)
        # Every unanswered request is kept by id: a status poll must not
        # overwrite a slower lyric or playlist read and drop its answer.
        self.assertIn("pendingOrder", host)
        self.assertNotIn("pendingId = id", host)
        reload_ops = host[host.find("ReloadOnTimeout = "):host.find("ReloadOnTimeout = ") + 160]
        self.assertIn('"transport"', reload_ops)
        for data_op in ("song_lyrics", "library_playlists", "library_tracks", "find_songs"):
            self.assertNotIn(data_op, reload_ops)
        self.assertIn("RequeuePending", host)
        self.assertLess(host.find("if (!PageReady())"), host.find("PostWebMessageAsJson"))
        self.assertIn("musicKitInstance", script)
        self.assertLess(script.find("musicKitInstance"), script.find('op: "ready"'))
        self.assertIn("setTimeout(pump, 250)", script)
        self.assertIn('op: "ready"', script)
        self.assertIn('op: "page_error"', script)
        self.assertNotIn("ExecuteScript", host)
        self.assertIn("GetStdHandle", host)
        self.assertIn("System.Threading.Timer", host)
        self.assertIn("trace stage=stdin", host)
        self.assertIn("trace stage=dispatch", host)
        self.assertIn("trace stage=inject", host)
        self.assertIn("trace stage=post", host)
        self.assertIn("trace stage=stdout", host)
        self.assertIn("query-timeout reload", host)
        exit_after = host.find("private void ExitAfterParent")
        self.assertIn("parent-closed", host[exit_after:exit_after + 400])
        self.assertIn("Environment.Exit(0)", host[exit_after:exit_after + 400])
        self.assertIn('addEventListener("error"', script)
        self.assertIn("console.error", script)
        client_source = Path(__file__).resolve().parents[1].joinpath("api", "apple_music_companion.py").read_text(encoding="utf-8")
        self.assertNotIn("DEVNULL", client_source)
        self.assertIn("stderr=subprocess.PIPE", client_source)
        self.assertIn("wait(timeout=1.5)", client_source)
        self.assertNotIn("GLUSPOT_APPLE_MUSIC_COMPANION", client_source)
        self.assertIn('"bin" / "Release" / "net48"', client_source)
        self.assertIn("sys.executable", client_source)
        startup, marker, playback = host.partition("private void ShowPlayback")
        self.assertEqual(marker, "private void ShowPlayback")
        self.assertNotIn("-32000", startup)
        self.assertIn("-32000", playback)
        project = (root / "GluSpotAppleMusicCompanion.csproj").read_text(encoding="utf-8")
        self.assertIn("<PlatformTarget>x64</PlatformTarget>", project)
        self.assertIn("<Prefer32Bit>false</Prefer32Bit>", project)
        self.assertIn("<WebView2LoaderPreference>x64</WebView2LoaderPreference>", project)
        script_path = root / "accept.ps1"
        raw = script_path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        checker = raw.decode("utf-8-sig")
        self.assertTrue(checker.isascii())
        for step in ("FAIL step=", "reason=", '"webview2"', '"build"', '"start"'):
            self.assertIn(step, checker)
        self.assertIn("GluSpotAppleMusicCompanion.csproj", checker)
        self.assertIn("dotnet", checker)
        self.assertIn("NOTE step=login", checker)
        self.assertIn("started", checker)
        lowered = checker.lower()
        self.assertNotIn("musicusertoken", lowered)
        self.assertNotIn("developertoken", lowered)
        self.assertNotIn("password", lowered)
        self.assertNotIn("cookie", lowered)

    def test_library_writes_are_real_posts_and_refusals_throw(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node unavailable")
        script = Path(__file__).resolve().parents[1] / "native" / "apple_music_companion" / "page_bridge.js"
        program = r"""
const lib = require(process.argv[1]);
(async () => {
const calls = [];
const mk = {api: {music: async (path, query, options) => { calls.push({path, query, options}); return {data: [{id: "p.new", type: "library-playlists", attributes: {name: "歌单"}}]}; }}};
const done = await lib.writeLibrary(mk, "/v1/me/library/playlists", {attributes: {name: "歌单"}});
const call = calls[0];
if (call.options.method !== "POST") throw new Error("not a POST");
if (call.options.fetchOptions) throw new Error("fetchOptions is ignored by this MusicKit build");
if (call.options.body.attributes.name !== "歌单") throw new Error("body");
if (Object.keys(call.query).length) throw new Error("body leaked into the query");
if (lib.libraryRows(done.result)[0].id !== "p.new") throw new Error("created id");
const refused = {api: {music: async () => ({status: 403})}};
let threw = false;
try { await lib.writeLibrary(refused, "/v1/me/library/playlists/p.1/tracks", {data: []}); } catch (error) { threw = /403/.test(error.message); }
if (!threw) throw new Error("a refused write must throw");
console.log("ok");
})().catch(error => { console.error(error.message); process.exit(1); });
"""
        completed = subprocess.run([node, "-e", program, str(script)], capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("ok", completed.stdout)

    def test_page_script_loads_a_simulated_library_response(self):
        node = shutil.which("node")
        self.assertIsNotNone(node)
        script = Path(__file__).resolve().parents[1] / "native" / "apple_music_companion" / "page_bridge.js"
        program = r"""
const lib = require(process.argv[1]);
(async () => {
const playlist = (id, name) => ({id, type: "library-playlists", attributes: {name}});
const sparse = (id, type) => ({id, type, href: "/v1/me/library/" + type + "/" + id});
const root = {
  data: {
    data: [sparse("p.1", "library-playlists"), sparse("p.fold", "library-playlist-folders")],
    resources: {
      "library-playlists": {"p.1": playlist("p.1", "午后")},
      "library-playlist-folders": {"p.fold": {id: "p.fold", type: "library-playlist-folders", attributes: {name: "旅行"}}}
    },
    next: "/v1/me/library/playlist-folders/p.playlistsroot/children?offset=2",
    meta: {total: 3}
  }
};
const second = {data: [playlist("p.2", "深夜")]};
const folder = {data: {data: [sparse("p.3", "library-playlists")], resources: {"library-playlists": {"p.3": playlist("p.3", "窗边")}}}};
const calls = [];
const page = await lib.collectPlaylists(async (path, query) => {
  calls.push({path, query});
  if (query.limit != null) throw new Error("limit");
  if (path.indexOf("p.fold") >= 0) return folder;
  if (path === lib.rootPath && query.offset === 0) return root;
  if (path === lib.rootPath && query.offset === 2) return second;
  throw new Error("unexpected " + path);
}, {limit: 100, offset: 0});
if (page.items.map(row => row.attributes.name).join(",") !== "午后,窗边,深夜") {
  throw new Error("names " + page.items.map(row => row.attributes && row.attributes.name).join(","));
}
if (page.items.some(row => row.type === "library-playlist-folders")) throw new Error("folder listed");
if (page.more !== false) throw new Error("more");
if (calls.some(call => String(call.path).indexOf("offset=") >= 0)) throw new Error("followed next url");
const oldNames = (root.data && root.data.data && root.data.data[0].attributes && root.data.data[0].attributes.name) || "";
if (oldNames) throw new Error("sparse row should not carry the name");
const fallbackCalls = [];
const fallback = await lib.collectPlaylists(async (path, query) => {
  fallbackCalls.push(path);
  if (path === lib.rootPath) throw new Error("missing");
  if (query.limit != null) throw new Error("limit");
  return {data: [playlist("p.9", "备用")]};
}, {limit: 25, offset: 0});
if (fallback.items[0].attributes.name !== "备用") throw new Error("fallback");
if (!fallbackCalls.includes(lib.playlistPath)) throw new Error("no fallback path");
const repeated = await lib.collectPlaylists(async (path, query) => {
  if (query.limit != null) throw new Error("limit");
  return {data: [playlist("p.1", "午后")], next: path + "?offset=" + (Number(query.offset || 0) + 1)};
}, {limit: 20, offset: 0});
if (repeated.items.length !== 1) throw new Error("repeat " + repeated.items.length);
const songs = lib.songRows({data: {results: {songs: {data: [playlist("s.1", "晴天")]}}}});
if (!songs.length || songs[0].attributes.name !== "晴天") throw new Error("search");
const direct = lib.songRows({results: {songs: {data: [playlist("s.2", "七里香")]}}});
if (direct[0].attributes.name !== "七里香") throw new Error("direct search");
const merged = lib.mergeCatalogSong(
  {id:"i.1", type:"library-songs", attributes:{name:"晴天", playParams:{catalogId:"9"}}},
  {id:"9", attributes:{artistName:"周杰伦", albumName:"叶惠美", durationInMillis:269000, artwork:{url:"https://example.test/{w}"}}}
);
if (!merged.attributes.artistName || merged.attributes.artistName !== "周杰伦") throw new Error("merge");
if (lib.extractTtml({data:[{attributes:{ttml:"<tt>line</tt>"}}]}).indexOf("<tt>") < 0) throw new Error("ttml");
const filled = await lib.attachCatalog(async function (path) {
  if (path.indexOf("/v1/catalog/cn/songs") !== 0) throw new Error(path);
  return {data: [{id: "9", type: "songs", attributes: {artistName: "周杰伦", albumName: "叶惠美", durationInMillis: 269000, artwork: {url: "https://example.test/{w}x{h}.{f}"}}}]};
}, "cn", [{id: "i.1", type: "library-songs", attributes: {name: "晴天", playParams: {catalogId: "9"}}}]);
if (filled.lookup !== "ok" || !filled.items[0].attributes.artwork.url) throw new Error("attach");
const missed = await lib.attachCatalog(async function () { throw new Error("down"); }, "cn", filled.items);
if (missed.lookup !== "ok" || missed.reason !== "filled") throw new Error("filled again " + missed.lookup);
const blank = await lib.attachCatalog(async function () {
  return {data: [{id: "9", type: "songs", attributes: {artwork: {url: "https://example.test/{w}x{h}bb.{f}"}, artistName: "周杰伦", albumName: "叶惠美", durationInMillis: 269000}}]};
}, "cn", [{id: "i.1", type: "library-songs", attributes: {name: "晴天", artwork: {}, playParams: {catalogId: "9"}}}]);
if (!blank.items[0].attributes.artwork.url || blank.reason !== "filled") throw new Error("blank " + blank.reason);
const unnamed = await lib.attachCatalog(async function () { throw new Error("no id"); }, "cn", [
  {id: "i.2", type: "library-songs", attributes: {name: "无名"}}
]);
if (unnamed.reason !== "no-catalog-id") throw new Error(unnamed.reason);
const included = await lib.collectTracks(async function (path, query) {
  if (query.include !== "catalog") throw new Error("include");
  return {data: {data: [{id: "i.3", type: "library-songs", attributes: {name: "晴天", artwork: {}}, relationships: {catalog: {data: [{id: "9", type: "songs"}]}}}], resources: {songs: {"9": {id: "9", type: "songs", attributes: {artistName: "周杰伦", albumName: "叶惠美", durationInMillis: 269000, artwork: {url: "https://example.test/{w}x{h}bb.{f}"}}}}}}};
}, "pl.1", {limit: 5, offset: 0});
if (!included.items[0].attributes.artwork.url || included.items[0].attributes.playParams.catalogId !== "9") throw new Error("included");
const lyricId = await lib.resolveLyricCatalog(async function () {
  return {data: [{id: "i.3", type: "library-songs", attributes: {name: "晴天"}, relationships: {catalog: {data: [{id: "9", type: "songs", attributes: {name: "晴天"}}]}}}]};
}, "i.3", "");
if (lyricId.id !== "9" || lyricId.reason !== "include") throw new Error("lyric " + lyricId.reason);
const shelfRow = (id, name, type) => ({id, type: type || "playlists", attributes: {name}});
const mine = lib.recommendationPlaylists({data: {data: [
  {id: "r1", type: "personal-recommendation", relationships: {contents: {data: [shelfRow("pl.a", "精选一"), shelfRow("1", "专辑", "albums")]}}},
  {id: "r2", type: "personal-recommendation", relationships: {contents: {data: [shelfRow("pl.a", "精选一"), shelfRow("pl.b", "精选二")]}}}
]}}, 10);
if (mine.map(row => row.id).join(",") !== "pl.a,pl.b") throw new Error("recommendations " + mine.map(row => row.id));
const tops = lib.chartPlaylists({results: {playlists: [{chart: "most-played", data: [shelfRow("pl.c", "热门"), shelfRow("pl.d", "")]}]}}, 10);
if (tops.map(row => row.id).join(",") !== "pl.c") throw new Error("charts");
const lists = lib.searchPlaylists({data: {results: {playlists: {data: [shelfRow("pl.e", "搜到")]}}}}, 10);
if (lists[0].id !== "pl.e") throw new Error("search playlists");
if (lib.tracksPath("pl.e", "cn") !== "/v1/catalog/cn/playlists/pl.e/tracks") throw new Error("catalog tracks");
if (lib.tracksPath("p.1", "cn") !== lib.playlistPath + "/p.1/tracks") throw new Error("library tracks");
if (lib.musicKitInstance(null)) throw new Error("null host");
if (lib.musicKitInstance({MusicKit: {getInstance() { return {}; }}})) throw new Error("no api");
const ready = lib.musicKitInstance({MusicKit: {getInstance() { return {api: {music() {}}}; }}});
if (!ready || !ready.api) throw new Error("ready");
})().catch((error) => { console.error(error); process.exit(1); });
"""
        completed = subprocess.run(
            [node, "-e", program, str(script)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)


def _executable(folder: str, body: str) -> Path:
    path = Path(folder) / "companion"
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class CompanionProcessLifetimeTests(unittest.TestCase):
    def test_login_wait_does_not_retire_the_process(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        script = _executable(folder.name, """
import json, sys
while True:
    line = sys.stdin.readline()
    if not line:
        break
    request = json.loads(line)
    show = request.get("args", {}).get("show", True)
    if request.get("op") == "login_window" and show is not False:
        continue
    sys.stdout.write(json.dumps({"v": 1, "id": request["id"], "ok": True, "body": {"logged_in": False}}) + "\\n")
    sys.stdout.flush()
""")
        client = ProcessAppleMusicCompanion(script, timeout=5)
        self.addCleanup(client.close)
        with self.assertRaises(OnlineMusicError) as caught:
            client.request("login_window", {"show": True}, timeout=0.3)
        self.assertEqual(caught.exception.code, "timeout")
        process = client._process
        self.assertIsNotNone(process)
        self.assertIsNone(process.poll())
        self.assertEqual(client.request("account", timeout=2)["logged_in"], False)
        self.assertIs(client._process, process)

    def test_ordinary_timeout_leaves_the_companion_alive_for_a_page_reload(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        script = _executable(folder.name, """
import sys
while True:
    line = sys.stdin.readline()
    if not line:
        break
""")
        client = ProcessAppleMusicCompanion(script, timeout=5)
        self.addCleanup(client.close)
        with self.assertRaises(OnlineMusicError) as caught:
            client.request("account", timeout=0.3)
        self.assertEqual(caught.exception.code, "timeout")
        process = client._process
        self.assertIsNotNone(process)
        self.assertIsNone(process.poll())

    def test_parent_close_waits_then_kills_a_companion_that_will_not_leave(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        script = _executable(folder.name, """
import time
time.sleep(30)
""")
        client = ProcessAppleMusicCompanion(script, timeout=5)
        with self.assertRaises(OnlineMusicError):
            client.request("account", timeout=0.2)
        process = client._process
        started = time.monotonic()
        client.close()
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 1.2)
        self.assertLess(elapsed, 4.0)
        self.assertIsNotNone(process.poll())

    def test_a_second_request_is_sent_while_the_first_is_still_waiting(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        script = _executable(folder.name, """
import json, sys, time
first = json.loads(sys.stdin.readline())
second = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 1, "id": second["id"], "ok": True, "body": {"shown": False}}) + "\\n")
sys.stdout.flush()
time.sleep(30)
""")
        client = ProcessAppleMusicCompanion(script, timeout=5)
        self.addCleanup(client.close)
        holder = {}

        def account():
            try:
                client.request("account", timeout=2)
            except OnlineMusicError as exc:
                holder["error"] = exc.code

        worker = threading.Thread(target=account, daemon=True)
        worker.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not client._waiters:
            time.sleep(0.01)
        self.assertTrue(client._waiters)
        started = time.monotonic()
        body = client.request("login_window", {"show": False}, timeout=1)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(body["shown"], False)
        worker.join(3)
        self.assertEqual(holder.get("error"), "timeout")

    def test_stderr_reaches_the_apple_log_without_secrets_and_signals_window_close(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        script = _executable(folder.name, """
import json, sys
sys.stderr.write("token=sekret cookie=abc password=hidden event=login-window-closed\\n")
sys.stderr.flush()
request = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 1, "id": request["id"], "ok": True, "body": {"logged_in": False}}) + "\\n")
sys.stdout.flush()
while True:
    extra = sys.stdin.readline()
    if not extra:
        break
""")
        client = ProcessAppleMusicCompanion(script, timeout=5)
        self.addCleanup(client.close)
        seen = []
        client.set_event_handler(seen.append)
        with self.assertLogs("GluSpot.apple", level="INFO") as logs:
            client.request("account", timeout=2)
            for _ in range(50):
                if seen:
                    break
                threading.Event().wait(0.02)
        self.assertEqual(seen, ["login-window-closed"])
        blob = "\n".join(logs.output).lower()
        self.assertIn("event=login-window-closed", blob)
        self.assertNotIn("sekret", blob)
        self.assertNotIn("token", blob)
        self.assertNotIn("cookie", blob)
        self.assertNotIn("password", blob)


class AppleValidationSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_switch_source_returns_while_validation_is_still_waiting(self):
        from PySide6.QtCore import QObject, Signal
        from local.music_library_controller import MusicLibraryController
        from local.music_library_window import MusicLibraryWindow
        from tests.test_playback_entry_controller import FakeSettings, FakeTasks

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        script = _executable(folder.name, """
import sys, time
sys.stdin.readline()
time.sleep(30)
""")
        companion = ProcessAppleMusicCompanion(script, timeout=30)
        self.addCleanup(companion.close)
        backend = AppleMusicBackend(companion)
        store = ProtectedCredentialStore(Path(folder.name) / "creds", Protector())
        service = OnlineMusicService(backend, store, clock=lambda: 1000.0)
        self.addCleanup(service.close)

        class Player(QObject):
            changed = Signal()
            statusChanged = Signal(str)
            track = None

            def stop(self):
                pass

        class Island:
            _music_library_active = False

        class Entry:
            def show_login(self, source=""):
                self.source = source

        window = MusicLibraryWindow()
        self.addCleanup(window.close)
        controller = MusicLibraryController(
            FakeSettings(completed=True), service, Player(), Island(), Entry(),
            window=window, tasks=FakeTasks(), art_tasks=FakeTasks(),
        )
        self.addCleanup(controller.close)
        controller._source = "apple_music"
        controller._account_validating = True
        blocked = threading.Event()

        def validate():
            blocked.set()
            try:
                service.account_status("apple_music", refresh=True)
            except OnlineMusicError:
                pass

        threading.Thread(target=validate, daemon=True).start()
        self.assertTrue(blocked.wait(2))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not companion._waiters:
            time.sleep(0.01)
        self.assertTrue(companion._waiters)
        started = time.monotonic()
        controller.switch_source()
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertFalse(controller._account_validating)
        self.assertEqual(controller._source, "")

    def test_personal_playlists_wait_for_a_live_account_and_stop_the_skeleton_on_timeout(self):
        from PySide6.QtCore import QObject, Signal
        from local.music_library_controller import MusicLibraryController
        from local.music_library_window import MusicLibraryWindow
        from tests.test_playback_entry_controller import FakeSettings

        class Tasks(QObject):
            completed = Signal(object, object, object)

            def __init__(self):
                super().__init__()
                self.jobs = []

            def submit(self, token, action):
                self.jobs.append((token, action))
                return True

            def close(self):
                self.jobs.clear()

            def run_latest(self, kind):
                token, action = next(job for job in reversed(self.jobs) if job[0][0] == kind)
                try:
                    result, error = action(), None
                except OnlineMusicError as exc:
                    result, error = None, exc.code
                self.completed.emit(token, result, error)

        class Service:
            def __init__(self):
                self.calls = []

            def restore_account(self, source):
                self.calls.append(("restore", source))
                return AccountSnapshot(source, True, "cached-user", "Apple", state="verified")

            def cached_catalog(self, source, section="playlists"):
                self.calls.append(("cached", source, section))
                return CatalogPage((), 0, False)

            def account_status(self, source, refresh=False):
                self.calls.append(("account", source, refresh))
                if refresh:
                    return AccountSnapshot(source, state="error", message="超时")
                return AccountSnapshot(source, True, "cached-user", "Apple", state="verified")

            def playlists(self, source, limit=100, offset=0):
                self.calls.append(("playlists", source))
                raise OnlineMusicError("timeout", source, "超时")

            def cancel_account_validation(self, source):
                self.calls.append(("cancel", source))

        class Player(QObject):
            changed = Signal()
            statusChanged = Signal(str)
            track = None

            def stop(self):
                pass

        class Island:
            _music_library_active = False

            def hide(self):
                pass

        class Entry:
            _library_origin = None

            def show_login(self, source=""):
                self.source = source

        window = MusicLibraryWindow()
        self.addCleanup(window.close)
        tasks = Tasks()
        service = Service()
        controller = MusicLibraryController(
            FakeSettings(completed=True), service, Player(), Island(), Entry(),
            window=window, tasks=tasks, art_tasks=Tasks(),
        )
        self.addCleanup(controller.close)
        controller.open_source("apple_music")
        tasks.run_latest("resume")
        tasks.run_latest("open")
        self.assertIn(("account", "apple_music", True), service.calls)
        self.assertNotIn(("playlists", "apple_music"), service.calls)
        self.assertIn("请重试", window._error)
        self.assertEqual(window._recovery_label, "重试")
        self.assertNotIn("personal", window._catalog_loading)
        self.assertFalse(window._loading)
        window.recovery_action.click()
        self.assertTrue(any(job[0][0] == "open" for job in tasks.jobs))

class AppleWindowRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_closing_the_login_window_refreshes_the_source_light(self):
        from PySide6.QtCore import QObject, Signal
        from local.playback_entry_controller import PlaybackEntryController

        class Tasks(QObject):
            completed = Signal(object, object, object)

            def __init__(self):
                super().__init__()
                self.jobs = []

            def submit(self, token, action):
                self.jobs.append((token, action))
                return True

            def run_latest(self, kind):
                token, action = next(job for job in reversed(self.jobs) if job[0][0] == kind)
                try:
                    result, error = action(), None
                except Exception:
                    result, error = None, "request_failed"
                self.completed.emit(token, result, error)

        class Window(QObject):
            selectionAccepted = Signal(bool, bool)
            sourceSelected = Signal(str)
            refreshRequested = Signal()
            closed = Signal()
            backRequested = Signal()
            loginAccepted = Signal(str)

            def __init__(self):
                super().__init__()
                self.statuses = {}

            def _set_source_status(self, source_id, status):
                self.statuses[source_id] = status

            @staticmethod
            def _account_status(account):
                if account.state in {"error", "expired"}:
                    return "error"
                return "connected" if account.logged_in else "signed_out"

        class Settings:
            def get(self, key, default=None):
                return default

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        companion = MemoryAppleMusicCompanion()
        companion.authorized = True
        companion.user_id = "abc123"
        service = OnlineMusicService(AppleMusicBackend(companion), ProtectedCredentialStore(Path(folder.name), Protector()), clock=lambda: 1000.0)
        self.addCleanup(service.close)
        tasks = Tasks()
        window = Window()
        controller = PlaybackEntryController(Settings(), object(), object(), service, window=window, tasks=tasks)
        self.addCleanup(controller.deleteLater)
        service.backend.notify_login_window_closed()
        tasks.run_latest("apple-window")
        self.assertEqual(window.statuses["apple_music"], "connected")
        self.assertEqual([call[0] for call in companion.calls if call[0] == "account"], ["account"])


if __name__ == "__main__":
    unittest.main()
