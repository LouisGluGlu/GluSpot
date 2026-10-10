from __future__ import annotations

import base64
import tempfile
import threading
import unittest
from pathlib import Path

from api.online_music import OnlineMusicService, OnlineMusicError
from api.online_music_credentials import ProtectedCredentialStore

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfixture-image").decode()
COOKIE = {"cookie": {"MUSIC_U": "synthetic-session-fixture"}}


class FixtureProtector:
    def protect(self, data):
        return bytes(value ^ 0xA5 for value in data)

    def unprotect(self, data):
        return self.protect(data)


class Backend:
    def __init__(self):
        self.calls = []
        self.state = "waiting"
        self.verified = True
        self.closed = False
        self.block_started = None
        self.block_release = None
        self.error = None
        self.catalog = {}

    def available(self, source):
        return (source != "qishui", "not yet supported" if source == "qishui" else "")

    def request(self, source, operation, params, credential=None):
        self.calls.append((source, operation, params))
        if self.error:
            raise OnlineMusicError("network", source, "服务连接失败")
        if operation == "begin_login":
            return {"data": {"key": "qr-fixture", "qr_image": PNG, "ttl": 180}}
        if operation == "poll_login":
            if self.block_started:
                self.block_started.set()
                self.block_release.wait(3)
            return {"data": {"state": self.state}, "credential": COOKIE}
        if operation == "account_status":
            return {"data": {"logged_in": self.verified, "user_id": "42", "nickname": "用户"}, "credential": COOKIE}
        if operation in {"logout", "cancel_login"}:
            return {"data": {}}
        return {"data": self.catalog.get(operation, {}), "credential": COOKIE}

    def close(self):
        self.closed = True


class OnlineMusicServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.backend = Backend()
        self.store = ProtectedCredentialStore(Path(self.folder.name), FixtureProtector())
        self.clock_value = 1000.0
        self.service = OnlineMusicService(self.backend, self.store, clock=lambda: self.clock_value)
        self.addCleanup(self.service.close)

    def login(self, source="netease"):
        snapshot = self.service.begin_login(source)
        self.backend.state = "confirmed"
        return self.service.poll_login(snapshot.session_id)

    def test_listing_and_default_snapshots_have_no_network_or_credentials(self):
        self.assertEqual([s.source_id for s in self.service.list_sources()], ["netease", "kugou", "qq", "qishui"])
        self.assertFalse(self.service.list_sources()[-1].available)
        self.assertFalse(self.service.account_status("netease").logged_in)
        self.assertFalse(self.service.has_login_record("netease"))
        self.assertEqual(self.backend.calls, [])

    def test_real_image_required_and_success_needs_verified_account(self):
        original = self.backend.request
        self.backend.request = lambda *args, **kwargs: {"data": {"key": "qr", "qr_image": "fake QR"}}
        with self.assertRaises(OnlineMusicError):
            self.service.begin_login("netease")
        self.backend.request = original
        self.backend.verified = False
        self.assertEqual(self.login().state, "error")
        self.assertFalse(self.service.has_login_record("netease"))

    def test_confirmed_session_saved_encrypted_and_restored_separately_from_validity(self):
        final = self.login()
        self.assertEqual(final.state, "confirmed")
        self.assertTrue(final.account.logged_in)
        self.assertEqual(final.qr_image_bytes, b"")
        self.assertTrue(self.service.has_login_record("netease"))
        self.assertNotIn(b"synthetic-session", (Path(self.folder.name) / "netease.dpapi").read_bytes())
        restored = OnlineMusicService(self.backend, self.store)
        self.addCleanup(restored.close)
        self.assertTrue(restored.has_login_record("netease"))
        self.assertFalse(restored.account_status("netease").logged_in)
        self.assertTrue(restored.account_status("netease", refresh=True).logged_in)
        self.assertNotIn("synthetic-session", repr(final))

    def test_expiry_does_not_poll_and_drops_image(self):
        snapshot = self.service.begin_login("netease")
        self.clock_value = snapshot.expires_at
        final = self.service.poll_login(snapshot.session_id)
        self.assertEqual(final.state, "expired")
        self.assertFalse(final.qr_image_bytes)
        self.assertEqual(len(self.backend.calls), 1)

    def test_cancel_inflight_confirmation_never_saves_session(self):
        snapshot = self.service.begin_login("netease")
        self.backend.state = "confirmed"
        self.backend.block_started = threading.Event()
        self.backend.block_release = threading.Event()
        result = []
        thread = threading.Thread(target=lambda: result.append(self.service.poll_login(snapshot.session_id)))
        thread.start()
        self.assertTrue(self.backend.block_started.wait(1))
        self.service.cancel_login(snapshot.session_id)
        self.backend.block_release.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0].state, "cancelled")
        self.assertFalse(self.service.has_login_record("netease"))

    def test_new_attempt_invalidates_previous_generation(self):
        first = self.service.begin_login("netease")
        second = self.service.begin_login("netease")
        self.assertNotEqual(first.session_id, second.session_id)
        self.backend.state = "confirmed"
        self.assertEqual(self.service.poll_login(first.session_id).state, "cancelled")
        self.assertEqual(self.service.poll_login(second.session_id).state, "confirmed")

    def test_refresh_network_error_preserves_local_login_record(self):
        self.login()
        self.backend.error = True
        status = self.service.account_status("netease", refresh=True)
        self.assertFalse(status.logged_in)
        self.assertEqual(status.state, "error")
        self.assertTrue(self.service.has_login_record("netease"))

    def test_daily_fm_stream_and_yrc_preserve_platform_evidence(self):
        self.login()
        song = {"id": 9, "name": "曲目", "ar": [{"name": "歌手"}], "al": {"name": "专辑", "picUrl": "https://example.org/art.png"}, "dt": 245000}
        self.backend.catalog = {"daily_recommendations": {"data": {"dailySongs": [song]}}, "personal_fm": {"data": [song]}, "stream_url": {"data": [{"url": "https://example.org/song.mp3", "freeTrialInfo": {"start": 1}, "expi": 60}]}, "lyrics": {"lrc": {"lyric": "[00:01]歌词"}, "yrc": {"lyric": "[1000,1000](1000,500,0)歌(1500,500,0)词"}, "ytlrc": {"lyric": "[00:01]translation"}}}
        daily = self.service.daily_recommendations()
        self.assertEqual(daily[0].song_id, "9")
        self.assertEqual(daily[0].duration_seconds, 245)
        self.assertEqual(self.service.personal_fm()[0], daily[0])
        stream = self.service.stream_url("netease", "9")
        self.assertTrue(stream.playable)
        self.assertTrue(stream.trial)
        self.assertEqual(stream.expires_at, self.clock_value + 60)
        self.assertNotIn(stream.url, repr(stream))
        lyrics = self.service.lyrics("netease", "9")
        self.assertIn("(1500,500,0)", lyrics.raw_yrc)
        self.assertIn("translation", lyrics.translation)

    def test_account_required_and_platform_denial_is_not_playable(self):
        with self.assertRaises(OnlineMusicError) as raised:
            self.service.daily_recommendations()
        self.assertEqual(raised.exception.code, "auth_required")
        self.login()
        self.backend.catalog["stream_url"] = {"data": [{"url": None, "code": -110}]}
        self.assertFalse(self.service.stream_url("netease", "9").playable)
        self.backend.catalog["stream_url"] = {"data": [{"url": "https://user:password@example.org/song"}]}
        self.assertFalse(self.service.stream_url("netease", "9").playable)

    def test_playlist_and_track_pagination(self):
        self.login()
        self.backend.catalog = {"playlists": {"playlist": [{"id": 12, "name": "列表", "trackCount": 2}]}, "playlist_tracks": {"songs": [{"id": 9, "name": "曲目", "dt": 1000}]}}
        page = self.service.playlists("netease", limit=1)
        self.assertEqual(page.items[0].playlist_id, "12")
        self.assertEqual(self.service.playlist_tracks("netease", "12").items[0].song_id, "9")
        self.assertEqual(page.next_offset, 1)

    def test_logout_clears_record_and_close_disables_requests(self):
        self.login()
        self.service.logout("netease")
        self.assertFalse(self.service.has_login_record("netease"))
        self.service.close()
        self.assertTrue(self.backend.closed)
        with self.assertRaises(OnlineMusicError):
            self.service.begin_login("netease")

    def test_qq_catalog_retains_upstream_fields_and_paged_window(self):
        self.login("qq")
        self.backend.catalog = {
            "playlists": {"v_playlist": [
                {"tid": "1", "diss_name": "first", "song_cnt": 1},
                {"tid": "2", "diss_name": "second", "song_cnt": "7", "cover_url": "https://example.org/cover.jpg"},
            ]},
            "playlist_tracks": {"_windowed": True, "songlist": [
                {"mid": "track-mid", "name": "track", "interval": 210, "singer": [{"name": "artist"}]},
            ]},
            "stream_url": {"url": "https://example.org/track.m4a"},
        }
        page = self.service.playlists("qq", limit=1, offset=1)
        self.assertEqual(page.items[0].playlist_id, "2")
        self.assertEqual(page.items[0].track_count, 7)
        self.assertEqual(page.items[0].artwork_url, "https://example.org/cover.jpg")
        tracks = self.service.playlist_tracks("qq", "2", limit=1, offset=200)
        self.assertEqual(tracks.items[0].song_id, "track-mid")
        self.assertEqual(tracks.items[0].duration_seconds, 210)
        self.assertEqual(tracks.next_offset, 201)
        self.assertTrue(self.service.stream_url("qq", "track-mid").playable)

    def test_qq_window_limit_matches_backend_and_fallback_slices_once(self):
        self.login("qq")
        songs = [{"mid": f"mid-{i}", "name": f"track-{i}"} for i in range(100)]
        self.backend.catalog["playlist_tracks"] = {"_windowed": True, "songlist": songs}
        page = self.service.playlist_tracks("qq", "2", limit=1000, offset=200)
        self.assertEqual(len(page.items), 100)
        self.assertTrue(page.has_more)
        self.assertEqual(self.backend.calls[-1][2]["limit"], 100)
        self.backend.catalog["playlist_tracks"] = {"songlist": songs}
        fallback = self.service.playlist_tracks("qq", "2", limit=2, offset=5)
        self.assertEqual([song.song_id for song in fallback.items], ["mid-5", "mid-6"])
        self.assertEqual(fallback.next_offset, 7)

    def test_malformed_remote_counts_and_expiry_do_not_break_catalog(self):
        self.login()
        self.backend.catalog = {
            "playlists": {"playlist": [{"id": 12, "name": "list", "trackCount": "unknown"}]},
            "stream_url": {"data": [{"url": "https://example.org/track.mp3", "expi": "unknown"}]},
        }
        self.assertEqual(self.service.playlists("netease").items[0].track_count, 0)
        self.assertEqual(self.service.stream_url("netease", "9").expires_at, self.clock_value)

    def test_verified_playlist_identifiers_use_the_track_endpoint_namespace(self):
        self.login("qq")
        self.backend.catalog["playlists"] = {"v_playlist": [
            {"dissid": "favorite-1", "diss_name": "favorite"},
            {"dirId": "directory-1", "diss_name": "directory"},
            {"dirid": "directory-2", "diss_name": "directory"},
        ]}
        self.assertEqual([p.playlist_id for p in self.service.playlists("qq").items], ["favorite-1", "directory-1", "directory-2"])
        self.login("kugou")
        self.backend.catalog["playlists"] = {"data": {"info": [
            {"id": "internal-id", "listid": "local-list", "global_collection_id": "global-id", "name": "list"},
        ]}}
        self.assertEqual(self.service.playlists("kugou").items[0].playlist_id, "global-id")

    def test_search_songs_preserves_metadata_and_authoritative_page_cursor(self):
        self.login("qq")
        self.backend.catalog["search"] = {"items": [
            {"mid": "search-mid", "title": "searched song", "album": {"mid": "albumMid", "name": "album title"}, "singer": [{"name": "artist"}], "interval": 123},
        ], "total": 87, "has_more": True, "next_offset": 60}
        page = self.service.search("qq", "  keyword  ", limit=30, offset=30)
        self.assertEqual(page.items[0].album, "album title")
        self.assertEqual(page.items[0].song_id, "search-mid")
        self.assertEqual(page.items[0].duration_seconds, 123)
        self.assertIn("albumMid.jpg", page.items[0].artwork_url)
        self.assertEqual((page.total, page.next_offset, page.has_more), (87, 60, True))
        self.assertEqual(self.backend.calls[-1][2], {"query": "keyword", "kind": "songs", "limit": 30, "offset": 30})

    def test_search_playlists_and_kugou_song_fields_use_playable_identifiers(self):
        for source, raw, expected in [
            ("netease", {"id": 9, "name": "list", "trackCount": 3}, "9"),
            ("qq", {"dissid": "42", "dissname": "list", "song_count": 3, "imgurl": "https://example.org/cover.jpg"}, "42"),
            ("kugou", {"specialid": 9, "gid": "collection_3_42_1_0", "specialname": "list", "song_count": 3}, "collection_3_42_1_0"),
        ]:
            with self.subTest(source=source):
                self.login(source)
                self.backend.catalog["search"] = {"items": [raw], "total": 1, "has_more": False, "next_offset": 1}
                page = self.service.search(source, "query", "playlists")
                self.assertEqual(page.items[0].playlist_id, expected)
                self.assertEqual(page.items[0].track_count, 3)
        self.backend.catalog["search"] = {"items": [{"FileHash": "SEARCHHASH", "SongName": "song", "SingerName": "artist", "AlbumName": "album", "Duration": 210}], "total": 1, "next_offset": 1}
        song = self.service.search("kugou", "query").items[0]
        self.assertEqual((song.song_id, song.title, song.artist, song.album, song.duration_seconds), ("SEARCHHASH", "song", "artist", "album", 210))

    def test_search_validates_arguments_auth_and_unavailable_platform(self):
        for query, kind in [("", "songs"), ("   ", "songs"), ("x" * 201, "songs"), ("a\nb", "songs"), ("query", "albums")]:
            with self.subTest(query=query, kind=kind), self.assertRaises(OnlineMusicError) as raised:
                self.service.search("netease", query, kind)
            self.assertEqual(raised.exception.code, "argument")
        with self.assertRaises(OnlineMusicError) as raised:
            self.service.search("netease", "query")
        self.assertEqual(raised.exception.code, "auth_required")
        with self.assertRaises(OnlineMusicError) as raised:
            self.service.search("qishui", "query")
        self.assertEqual(raised.exception.code, "unavailable")

    def test_kugou_playlist_artwork_expands_platform_size_template(self):
        self.login("kugou")
        for field, value, expected in (
            ("img", "https://example.org/cover/{size}/fixture.jpg", "https://example.org/cover/500/fixture.jpg"),
            ("imgurl", "https://example.org/cover/{size}/fixture.jpg", "https://example.org/cover/500/fixture.jpg"),
            ("img", "https://example.org/cover/fixed.jpg", "https://example.org/cover/fixed.jpg"),
            ("imgurl", "https://user:secret@example.org/cover/{size}.jpg", ""),
            ("img", {"unexpected": "value"}, ""),
        ):
            with self.subTest(field=field, value=value):
                raw = {"gid": "collection_3_42_1_0", "specialname": "fixture", field: value}
                self.backend.catalog["search"] = {"items": [raw], "total": 1, "next_offset": 1}
                self.backend.catalog["playlists"] = {"data": {"info": [raw]}}
                self.assertEqual(self.service.search("kugou", "query", "playlists").items[0].artwork_url, expected)
                self.assertEqual(self.service.playlists("kugou").items[0].artwork_url, expected)

    def test_search_empty_last_page_and_bad_rows_do_not_restart_pagination(self):
        self.login("kugou")
        self.backend.catalog["search"] = {"items": [{"specialid": 3, "specialname": "wrong namespace"}], "total": 1, "next_offset": 1, "has_more": False}
        page = self.service.search("kugou", "query", "playlists")
        self.assertEqual(page.items, ())
        self.assertEqual(page.next_offset, 1)
        self.backend.catalog["search"] = {"items": [], "total": 0, "next_offset": 30, "has_more": False}
        page = self.service.search("kugou", "query", offset=30)
        self.assertEqual((page.items, page.next_offset, page.has_more, page.total), ((), 30, False, 0))
        self.backend.catalog["search"] = {"items": [], "next_offset": 20, "has_more": True}
        self.assertFalse(self.service.search("kugou", "query", offset=30).has_more)


if __name__ == "__main__":
    unittest.main()
