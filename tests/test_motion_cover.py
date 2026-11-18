"""Motion-cover race, matching, cache, and static fallback. Network stays mocked."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path
from urllib.parse import parse_qs

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, QUrl
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication

from api.motion_cover import (
    MOTION_COVER_TTL_SECONDS, AppleTokenStore, CancelToken, MotionCoverCache, MotionCoverResolver,
    MotionResult, MotionTrack, _Provider, aes128_ecb_decrypt, aes128_ecb_encrypt, apple_web_token,
    choose_hls_variant, dynamic_cover_url, eapi_decrypt_payload, eapi_form, editorial_video_url,
    log_motion, netease_song_id, parse_hls_variants, race_providers, select_itunes_album_id,
    select_netease_song_id,
)
from local.motion_cover_playback import (
    install_motion_cover, motion_track_from_host, paint_motion_cover, shared_session,
)
from local.motion_cover_playback import MotionCoverSession
from local import settings_store


def _app():
    return QApplication.instance() or QApplication([])


class AesAndMatchTests(unittest.TestCase):
    def test_aes128_ecb_matches_nist_vector(self):
        key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        plain = bytes.fromhex("00112233445566778899aabbccddeeff")
        cipher = aes128_ecb_encrypt(key, plain)
        self.assertEqual(cipher.hex(), "69c4e0d86a7b0430d8cdb78070b4c55a")
        self.assertEqual(aes128_ecb_decrypt(key, cipher), plain)

    def test_eapi_form_round_trips_song_id_without_leaving_the_path(self):
        encoded = eapi_form("2101179024")
        params = parse_qs(encoded.decode("ascii"))["params"][0]
        plain = aes128_ecb_decrypt(__import__("api.motion_cover", fromlist=["_EAPI_KEY"])._EAPI_KEY, bytes.fromhex(params))
        text = plain.rstrip(bytes((plain[-1],))).decode("utf-8")
        self.assertIn("/api/songplay/dynamic-cover", text)
        self.assertIn("2101179024", text)
        payload = eapi_decrypt_payload(b'{"code":200,"data":{"videoPlayUrl":"https://dcover.example/a.mp4"}}')
        self.assertEqual(dynamic_cover_url(payload), "https://dcover.example/a.mp4")
        self.assertEqual(dynamic_cover_url({"code": 200, "data": None}), "")

    def test_song_id_and_metadata_matching(self):
        self.assertEqual(netease_song_id("owned:netease:99", ""), "99")
        self.assertEqual(netease_song_id("owned:qq:99", "QQ音乐"), "")
        self.assertEqual(netease_song_id("42", "网易云音乐"), "42")
        track = MotionTrack("晴天", "周杰伦", "叶惠美", 269.0)
        songs = {"result": {"songs": [
            {"id": 1, "name": "晴天", "duration": 200000, "artists": [{"name": "周杰伦"}], "album": {"name": "其他"}},
            {"id": 2, "name": "晴天", "duration": 269000, "artists": [{"name": "周杰伦"}], "album": {"name": "叶惠美"}},
            {"id": 3, "name": "其他", "duration": 269000, "artists": [{"name": "周杰伦"}], "album": {"name": "叶惠美"}},
        ]}}
        self.assertEqual(select_netease_song_id(songs, track), "2")
        self.assertEqual(select_netease_song_id(songs, MotionTrack("没有这首", "周杰伦")), "")
        itunes = {"results": [
            {"kind": "song", "trackName": "晴天", "artistName": "周杰伦", "collectionName": "其他", "collectionId": 5, "trackTimeMillis": 269000},
            {"kind": "song", "trackName": "晴天", "artistName": "周杰伦", "collectionName": "叶惠美", "collectionId": 9, "trackTimeMillis": 269200},
        ]}
        self.assertEqual(select_itunes_album_id(itunes, track), "9")
        editorial = {"data": [{"attributes": {"editorialVideo": {
            "motionSquareVideo1x1": {"video": "https://video.example/square.m3u8"},
            "motionDetailSquare": {"video": "https://video.example/detail.m3u8"},
        }}}]}
        self.assertEqual(editorial_video_url(editorial), "https://video.example/detail.m3u8")
        self.assertEqual(editorial_video_url({"data": [{"attributes": {}}]}), "")

    def test_hls_variant_stays_within_the_slot_cap(self):
        playlist = "\n".join((
            "#EXTM3U",
            '#EXT-X-STREAM-INF:BANDWIDTH=800000,AVERAGE-BANDWIDTH=700000,RESOLUTION=480x480,CODECS="avc1.4d401e"',
            "low.m3u8",
            '#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1080x1080,CODECS="avc1.640028"',
            "high.m3u8",
            '#EXT-X-STREAM-INF:BANDWIDTH=500000,RESOLUTION=720x720,CODECS="hvc1.1.6.L93.B0"',
            "hevc.m3u8",
        ))
        variants = parse_hls_variants(playlist, "https://video.example/master.m3u8")
        # The leanest H.264 rendition at least as wide as the slot; HEVC is skipped.
        self.assertEqual(choose_hls_variant(variants, 400).url, "https://video.example/low.m3u8")
        self.assertEqual(choose_hls_variant(variants, 500).url, "https://video.example/high.m3u8")
        self.assertEqual(choose_hls_variant(variants).width, 1080)

    def test_hls_variant_prefers_sharp_lean_h264_within_the_cap(self):
        rows = [(486, 773877, "avc1.64001f"), (960, 2910213, "avc1.64001f"), (1080, 6788272, "avc1.640020"),
                (1080, 3952898, "avc1.640020"), (2160, 6881184, "hvc1.2.20000000.H150"), (1920, 9000000, "avc1.640028")]
        def playlist(items):
            return "#EXTM3U\n" + "\n".join(
                f'#EXT-X-STREAM-INF:BANDWIDTH={bw},RESOLUTION={w}x{w},CODECS="{codec}"\nv{w}-{bw}.m3u8'
                for w, bw, codec in items)

        chosen = choose_hls_variant(parse_hls_variants(playlist(rows), "https://video.example/master.m3u8"))
        self.assertEqual((chosen.width, chosen.bandwidth), (1080, 3952898))
        below = [row for row in rows if row[0] != 1080]
        small = choose_hls_variant(parse_hls_variants(playlist(below), "https://video.example/master.m3u8"))
        self.assertEqual(small.width, 960)

    def test_apple_token_keeps_only_the_web_player_issuer(self):
        import base64
        body = base64.urlsafe_b64encode(json.dumps({"iss": "AMPWebPlay", "exp": 9_999_999_999}).encode()).decode().rstrip("=")
        other = base64.urlsafe_b64encode(json.dumps({"iss": "Other", "exp": 9_999_999_999}).encode()).decode().rstrip("=")
        script = f"var a='eyJhbGciOiJub25lIn0.{other}.x'; var b='eyJhbGciOiJub25lIn0.{body}.y';"
        token, expiry = apple_web_token(script, now=1_000)
        self.assertTrue(token.endswith(f".{body}.y"))
        self.assertEqual(expiry, 9_999_999_999)
        self.assertEqual(apple_web_token("no token"), ("", 0.0))


class RaceCacheTests(unittest.TestCase):
    def test_first_valid_video_cancels_the_slower_provider(self):
        started = threading.Event()
        cancelled = threading.Event()

        def slow(track, cancel, deadline):
            started.set()
            if cancel._event.wait(1.0):
                cancelled.set()
                return ""
            return "https://cdn.example/slow.mp4"

        def fast(track, cancel, deadline):
            self.assertTrue(started.wait(1.0))
            return "https://cdn.example/fast.mp4"

        result = race_providers((
            _Provider("slow", slow),
            _Provider("fast", fast),
        ), MotionTrack("晴天", "周杰伦"), timeout=1.0)
        self.assertEqual(result.provider, "fast")
        self.assertEqual(result.url, "https://cdn.example/fast.mp4")
        self.assertTrue(cancelled.wait(1.0))

    def test_all_misses_fall_back_and_cache_none(self):
        calls = {"count": 0}

        def miss(track, cancel, deadline):
            calls["count"] += 1
            return ""

        with tempfile.TemporaryDirectory() as directory:
            cache = MotionCoverCache(Path(directory), clock=lambda: 1_000.0)
            result = race_providers((_Provider("netease", miss), _Provider("apple", miss)), MotionTrack("晴天", "周杰伦"), timeout=1.0)
            self.assertEqual(result.kind, "none")
            self.assertEqual(calls["count"], 2)
            cache.put("song", result, now=1_000.0)
            cached = cache.get("song", now=1_000.0)
            self.assertEqual(cached.kind, "none")
            self.assertTrue(cached.cached)
            self.assertIsNone(cache.get("song", now=1_000.0 + MOTION_COVER_TTL_SECONDS + 1))

    def test_a_timeout_is_not_remembered_as_no_motion_cover(self):
        def stuck(track, cancel, deadline):
            cancel._event.wait(2.0)
            return ""

        def miss(track, cancel, deadline):
            return ""

        result = race_providers((_Provider("netease", miss), _Provider("apple", stuck)), MotionTrack("晴天", "周杰伦"), timeout=0.2)
        self.assertEqual(result.kind, "none")
        self.assertFalse(result.final)
        with tempfile.TemporaryDirectory() as directory:
            cache = MotionCoverCache(Path(directory))
            calls = []

            def fetch(method, url, **kwargs):
                calls.append(url)
                raise TimeoutError("slow network")

            resolver = MotionCoverResolver(cache, fetch, tokens=AppleTokenStore())
            track = MotionTrack("晴天", "周杰伦")
            self.assertEqual(resolver.resolve(track).kind, "none")
            self.assertIsNone(cache.get(track.cache_key()))      # asked again next time
        settled = race_providers((_Provider("netease", miss), _Provider("apple", miss)), MotionTrack("晴天", "周杰伦"), timeout=1.0)
        self.assertTrue(settled.final)

    def test_netease_asks_through_the_signed_in_account(self):
        asked = []

        def lookup(song_id):
            asked.append(song_id)
            return "https://dcover.example/signed.mp4"

        def fetch(method, url, **kwargs):
            raise AssertionError("no anonymous request when the account answers")

        with tempfile.TemporaryDirectory() as directory:
            resolver = MotionCoverResolver(MotionCoverCache(Path(directory)), fetch, tokens=AppleTokenStore(),
                                           netease_lookup=lookup)
            result = resolver.resolve(MotionTrack("晴天", "周杰伦", track_id="owned:netease:7"))
        self.assertEqual((result.kind, result.provider, result.url), ("hit", "netease", "https://dcover.example/signed.mp4"))
        self.assertEqual(asked, ["7"])

    def test_cache_key_does_not_change_when_the_album_arrives(self):
        self.assertEqual(MotionTrack("晴天", "周杰伦").cache_key(), MotionTrack("晴天", "周杰伦", album="叶惠美").cache_key())

    def test_cached_hit_skips_providers_and_video_files_respect_the_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            now = {"value": 50.0}
            cache = MotionCoverCache(Path(directory), ttl_seconds=10, max_bytes=5, max_file_bytes=4, clock=lambda: now["value"])
            track = MotionTrack("晴天", "周杰伦", track_id="owned:netease:7")
            cache.put(track.cache_key(), MotionResult("hit", "netease", "https://cdn.example/a.mp4"), now=50)
            calls = []

            def fetch(*args, **kwargs):
                calls.append(args)
                return 500, b""

            resolver = MotionCoverResolver(cache, fetch)
            result = resolver.resolve(track)
            self.assertEqual(result.kind, "hit")
            self.assertTrue(result.cached)
            self.assertEqual(calls, [])
            self.assertEqual(cache.store_video("https://cdn.example/one.mp4", [b"12345"], key="song"), "")
            first = cache.store_video("https://cdn.example/one.mp4", [b"123"], key="song")
            os.utime(first, (10, 10))
            second = cache.store_video("https://cdn.example/two.mp4", [b"456"], key="other")
            self.assertTrue(second.endswith(".mp4"))
            self.assertTrue(Path(second).is_file())
            self.assertFalse(Path(first).exists())

    def test_netease_id_wins_and_logs_no_token(self):
        seen = []

        def fetch(method, url, **kwargs):
            seen.append(url)
            cancel = kwargs.get("cancel")
            if "dynamic-cover" in url:
                return 200, b'{"code":200,"data":{"videoPlayUrl":"https://dcover.example/cover.mp4"}}'
            if cancel is not None:
                while not cancel.is_set():
                    time.sleep(0.01)
                raise RuntimeError("Bearer eyJsecret.payload.signature")
            raise RuntimeError("Bearer eyJsecret.payload.signature")

        with tempfile.TemporaryDirectory() as directory:
            cache = MotionCoverCache(Path(directory))
            resolver = MotionCoverResolver(cache, fetch, tokens=AppleTokenStore())
            with self.assertLogs("GluSpot.motion_cover", level="INFO") as logs:
                result = resolver.resolve(MotionTrack("晴天", "周杰伦", track_id="owned:netease:7", source_app="网易云音乐"))
            self.assertEqual(result.kind, "hit")
            self.assertEqual(result.provider, "netease")
            self.assertTrue(any("dynamic-cover" in url for url in seen))
            self.assertFalse(any("cloudsearch" in url for url in seen))
            text = "\n".join(logs.output)
            self.assertNotIn("eyJ", text)
            self.assertNotIn("Bearer", text)
            self.assertNotIn("https://", text)
            log_motion("apple", "miss", 3)
            self.assertIn("provider=apple", logs.output[-1] if False else text)

    def test_cancelled_read_is_a_timeout_not_an_attribute_error(self):
        from unittest.mock import patch
        from api.motion_cover import CancelToken, urllib_fetch

        class Response:
            status = 200

            def read(self, _limit):
                cancel.cancel()
                raise AttributeError("closed body")

            def close(self):
                pass

        cancel = CancelToken()
        with patch("api.motion_cover.urlopen", return_value=Response()):
            with self.assertRaises(TimeoutError):
                urllib_fetch("GET", "https://example.test/cover", cancel=cancel)

    def test_settings_default_on_and_reject_non_bool(self):
        _app()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"v2_motion_cover_enabled": "yes"}), encoding="utf-8")
            manager = settings_store.SettingsManager(settings_path=str(path))
            self.assertTrue(manager.get("v2_motion_cover_enabled"))
            manager.set("v2_motion_cover_enabled", False)
            self.assertFalse(manager.get("v2_motion_cover_enabled"))
            manager.set("v2_motion_cover_enabled", "on")
            self.assertFalse(manager.get("v2_motion_cover_enabled"))


class _Player:
    def __init__(self):
        self.state = "stopped"
        self.loops = 1
        self.source = None

    def setSource(self, url):
        self.source = url

    def setLoops(self, loops):
        self.loops = loops

    def play(self):
        self.state = "playing"

    def pause(self):
        self.state = "paused"

    def stop(self):
        self.state = "stopped"


class _Host:
    def __init__(self):
        self.visible = True
        self.minimized = False
        self.updates = 0

    def isVisible(self):
        return self.visible

    def isMinimized(self):
        return self.minimized

    def devicePixelRatioF(self):
        return 2.0

    def update(self, *_args):
        self.updates += 1


class PlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def tearDown(self):
        install_motion_cover(None)

    def test_clear_during_fade_stops_the_frame_timer(self):
        session = MotionCoverSession(enabled=False)
        self.addCleanup(session.shutdown)
        session._fade_at = time.monotonic()
        session._fade.start()
        self.assertTrue(session._fade.isActive())
        self.assertEqual(session._fade.interval(), 16)
        session.clear()
        self.assertFalse(session._fade.isActive())
        session._fade_at = 0.0
        session._fade.start()
        session._tick_fade()
        self.assertFalse(session._fade.isActive())

    def test_song_pause_pauses_the_video_and_resume_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "clip.mp4"
            video.write_bytes(b"not-a-real-decoder")
            player = _Player()
            session = MotionCoverSession(
                enabled=True,
                resolver=type("Resolver", (), {
                    "resolve": lambda self, track, cancel=None: MotionResult("hit", "netease", local_path=str(video)),
                    "cache": MotionCoverCache(Path(directory) / "cache"),
                })(),
                player_factory=lambda _callback: player,
            )
            self.addCleanup(session.shutdown)
            host = _Host()
            session.note_cover(host, 120, True, QRectF(0, 0, 120, 120))
            host.track_title = "晴天"
            host.artist = "周杰伦"
            host.media_track_id = "owned:netease:7"
            host.media_source_app = "网易云音乐"
            host.media_duration_seconds = 269
            host.album_queue = []
            host.media_is_playing = True
            session.note_track(motion_track_from_host(host))
            session.note_playback(True)
            for _ in range(40):
                self.app.processEvents()
                if player.state == "playing":
                    break
                time.sleep(0.02)
            self.assertEqual(player.state, "playing")
            source = player.source
            session.note_playback(False)
            self.assertEqual(player.state, "paused")
            self.assertEqual(player.source, source)
            session.note_playback(True)
            self.assertEqual(player.state, "playing")
            self.assertEqual(player.source, source)

    def test_end_of_a_pass_loops_and_moves_to_the_downloaded_file(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "clip.mp4"
            local.write_bytes(b"not-a-real-decoder")
            player = _Player()
            player.positions = []
            player.setPosition = player.positions.append
            session = MotionCoverSession(enabled=True, player_factory=lambda _callback: player)
            self.addCleanup(session.shutdown)
            host = _Host()
            session.note_cover(host, 120, True, QRectF(0, 0, 120, 120))
            session._key = "song"
            session._generation = 3
            session._on_ready(3, MotionResult("hit", "apple", "https://cdn.example/clip.mp4"))
            self.assertEqual(player.state, "playing")
            self.assertEqual(player.source.toString(), "https://cdn.example/clip.mp4")
            # The backend stops at the end of a streamed pass: the loop goes on.
            player.state = "stopped"
            session._on_media_status(type("Status", (), {"name": "EndOfMedia"})())
            self.assertEqual(player.positions, [0])
            self.assertEqual(player.state, "playing")
            # The download finishes mid-pass: it takes over at the next loop, not now.
            session._on_ready(3, MotionResult("hit", "apple", "https://cdn.example/clip.mp4", str(local)))
            self.assertEqual(player.source.toString(), "https://cdn.example/clip.mp4")
            session._on_media_status(type("Status", (), {"name": "EndOfMedia"})())
            self.assertEqual(player.source.toLocalFile().replace("/", "\\").lower(), str(local).lower())
            self.assertEqual(player.state, "playing")

    def test_a_lookup_that_timed_out_is_asked_again(self):
        with tempfile.TemporaryDirectory() as directory:
            answers = [MotionResult("none", "apple", final=False), MotionResult("hit", "apple", "https://cdn.example/a.mp4")]
            asked = []

            class Resolver:
                cache = MotionCoverCache(Path(directory))

                def resolve(self, track, cancel=None):
                    asked.append(track.title)
                    return answers[min(len(asked), len(answers)) - 1]

            from local import motion_cover_playback as playback
            session = MotionCoverSession(enabled=True, resolver=Resolver(), player_factory=lambda _callback: _Player())
            self.addCleanup(session.shutdown)
            with mock.patch.object(playback, "RETRY_SECONDS", (0.05, 0.05)):
                session.note_track(MotionTrack("晴天", "周杰伦"))
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline and not session._source:
                    self.app.processEvents()
                    time.sleep(0.02)
            self.assertEqual(len(asked), 2)
            self.assertEqual(session._source, "https://cdn.example/a.mp4")

    def test_pause_stop_crossfade_and_rounded_corner(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "clip.mp4"
            video.write_bytes(b"not-a-real-decoder")
            player = _Player()
            session = MotionCoverSession(
                enabled=True,
                resolver=type("Resolver", (), {
                    "resolve": lambda self, track, cancel=None: MotionResult("hit", "netease", local_path=str(video)),
                    "cache": MotionCoverCache(Path(directory) / "cache"),
                })(),
                player_factory=lambda _callback: player,
            )
            install_motion_cover(session)
            self.addCleanup(session.shutdown)
            host = _Host()
            session.note_cover(host, 120, True, QRectF(0, 0, 120, 120))
            host.track_title = "晴天"
            host.artist = "周杰伦"
            host.media_track_id = "owned:netease:7"
            host.media_source_app = "网易云音乐"
            host.media_duration_seconds = 269
            host.album_queue = []
            session.note_track(motion_track_from_host(host))
            for _ in range(40):
                self.app.processEvents()
                if player.state == "playing":
                    break
                time.sleep(0.02)
            self.assertEqual(player.state, "playing")
            self.assertIsInstance(player.source, QUrl)
            self.assertEqual(player.loops, -1)
            host.visible = False
            session.note_cover(host, 0, False, QRectF())
            self.assertEqual(player.state, "paused")
            session._hidden_since = time.monotonic() - 2
            session._sync_playback()
            self.assertEqual(player.state, "stopped")

            host.visible = True
            session.note_cover(host, 120, True, QRectF(0, 0, 120, 120))
            session._playing = True
            session._key = motion_track_from_host(host).cache_key()
            frame = QImage(800, 400, QImage.Format.Format_RGB32)
            frame.fill(QColor("blue"))
            session.supply_frame(frame)
            self.assertLessEqual(max(session._image.width(), session._image.height()), 240)
            self.assertGreater(session._image.width(), 0)
            canvas = QImage(32, 32, QImage.Format.Format_ARGB32)
            canvas.fill(QColor("red"))
            area = QRectF(0, 0, 32, 32)
            with QPainter(canvas) as painter:
                path = QPainterPath()
                path.addRoundedRect(area, 12, 12)
                painter.setClipPath(path)
                image, opacity = session.reveal(session._key)
                self.assertEqual(opacity, 0.0)
                paint_motion_cover(painter, area, session._key)
            self.assertEqual(canvas.pixelColor(16, 16), QColor("red"))
            session._fade_at = time.monotonic() - 5
            with QPainter(canvas) as painter:
                path = QPainterPath()
                path.addRoundedRect(area, 12, 12)
                painter.setClipPath(path)
                paint_motion_cover(painter, area, session._key)
            self.assertEqual(canvas.pixelColor(16, 16).blue(), 255)
            self.assertEqual(canvas.pixelColor(0, 0), QColor("red"))
            session.set_enabled(False)
            self.assertTrue(session._image.isNull())
            self.assertIsNone(shared_session().reveal(session._key)[0])


if __name__ == "__main__":
    unittest.main()
