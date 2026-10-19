from __future__ import annotations

import base64
import json
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
    def test_netease_keeps_raw_companion_variants_and_original_clocks(self):
        self.login("netease")
        raw = {"lrc": {"lyric": "[00:01]Hello"},
            "yrc": {"lyric": "[1500,1000](1500,1000,0)Hello"},
            "tlyric": {"lyric": "[00:01]旧译文"}, "ytlrc": {"lyric": "[00:01.5]准确译文"},
            "romalrc": {"lyric": "[00:01]old roman"}, "yromalrc": {"lyric": "[00:01.5]accurate roman"}}
        self.backend.catalog["lyrics"] = raw
        payload = self.service.lyrics("netease", "1")
        self.assertEqual(json.loads(payload.raw_netease), raw)
        self.assertEqual(payload.translation, raw["ytlrc"]["lyric"])
        self.assertEqual(payload.romanization, raw["yromalrc"]["lyric"])

    def test_qq_service_really_decrypts_qrc_and_preserves_corrupt_field_lrc(self):
        from tests.test_lyric_source_formats import QRC_CIPHER, QRC_TEXT
        self.login("qq")
        for encrypted, expected in ((QRC_CIPHER, QRC_TEXT), ("bad hex", "")):
            with self.subTest(encrypted=encrypted[:10]):
                self.backend.catalog["lyrics"] = {"lyric": encrypted,
                    "raw_lrc": "[00:01]existing line", "translation": "[00:01]译文"}
                payload = self.service.lyrics("qq", "mid9")
                self.assertEqual(payload.raw_qrc, expected)
                self.assertEqual(payload.raw_lrc, "[00:01]existing line")
                self.assertEqual(payload.translation, "[00:01]译文")
                self.assertEqual(payload.source, "qq-account")
                self.assertEqual((payload.source_id, payload.song_id), ("qq", "mid9"))

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
        self.assertEqual(daily[0].artwork_url, "https://example.org/art.png")
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

    def test_personal_fm_legacy_duration_is_milliseconds_without_changing_other_sources(self):
        self.login()
        self.backend.catalog["personal_fm"] = {"data": [{
            "id": 9, "name": "曲目", "duration": 178106,
            "artists": [{"name": "歌手"}],
            "album": {"name": "专辑", "picUrl": "https://example.org/art.png"},
        }]}
        song = self.service.personal_fm()[0]
        self.assertEqual(song.duration_seconds, 178.106)
        self.assertEqual(song.artist, "歌手")
        self.assertEqual(song.artwork_url, "https://example.org/art.png")
        for source in ("qq", "kugou"):
            with self.subTest(source=source):
                self.assertEqual(self.service._song(source, {"id": 9, "name": "曲目", "duration": 178}).duration_seconds, 178)

    def recommendation_fixture(self):
        self.backend.catalog["daily_recommendations"] = {"data": {"dailySongs": [
            {"id": 1, "name": "no picture"},
            {"id": 2, "name": "daily picture", "al": {"picUrl": "https://example.org/daily.jpg"}}]}}
        self.backend.catalog["personal_fm"] = {"data": [
            {"id": 3, "name": "invalid picture", "album": {"picUrl": "file:///private"}},
            {"id": 4, "name": "FM picture", "album": {"picUrl": "https://example.org/fm.jpg"}}]}

    def test_recommendation_artwork_keeps_the_same_batch_for_one_actual_play(self):
        self.login()
        self.recommendation_fixture()
        for kind, operation, play in (("daily", "daily_recommendations", self.service.daily_recommendations),
                                      ("fm", "personal_fm", self.service.personal_fm)):
            with self.subTest(kind=kind):
                self.assertEqual(self.service.recommendation_artwork(kind), f"https://example.org/{kind}.jpg")
                self.assertEqual(self.service.recommendation_artwork(kind), f"https://example.org/{kind}.jpg")
                songs = play()
                self.assertEqual(len(songs), 2)
                self.assertEqual(len([call for call in self.backend.calls if call[1] == operation]), 1)
                self.assertNotIn(kind, self.service._recommendation_previews)
                self.assertEqual(play(), songs)
                self.assertEqual(len([call for call in self.backend.calls if call[1] == operation]), 2)

    def test_playback_before_cover_reuses_the_real_playback_cover_without_consuming_next_fm(self):
        self.login()
        self.recommendation_fixture()
        self.service.personal_fm()
        self.assertEqual(self.service.recommendation_artwork("fm"), "https://example.org/fm.jpg")
        self.assertEqual(len([call for call in self.backend.calls if call[1] == "personal_fm"]), 1)
        self.assertNotIn("fm", self.service._recommendation_previews)

    def test_simultaneous_recommendation_cover_and_playback_share_one_request_in_both_orders(self):
        self.login()
        self.recommendation_fixture()
        original = self.backend.request
        for preview_first in (True, False):
            with self.subTest(preview_first=preview_first):
                self.service._recommendation_previews.clear()
                self.service._recommendation_covers.clear()
                started, release = threading.Event(), threading.Event()
                def request(source, operation, params, credential=None):
                    if operation == "personal_fm":
                        started.set()
                        if not release.wait(3):
                            raise RuntimeError("fixture release timeout")
                    return original(source, operation, params, credential)
                self.backend.request = request
                results, errors = {}, []
                def run(key):
                    try:
                        results[key] = (self.service.recommendation_artwork("fm") if key == "cover"
                                        else self.service.personal_fm())
                    except Exception as error:
                        errors.append(error)
                first, second = ("cover", "play") if preview_first else ("play", "cover")
                workers = [threading.Thread(target=run, args=(key,)) for key in (first, second)]
                before = len([call for call in self.backend.calls if call[1] == "personal_fm"])
                workers[0].start()
                self.assertTrue(started.wait(1))
                workers[1].start()
                release.set()
                for worker in workers:
                    worker.join(3)
                    self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(results["cover"], "https://example.org/fm.jpg")
                self.assertEqual([song.song_id for song in results["play"]], ["3", "4"])
                self.assertEqual(len([call for call in self.backend.calls if call[1] == "personal_fm"]) - before, 1)
        self.backend.request = original

    def test_recommendation_cache_ttl_refreshes_daily_but_never_discards_unread_fm(self):
        self.login()
        self.recommendation_fixture()
        for kind in ("daily", "fm"):
            self.service.recommendation_artwork(kind)
        self.clock_value += 301
        self.backend.catalog["daily_recommendations"] = {"songs": [{"id": 20, "name": "new daily"}]}
        self.backend.catalog["personal_fm"] = {"data": [{"id": 40, "name": "new radio"}]}
        self.assertEqual(self.service.daily_recommendations()[0].song_id, "20")
        self.assertEqual(self.service.recommendation_artwork("fm"), "https://example.org/fm.jpg")
        self.assertEqual([song.song_id for song in self.service.personal_fm()], ["3", "4"])
        self.assertEqual(self.service.personal_fm()[0].song_id, "40")
        self.assertLessEqual(len(self.service._recommendation_covers), 2)
        self.assertEqual(self.service._recommendation_previews, {})

    def test_recommendation_failure_retries_without_retaining_an_invalid_batch_or_fabricating_art(self):
        self.login()
        self.backend.error = True
        with self.assertRaises(OnlineMusicError):
            self.service.recommendation_artwork("fm")
        self.assertEqual(self.service._recommendation_previews, {})
        self.assertEqual(self.service._recommendation_covers, {})
        self.backend.error = False
        self.backend.catalog["personal_fm"] = {"data": [{"id": 7, "name": "no image"}]}
        self.assertEqual(self.service.recommendation_artwork("fm"), "")
        self.assertEqual(self.service.recommendation_artwork("fm"), "")
        self.assertEqual(self.service.personal_fm()[0].song_id, "7")
        self.assertEqual(len([call for call in self.backend.calls if call[1] == "personal_fm"]), 2)  # One failed, one retained.

    def test_recommendation_preview_inflight_logout_and_relogin_cannot_restore_old_songs(self):
        self.login()
        self.recommendation_fixture()
        original = self.backend.request
        started, release = threading.Event(), threading.Event()
        def request(source, operation, params, credential=None):
            if operation == "personal_fm":
                started.set()
                release.wait(3)
            return original(source, operation, params, credential)
        self.backend.request = request
        errors = []
        def preview():
            try:
                self.service.recommendation_artwork("fm")
            except OnlineMusicError as error:
                errors.append(error.code)
        worker = threading.Thread(target=preview)
        worker.start()
        self.addCleanup(release.set)
        self.addCleanup(worker.join, 3)
        self.assertTrue(started.wait(1))
        self.service.logout("netease")
        self.login()
        release.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, ["cancelled"])
        self.assertEqual(self.service._recommendation_previews, {})
        self.assertEqual(self.service._recommendation_covers, {})
        self.backend.catalog["personal_fm"] = {"data": [{"id": 10, "name": "new account"}]}
        self.assertEqual(self.service.personal_fm()[0].song_id, "10")

    def test_recommendation_artwork_rejects_unknown_kind_or_platform_before_request(self):
        self.login()
        count = len(self.backend.calls)
        for kind, source in (("other", "netease"), ("fm", "qq"), ("daily", "kugou")):
            with self.subTest(kind=kind, source=source), self.assertRaises(OnlineMusicError):
                self.service.recommendation_artwork(kind, source)
        self.assertEqual(len(self.backend.calls), count)

    def test_parallel_recommendation_auth_expiry_cannot_be_undone_by_successful_old_response(self):
        self.login()
        self.recommendation_fixture()
        self.backend.catalog["daily_recommendations"] = {"code": 301}
        original = self.backend.request
        started, release = threading.Event(), threading.Event()
        def request(source, operation, params, credential=None):
            if operation == "personal_fm":
                started.set()
                release.wait(3)
            return original(source, operation, params, credential)
        self.backend.request = request
        errors = []
        def preview():
            try:
                self.service.recommendation_artwork("fm")
            except OnlineMusicError as error:
                errors.append(error.code)
        worker = threading.Thread(target=preview)
        worker.start()
        self.addCleanup(release.set)
        self.addCleanup(worker.join, 3)
        self.assertTrue(started.wait(1))
        with self.assertRaises(OnlineMusicError) as raised:
            self.service.recommendation_artwork("daily")
        self.assertEqual(raised.exception.code, "auth_required")
        release.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, ["auth_required"])
        self.assertEqual(self.service._recommendation_previews, {})
        self.assertEqual(self.service._recommendation_covers, {})

    def test_cached_recommendation_requires_still_verified_account(self):
        self.login()
        self.recommendation_fixture()
        self.service.recommendation_artwork("fm")
        from api.online_music import AccountSnapshot
        self.service._accounts["netease"] = AccountSnapshot("netease", state="expired")
        self.backend.verified = False
        with self.assertRaises(OnlineMusicError) as raised:
            self.service.recommendation_artwork("fm")
        self.assertEqual(raised.exception.code, "auth_required")
        self.assertEqual(self.service._recommendation_previews, {})
        self.assertEqual(self.service._recommendation_covers, {})

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

    def test_qq_actual_created_playlist_shape_retains_name_cover_and_song_count(self):
        self.login("qq")
        self.backend.catalog["playlists"] = {"total": 3, "bFinish": True, "v_playlist": [
            {"tid": "101", "dirId": 201, "dirName": "liked fixture", "songNum": 12,
             "bigpicUrl": "https://example.org/created.png"},
            {"tid": "102", "dirId": 9, "dirName": "created fixture", "songNum": "7",
             "picUrl": "//example.org/second.png"},
            {"dissid": "103", "name": "favorite fixture", "songnum": 4,
             "coverUrl": "https://example.org/favorite.png"},
        ]}
        first = self.service.playlists("qq", limit=2)
        self.assertEqual([p.title for p in first.items], ["liked fixture", "created fixture"])
        self.assertEqual([p.track_count for p in first.items], [12, 7])
        self.assertEqual(first.items[1].artwork_url, "https://example.org/second.png")
        self.assertEqual((first.next_offset, first.total, first.has_more), (2, 3, True))
        last = self.service.playlists("qq", limit=1, offset=2)
        self.assertEqual(last.items[0].artwork_url, "https://example.org/favorite.png")
        self.assertEqual((last.next_offset, last.total, last.has_more), (3, 3, False))

    def test_kugou_mixed_library_keeps_playlist_identity_and_consumes_album_rows(self):
        self.login("kugou")
        self.backend.catalog["playlists"] = {"status": 1, "data": {"list_count": 5, "info": [
            {"source": 1, "global_collection_id": "created", "name": "created fixture",
             "sizable_cover": "https://example.org/{size}/created.png", "m_count": 9},
            {"source": 2, "global_collection_id": "album", "name": "album fixture", "musiclib_id": 7},
            {"source": "1", "global_collection_id": "favorite", "name": "favorite fixture",
             "pic": "//example.org/{size}/favorite.png", "music_num": 3},
        ]}}
        first = self.service.playlists("kugou", limit=3)
        self.assertEqual([p.playlist_id for p in first.items], ["created", "favorite"])
        self.assertEqual([p.track_count for p in first.items], [9, 3])
        self.assertEqual(first.items[0].artwork_url, "https://example.org/500/created.png")
        self.assertEqual(first.items[1].artwork_url, "https://example.org/500/favorite.png")
        self.assertEqual((first.next_offset, first.total, first.has_more), (3, 5, True))
        self.backend.catalog["playlists"] = {"status": 1, "data": {"list_count": 5, "info": []}}
        empty = self.service.playlists("kugou", limit=3, offset=3)
        self.assertFalse(empty.has_more, "a stale total must not create endless empty pages")

    def test_platform_artwork_templates_do_not_weaken_stream_url_validation(self):
        account = self.service._account("kugou", {"logged_in": True, "user_id": "42",
            "nickname": "fixture", "avatar_url": "//example.org/{size}/avatar.png"})
        self.assertEqual(account.avatar_url, "https://example.org/500/avatar.png")
        self.assertEqual(self.service._artwork_url("//user:password@example.org/avatar.png"), "")
        self.assertEqual(self.service._http_url("//example.org/song.mp3"), "")
        song = self.service._song("kugou", {"FileHash": "fixture-hash", "SongName": "fixture",
            "album_info": {"name": "fixture album", "sizable_cover": "https://example.org/{size}/album.png"}})
        self.assertEqual(song.artwork_url, "https://example.org/500/album.png")
        self.assertEqual(song.album, "fixture album")

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

    def test_recommended_playlists_keep_platform_identity_and_page_cursor(self):
        for source in ("netease", "qq"):
            with self.subTest(source=source):
                self.login(source)
                self.backend.catalog["recommended_playlists"] = {
                    "items": [{"id": 12, "name": "平台推荐", "picUrl": "https://example.org/playlist.png", "trackCount": 7}],
                    "total": 3, "next_offset": 2, "has_more": True,
                }
                page = self.service.recommended_playlists(source, limit=1, offset=1)
                item = page.items[0]
                self.assertEqual((item.source_id, item.playlist_id, item.title, item.track_count), (source, "12", "平台推荐", 7))
                self.assertEqual(item.artwork_url, "https://example.org/playlist.png")
                self.assertEqual((page.total, page.next_offset, page.has_more), (3, 2, True))
                self.assertEqual(self.backend.calls[-1], (source, "recommended_playlists", {"limit": 1, "offset": 1}))

    def test_recommended_playlists_are_bounded_deduplicated_and_do_not_invent_more(self):
        self.login()
        self.backend.catalog["recommended_playlists"] = {
            "items": [{"id": 9, "name": "推荐"}, {"id": 9, "name": "重复"}, {"id": 10}, None, {"id": 11, "name": " "}],
            "total": 3, "next_offset": 4, "has_more": True,
        }
        page = self.service.recommended_playlists("netease", limit=1000)
        self.assertEqual([item.playlist_id for item in page.items], ["9"])
        self.assertFalse(page.has_more)
        self.assertEqual(self.backend.calls[-1][2]["limit"], 100)
        self.backend.catalog["recommended_playlists"] = {"items": [], "total": 0, "next_offset": 0, "has_more": True}
        page = self.service.recommended_playlists("netease", offset=1000000)
        self.assertEqual((page.items, page.next_offset, page.has_more), ((), 100000, False))

    def test_recommended_playlists_require_verified_login_and_real_supported_source(self):
        for source in ("netease", "qq"):
            with self.subTest(source=source), self.assertRaises(OnlineMusicError) as raised:
                self.service.recommended_playlists(source)
            self.assertEqual(raised.exception.code, "auth_required")
        with self.assertRaises(OnlineMusicError) as raised:
            self.service.recommended_playlists("kugou")
        self.assertEqual(raised.exception.code, "unsupported")
        self.assertNotIn("recommendations", next(s for s in self.service.list_sources() if s.source_id == "kugou").capabilities)
        self.assertTrue(all("recommendations" in s.capabilities for s in self.service.list_sources() if s.source_id in {"netease", "qq"}))

    def test_recommended_playlists_invalid_response_and_failure_remain_errors(self):
        self.login()
        for data in ({}, {"items": {}}, {"items": None}):
            self.backend.catalog["recommended_playlists"] = data
            with self.subTest(data=data), self.assertRaises(OnlineMusicError) as raised:
                self.service.recommended_playlists("netease")
            self.assertEqual(raised.exception.code, "response")
        self.backend.error = True
        with self.assertRaises(OnlineMusicError) as raised:
            self.service.recommended_playlists("netease")
        self.assertEqual(raised.exception.code, "network")

    def test_recommended_playlists_reject_invalid_bounds_before_catalog_request(self):
        self.login()
        for limit, offset in ((True, 0), (1, False), (None, 0), (1, float("inf"))):
            count = len(self.backend.calls)
            with self.subTest(limit=limit, offset=offset), self.assertRaises(OnlineMusicError) as raised:
                self.service.recommended_playlists("netease", limit=limit, offset=offset)
            self.assertEqual(raised.exception.code, "argument")
            self.assertEqual(len(self.backend.calls), count)

    def test_recommended_playlists_response_cannot_survive_logout_generation(self):
        self.login()
        started, release = threading.Event(), threading.Event()
        original = self.backend.request
        self.backend.catalog["recommended_playlists"] = {"items": [{"id": 9, "name": "旧账号推荐"}], "total": 1, "next_offset": 1}
        def request(source, operation, params, credential=None):
            if operation == "recommended_playlists":
                started.set()
                release.wait(3)
            return original(source, operation, params, credential)
        self.backend.request = request
        errors, result = [], []
        def load():
            try:
                result.append(self.service.recommended_playlists("netease"))
            except OnlineMusicError as error:
                errors.append(error.code)
        worker = threading.Thread(target=load)
        worker.start()
        self.addCleanup(release.set)
        self.addCleanup(worker.join, 3)
        self.assertTrue(started.wait(1))
        self.service.logout("netease")
        release.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual((errors, result), (["cancelled"], []))
        self.assertFalse(self.service.has_login_record("netease"))


if __name__ == "__main__":
    unittest.main()
