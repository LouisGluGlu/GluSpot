from __future__ import annotations

import asyncio
from copy import deepcopy
import io
import json
from pathlib import Path
import threading
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage

from api.artwork_sources import (
    ARTWORK_JSON_BYTE_LIMIT, MusicBrainzArtworkResolver, MusicBrainzRequestGate,
    artwork_duration_matches, artwork_text_key, cover_art_archive_front_url,
    cover_art_archive_group_front_url, musicbrainz_release_group,
    musicbrainz_release_id, read_artwork_response,
)
from api.providers import EmptyIslandDataProvider, WindowsMediaProvider, empty_visual_data
from tests.test_provider_state import FakeLyricsStore, FakeSession


RELEASE_ID = "f77cb94a-0d13-4a76-a5ab-bfdfd5cebddb"
SECOND_RELEASE_ID = "19a73db0-0184-4ed9-a35d-07cbb68df5ed"
GROUP_ID = "c78abbe0-82a4-4868-9344-401ec34f5b69"
OTHER_GROUP_ID = "25c94f8e-676c-4944-9d3b-68f9d9f6fabb"
ART_URL = "https://archive.org/download/mbid-test/front.jpg"
BOUNDED_ART_URL = f"https://coverartarchive.org/release/{RELEASE_ID}/front-1200"


def recording_payload(title="Song", artist="Artist", album="Album", duration=240000):
    credit = [{"name": artist, "artist": {"name": artist}}]
    return {"count": 1, "recordings": [{
        "title": title, "length": duration, "artist-credit": credit,
        "releases": [{
            "id": RELEASE_ID, "title": album, "status": "Official", "artist-credit": credit,
            "media": [{"track": [{"title": title, "length": duration}]}],
        }],
    }]}


def artwork_payload():
    return {"release": f"https://musicbrainz.org/release/{RELEASE_ID}", "images": [
        {"front": True, "approved": True, "image": ART_URL},
    ]}


def chicago_group_payload(*, album="Xscape", duration=245440):
    payload = recording_payload("Chicago", "Michael Jackson", album, duration)
    first = payload["recordings"][0]["releases"][0]
    first["release-group"] = {"id": GROUP_ID, "title": "Xscape", "primary-type": "Album"}
    second = deepcopy(first)
    second["id"] = SECOND_RELEASE_ID
    second["title"] = "XSCAPE"
    payload["recordings"][0]["releases"].append(second)
    # The longer original version has the same displayed song name in some
    # catalogs; the original-clock duration excludes its different edition.
    original_version = deepcopy(payload["recordings"][0])
    original_version["length"] = 283000
    original_version["releases"][0]["media"][0]["track"][0]["length"] = 283000
    original_version["releases"][1]["media"][0]["track"][0]["length"] = 283000
    original_version["releases"][0]["release-group"]["id"] = OTHER_GROUP_ID
    original_version["releases"][1]["release-group"]["id"] = OTHER_GROUP_ID
    payload["recordings"].append(original_version)
    payload["count"] = 2
    return payload


def group_artwork_payload():
    # The real Xscape CAA release-group response names a member release and
    # offers only 250/500 thumbnail keys, although /front-1200 is available.
    return {
        "release": f"http://musicbrainz.org/release/{SECOND_RELEASE_ID}",
        "images": [{
            "front": True, "approved": True,
            "image": "http://coverartarchive.org/release/19a73db0-0184-4ed9-a35d-07cbb68df5ed/10047912847.jpg",
            "thumbnails": {
                "large": "http://coverartarchive.org/release/19a73db0-0184-4ed9-a35d-07cbb68df5ed/10047912847-500.jpg",
                "small": "http://coverartarchive.org/release/19a73db0-0184-4ed9-a35d-07cbb68df5ed/10047912847-250.jpg",
            },
        }],
    }


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class JsonResponse(io.BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode("utf-8"))


class ManualThread:
    jobs = []

    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.jobs.append(self.target)


class ManualTimer:
    timers = []

    def __init__(self, interval, callback):
        self.interval, self.callback = interval, callback
        self.cancelled = False

    def start(self):
        self.timers.append(self)

    def cancel(self):
        self.cancelled = True


class ArtworkMetadataTests(unittest.TestCase):
    def test_normalization_does_not_remove_version_or_punctuation(self):
        self.assertEqual(artwork_text_key("  SONG  (Live) "), "song (live)")
        self.assertNotEqual(artwork_text_key("Song"), artwork_text_key("Song (Live)"))
        self.assertTrue(artwork_duration_matches(240, 242, require_known=True))
        self.assertFalse(artwork_duration_matches(240, 242.01, require_known=True))
        self.assertFalse(artwork_duration_matches(240, 0, require_known=True))
        self.assertFalse(artwork_duration_matches(float("nan"), 240))

    def test_musicbrainz_requires_complete_matching_release_track(self):
        self.assertEqual(musicbrainz_release_id(recording_payload(), "song", "Artist", "Album", 240), RELEASE_ID)
        mutations = [
            lambda p: p["recordings"][0].update(title="Song (Remix)"),
            lambda p: p["recordings"][0].update(length=0),
            lambda p: p["recordings"][0].update(video=True),
            lambda p: p["recordings"][0]["releases"][0].update(title="Other Album"),
            lambda p: p["recordings"][0]["releases"][0].update(status="Bootleg"),
            lambda p: p["recordings"][0]["releases"][0].update(**{"artist-credit": []}),
            lambda p: p["recordings"][0]["releases"][0]["media"][0]["track"][0].update(title="Song (Live)"),
            lambda p: p["recordings"][0]["releases"][0]["media"][0]["track"][0].update(length=245000),
            lambda p: p.update(count=13),
        ]
        for mutation in mutations:
            payload = recording_payload()
            mutation(payload)
            with self.subTest(payload=payload):
                self.assertEqual(musicbrainz_release_id(payload, "Song", "Artist", "Album", 240), "")

    def test_ambiguous_release_editions_are_not_first_hit(self):
        payload = recording_payload()
        alternate = deepcopy(payload["recordings"][0]["releases"][0])
        alternate["id"] = SECOND_RELEASE_ID
        payload["recordings"][0]["releases"].append(alternate)
        self.assertEqual(musicbrainz_release_id(payload, "Song", "Artist", "Album", 240), "")

    def test_chicago_editions_share_one_verified_album_group(self):
        payload = chicago_group_payload()
        self.assertEqual(musicbrainz_release_id(payload, "Chicago", "Michael Jackson", "Xscape", 245.44), "")
        self.assertEqual(
            musicbrainz_release_group(payload, "Chicago", "Michael Jackson", "Xscape", 245.44),
            (GROUP_ID, frozenset((RELEASE_ID, SECOND_RELEASE_ID))),
        )
        self.assertEqual(
            musicbrainz_release_group(payload, "Chicago", "Michael Jackson", "", 245.44),
            (GROUP_ID, frozenset((RELEASE_ID, SECOND_RELEASE_ID))),
        )
        self.assertEqual(
            musicbrainz_release_group(payload, "Chicago", "Michael Jackson", "", 283)[0],
            OTHER_GROUP_ID,
        )

    def test_multiple_or_unknown_album_groups_stay_ambiguous(self):
        payload = chicago_group_payload()
        payload["recordings"][0]["releases"][1]["release-group"]["id"] = OTHER_GROUP_ID
        self.assertEqual(musicbrainz_release_group(payload, "Chicago", "Michael Jackson", "", 245.44), ("", frozenset()))
        payload["recordings"][0]["releases"][1].pop("release-group")
        self.assertEqual(musicbrainz_release_group(payload, "Chicago", "Michael Jackson", "", 245.44), ("", frozenset()))

    def test_group_front_requires_approved_art_from_verified_member(self):
        expected = f"https://coverartarchive.org/release-group/{GROUP_ID}/front-1200"
        verified = frozenset((RELEASE_ID, SECOND_RELEASE_ID))
        self.assertEqual(cover_art_archive_group_front_url(group_artwork_payload(), GROUP_ID, verified), expected)
        art = group_artwork_payload()
        art["images"][0]["thumbnails"]["1200"] = "http://coverartarchive.org/release/thumb-1200.jpg"
        self.assertEqual(
            cover_art_archive_group_front_url(art, GROUP_ID, verified),
            "https://coverartarchive.org/release/thumb-1200.jpg",
        )
        mutations = [
            lambda p: p.update(release="https://musicbrainz.org/release/00000000-0000-0000-0000-000000000000"),
            lambda p: p["images"][0].update(approved=False),
            lambda p: p["images"][0].update(front=False),
            lambda p: p["images"][0].update(image="https://archive.org.evil.test/front.jpg"),
        ]
        for mutation in mutations:
            art = group_artwork_payload()
            mutation(art)
            self.assertEqual(cover_art_archive_group_front_url(art, GROUP_ID, verified), "")

    def test_caa_requires_matching_release_approved_front_and_trusted_host(self):
        self.assertEqual(cover_art_archive_front_url(artwork_payload(), RELEASE_ID), ART_URL)
        mutations = [
            lambda p: p.update(release=f"https://musicbrainz.org/release/{SECOND_RELEASE_ID}"),
            lambda p: p["images"][0].update(front=False),
            lambda p: p["images"][0].update(approved=False),
            lambda p: p["images"][0].update(image="https://archive.org.evil.test/front.jpg"),
            lambda p: p["images"][0].update(image="https://user:secret@archive.org/front.jpg"),
            lambda p: p.update(release=f"ftp://musicbrainz.org/release/{RELEASE_ID}"),
            lambda p: p.update(release=f"https://user:secret@musicbrainz.org/release/{RELEASE_ID}"),
            lambda p: p.update(release=f"https://musicbrainz.org/release/{RELEASE_ID}/"),
        ]
        for mutation in mutations:
            payload = artwork_payload()
            mutation(payload)
            self.assertEqual(cover_art_archive_front_url(payload, RELEASE_ID), "")
            self.assertEqual(cover_art_archive_front_url(payload, RELEASE_ID, bounded=True), "")

    def test_unique_release_uses_bounded_front_without_downgrading_to_small_thumbnails(self):
        payload = artwork_payload()
        payload["images"][0]["thumbnails"] = {"250": "https://archive.org/front-250.jpg"}
        self.assertEqual(cover_art_archive_front_url(payload, RELEASE_ID, bounded=True), BOUNDED_ART_URL)
        self.assertEqual(cover_art_archive_front_url(payload, "invalid", bounded=True), "")
        payload["images"][0]["thumbnails"]["1200"] = "http://archive.org/front-1200.jpg"
        self.assertEqual(
            cover_art_archive_front_url(payload, RELEASE_ID, bounded=True),
            "https://archive.org/front-1200.jpg",
        )
        for invalid in ("https://archive.org.evil.test/front.jpg", "https://user:secret@archive.org/front.jpg"):
            payload["images"][0]["thumbnails"]["1200"] = invalid
            self.assertEqual(cover_art_archive_front_url(payload, RELEASE_ID, bounded=True), BOUNDED_ART_URL)

    def test_size_variant_is_taken_only_from_the_verified_front_image(self):
        payload = artwork_payload()
        invalid = deepcopy(payload["images"][0])
        invalid.update(image="https://untrusted.test/front.jpg", thumbnails={"1200": "https://archive.org/wrong-1200.jpg"})
        payload["images"].insert(0, invalid)
        self.assertEqual(cover_art_archive_front_url(payload, RELEASE_ID, bounded=True), BOUNDED_ART_URL)
        self.assertEqual(
            cover_art_archive_group_front_url(payload, GROUP_ID, frozenset((RELEASE_ID,))),
            f"https://coverartarchive.org/release-group/{GROUP_ID}/front-1200",
        )

    def test_bounded_read1_observes_deadline_between_chunks(self):
        clock = Clock()

        class Trickle(io.BytesIO):
            calls = 0

            def read1(self, size):
                self.calls += 1
                clock.now += 0.4
                return super().read1(min(size, 1))

        response = Trickle(b"123456")
        with self.assertRaises(TimeoutError):
            read_artwork_response(response, 100.7, clock=clock)
        self.assertEqual(response.calls, 2)
        with self.assertRaises(ValueError):
            read_artwork_response(io.BytesIO(b"x" * (ARTWORK_JSON_BYTE_LIMIT + 1)), 200, clock=clock)


class ArtworkResolverTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.gate = MusicBrainzRequestGate(clock=self.clock, sleep=self.clock.sleep)
        self.resolver = MusicBrainzArtworkResolver(clock=self.clock, request_gate=self.gate)

    def test_unknown_metadata_skips_network(self):
        self.resolver._fetch_json = Mock()
        for args in [("Song", "Artist", "", 0), ("Song", "Artist", "Album", 0), ("Song", "", "Album", 240)]:
            self.assertEqual(self.resolver.resolve(*args), "")
        self.resolver._fetch_json.assert_not_called()

    def test_missing_album_uses_unique_verified_group_and_bounded_front(self):
        payload = chicago_group_payload()
        art = group_artwork_payload()
        self.resolver._fetch_json = Mock(side_effect=[payload, art])
        expected = f"https://coverartarchive.org/release-group/{GROUP_ID}/front-1200"
        self.assertEqual(self.resolver.resolve("Chicago", "Michael Jackson", "", 245.44), expected)
        self.assertEqual(self.resolver.resolve("Chicago", "Michael Jackson", "", 245.44), expected)
        self.assertEqual(self.resolver._fetch_json.call_count, 2)
        query = parse_qs(urlsplit(self.resolver._fetch_json.call_args_list[0].args[0]).query)["query"][0]
        self.assertIn('recording:"Chicago"', query)
        self.assertNotIn("release:", query)
        self.assertEqual(
            self.resolver._fetch_json.call_args_list[1].args[0],
            f"https://coverartarchive.org/release-group/{GROUP_ID}",
        )

    def test_group_lookup_can_use_seven_seconds_within_provider_deadline(self):
        def fetch(url, deadline):
            self.clock.now += 3.6 if "musicbrainz.org/ws/" in url else 3.7
            if self.clock.now >= deadline:
                raise TimeoutError("metadata delivery budget expired")
            return chicago_group_payload() if "musicbrainz.org/ws/" in url else group_artwork_payload()

        self.resolver._fetch_json = fetch
        self.assertEqual(
            self.resolver.resolve("Chicago", "Michael Jackson", "", 245.44, deadline=109.0),
            f"https://coverartarchive.org/release-group/{GROUP_ID}/front-1200",
        )
        self.assertLess(self.clock.now, 109.0)

    def test_different_matching_groups_do_not_request_caa(self):
        payload = chicago_group_payload()
        payload["recordings"][0]["releases"][1]["release-group"]["id"] = OTHER_GROUP_ID
        self.resolver._fetch_json = Mock(return_value=payload)
        self.assertEqual(self.resolver.resolve("Chicago", "Michael Jackson", "", 245.44), "")
        self.resolver._fetch_json.assert_called_once()

    def test_success_query_escaping_identifying_ua_and_positive_cache(self):
        payload = recording_payload(title='Song "Live"')
        with patch("api.artwork_sources.urlopen", side_effect=[JsonResponse(payload), JsonResponse(artwork_payload())]) as opener:
            url = self.resolver.resolve('Song "Live"', "Artist", "Album", 240)
            self.assertEqual(url, BOUNDED_ART_URL)
            self.assertEqual(self.resolver.resolve('Song "Live"', "Artist", "Album", 240), BOUNDED_ART_URL)
        self.assertEqual(opener.call_count, 2)
        first = opener.call_args_list[0].args[0]
        query = parse_qs(urlsplit(first.full_url).query)["query"][0]
        self.assertIn(r'recording:"Song \"Live\""', query)
        self.assertIn("GluSpot", first.get_header("User-agent"))
        self.assertEqual(opener.call_args_list[1].args[0].full_url, f"https://coverartarchive.org/release/{RELEASE_ID}")

    def test_normal_miss_negative_cache_and_expiry(self):
        self.resolver._fetch_json = Mock(return_value={"count": 0, "recordings": []})
        self.assertEqual(self.resolver.resolve("Song", "Artist", "Album", 240), "")
        self.assertEqual(self.resolver.resolve("Song", "Artist", "Album", 240), "")
        self.assertEqual(self.resolver._fetch_json.call_count, 1)
        self.clock.now += self.resolver.MISS_SECONDS + 0.1
        self.resolver.resolve("Song", "Artist", "Album", 240)
        self.assertEqual(self.resolver._fetch_json.call_count, 2)

    def test_network_failure_cools_source_for_other_tracks(self):
        self.resolver._fetch_json = Mock(side_effect=OSError("offline"))
        self.resolver.resolve("Song", "Artist", "Album", 240)
        self.resolver.resolve("Other", "Artist", "Album", 240)
        self.assertEqual(self.resolver._fetch_json.call_count, 1)
        self.clock.now += self.resolver.FAILURE_SECONDS + 0.1
        self.resolver.resolve("Other", "Artist", "Album", 240)
        self.assertEqual(self.resolver._fetch_json.call_count, 2)

    def test_cache_is_bounded(self):
        self.resolver._fetch_json = Mock(return_value={"count": 0, "recordings": []})
        for index in range(self.resolver.CACHE_LIMIT + 5):
            self.resolver.resolve(f"Song {index}", "Artist", "Album", 240)
        self.assertEqual(len(self.resolver._cache), self.resolver.CACHE_LIMIT)

    def test_process_gate_spaces_different_resolvers(self):
        starts = []
        second = MusicBrainzArtworkResolver(clock=self.clock, request_gate=self.gate)
        for resolver in (self.resolver, second):
            resolver._fetch_json = lambda *_args: starts.append(self.clock()) or {"count": 0, "recordings": []}
            resolver.resolve("Song", "Artist", "Album", 240)
        self.assertGreaterEqual(starts[1] - starts[0], 1.0)
        self.assertFalse(self.gate.wait(self.clock() + 0.1))

    def test_same_metadata_concurrent_resolve_is_singleflight(self):
        started, release = threading.Event(), threading.Event()
        calls = []

        def fetch(url, _deadline):
            calls.append(url)
            if "musicbrainz.org/ws/" in url:
                started.set()
                if not release.wait(2):
                    raise TimeoutError("test worker was not released")
                return recording_payload()
            return artwork_payload()

        self.resolver._fetch_json = fetch
        results = []
        worker = threading.Thread(target=lambda: results.append(self.resolver.resolve("Song", "Artist", "Album", 240)))
        worker.start()
        try:
            self.assertTrue(started.wait(2))
            self.assertEqual(self.resolver.resolve("Song", "Artist", "Album", 240), "")
            self.assertEqual(len(calls), 1)
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [BOUNDED_ART_URL])
        self.assertEqual(self.resolver.resolve("Song", "Artist", "Album", 240), BOUNDED_ART_URL)
        self.assertEqual(len(calls), 2)


class ArtworkProviderLadderTests(unittest.TestCase):
    def setUp(self):
        self.provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        self.provider._online_current_art_lookup_enabled = True
        self.provider._music_player_adapter = "netease"
        self.provider._resolve_netease_art_url = Mock(return_value="")
        self.provider._musicbrainz_artwork_resolver = SimpleNamespace(resolve=Mock(return_value=ART_URL))
        self.provider._schedule_queue_album_art_prefetch = Mock()
        ManualThread.jobs, ManualTimer.timers = [], []
        self.thread_patch = patch("api.providers.threading.Thread", ManualThread)
        self.timer_patch = patch("api.providers.threading.Timer", ManualTimer)
        self.thread_patch.start()
        self.timer_patch.start()
        self.addCleanup(self.thread_patch.stop)
        self.addCleanup(self.timer_patch.stop)

    def schedule(self, track="track"):
        return self.provider._schedule_current_netease_art_lookup(track, "Song", "Artist", album_title="Album", duration_seconds=240)

    def test_same_platform_then_caa_and_prefetch_has_true_source(self):
        self.assertTrue(self.schedule())
        self.assertFalse(self.schedule())
        ManualThread.jobs.pop(0)()
        self.provider._resolve_netease_art_url.assert_called_once()
        self.assertEqual(self.provider._current_art_url_by_track_id["track"], ART_URL)
        self.assertEqual(self.provider._current_art_lookup_sources["track"], "cover-art-archive")
        self.provider._schedule_queue_album_art_prefetch.assert_called_once_with(ART_URL)
        self.assertTrue(ManualTimer.timers[0].cancelled)

    def test_netease_hit_does_not_query_musicbrainz(self):
        self.provider._resolve_netease_art_url.return_value = "https://music.126.net/front.jpg"
        self.schedule()
        ManualThread.jobs.pop(0)()
        self.provider._musicbrainz_artwork_resolver.resolve.assert_not_called()
        self.assertEqual(self.provider._current_art_lookup_sources["track"], "netease")

    def test_other_player_skips_netease_search(self):
        self.provider._music_player_adapter = "qq"
        self.schedule()
        self.provider._music_player_adapter = "netease"
        ManualThread.jobs.pop(0)()
        self.provider._resolve_netease_art_url.assert_not_called()
        self.provider._musicbrainz_artwork_resolver.resolve.assert_called_once()

    def test_netease_cover_fallback_survives_disabled_progress_sync(self):
        self.provider._music_player_adapter = "system"
        self.provider._raw_source_app_user_model_id = "D:\\Apps\\CloudMusic\\cloudmusic.exe"
        self.provider._resolve_netease_art_url.return_value = "https://music.126.net/front.jpg"
        self.assertTrue(self.provider._schedule_current_netease_art_lookup("track", "Song", "Artist"))
        # The source must be captured with the attempt, before another song
        # can change the active session while this worker waits to run.
        self.provider._raw_source_app_user_model_id = "QQMusic.exe"
        ManualThread.jobs.pop(0)()
        self.provider._resolve_netease_art_url.assert_called_once()
        self.provider._musicbrainz_artwork_resolver.resolve.assert_not_called()
        self.assertEqual(self.provider._current_art_lookup_sources["track"], "netease")

    def test_rejected_download_moves_to_next_source(self):
        bad_url = "https://music.126.net/front.jpg"
        self.provider._current_art_url_by_track_id = {"track": bad_url}
        self.provider._queue_art_failed_until = {bad_url: 1000.0}
        self.provider._resolve_netease_art_url.return_value = bad_url
        with patch("api.providers.time.monotonic", return_value=100.0):
            self.schedule()
            ManualThread.jobs.pop(0)()
        self.assertEqual(self.provider._current_art_url_by_track_id["track"], ART_URL)

    def test_expired_generation_cannot_publish_into_retry(self):
        clock = Clock()
        self.provider._resolve_netease_art_url.side_effect = ["https://test/old.jpg", "https://test/new.jpg"]
        with patch("api.providers.time.monotonic", clock):
            self.assertTrue(self.schedule())
            old_worker = ManualThread.jobs.pop(0)
            clock.now += 9.0
            ManualTimer.timers[0].callback()
            self.assertFalse(self.schedule())
            clock.now += 45.1
            self.assertTrue(self.schedule())
            new_worker = ManualThread.jobs.pop(0)
            old_worker()
            self.assertNotIn("track", self.provider._current_art_url_by_track_id)
            new_worker()
        self.assertEqual(self.provider._current_art_url_by_track_id["track"], "https://test/new.jpg")
        self.provider._schedule_queue_album_art_prefetch.assert_called_once_with("https://test/new.jpg")

    def test_metadata_workers_and_track_urls_are_bounded(self):
        self.assertTrue(self.schedule("a"))
        self.assertTrue(self.schedule("b"))
        self.assertFalse(self.schedule("c"))
        for worker in list(ManualThread.jobs):
            worker()
        ManualThread.jobs.clear()
        for index in range(30):
            self.assertTrue(self.schedule(str(index)))
            ManualThread.jobs.pop(0)()
        self.assertEqual(len(self.provider._current_art_url_by_track_id), 24)
        self.assertEqual(len(self.provider._current_art_lookup_sources), 24)


class NeteaseArtworkMetadataTests(unittest.TestCase):
    def resolve(self, songs, **kwargs):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        with patch("api.providers.urlopen", return_value=JsonResponse({"result": {"songs": songs}})):
            return provider._resolve_netease_art_url("Song", "Artist", **kwargs)

    @staticmethod
    def song(**changes):
        result = {"name": "Song", "artists": [{"name": "Artist"}], "duration": 240000,
                  "album": {"id": 1, "name": "Album", "picUrl": "https://music.126.net/front.jpg"}}
        result.update(changes)
        return result

    def test_album_duration_version_and_artist_are_gates(self):
        wrong_album = self.song(album={"id": 2, "name": "Different", "picUrl": "https://test/wrong.jpg"})
        rows = [wrong_album, self.song(duration=245000), self.song(name="Song (Live)"), self.song(artists=[{"name": "Other Artist"}]), self.song()]
        self.assertEqual(self.resolve(rows, album_title="Album", duration_seconds=240), "https://music.126.net/front.jpg?param=2560y2560")
        for song in rows[:-1]:
            self.assertEqual(self.resolve([song], album_title="Album", duration_seconds=240), "")
        self.assertEqual(self.resolve([self.song(duration=0)], duration_seconds=240), "")

    def test_unknown_album_retains_reference_first_matching_result(self):
        other = self.song(album={"id": 2, "name": "Compilation", "picUrl": "https://test/wrong.jpg"})
        self.assertEqual(self.resolve([self.song(), other]), "https://music.126.net/front.jpg?param=2560y2560")

    def test_chicago_same_named_album_accepts_separate_catalog_ids(self):
        songs = [self.song(name="Chicago", artists=[{"name": "Michael Jackson"}], duration=245000,
                           album={"id": 1, "name": "XSCAPE", "picUrl": "https://p1.music.126.net/front.jpg"}),
                 self.song(name="Chicago", artists=[{"name": "Michael Jackson"}], duration=245440,
                           album={"id": 2, "name": "Xscape", "picUrl": "https://p2.music.126.net/front.jpg"})]
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        with patch("api.providers.urlopen", return_value=JsonResponse({"result": {"songs": songs}})):
            self.assertEqual(
                provider._resolve_netease_art_url("Chicago", "Michael Jackson", album_title="Xscape", duration_seconds=245.44),
                "https://p1.music.126.net/front.jpg?param=2560y2560",
            )
        with patch("api.providers.urlopen", return_value=JsonResponse({"result": {"songs": songs}})):
            self.assertEqual(
                provider._resolve_netease_art_url("Chicago", "Michael Jackson", duration_seconds=245.44),
                "https://p1.music.126.net/front.jpg?param=2560y2560",
            )

    def test_same_album_name_never_bypasses_duration_or_version(self):
        rows = [self.song(name="Song (Live)", album={"id": 2, "name": "Album", "picUrl": "https://p2.music.126.net/wrong.jpg"}),
                self.song(duration=283000, album={"id": 3, "name": "Album", "picUrl": "https://p3.music.126.net/wrong.jpg"}),
                self.song()]
        self.assertEqual(self.resolve(rows, album_title="Album", duration_seconds=240), "https://music.126.net/front.jpg?param=2560y2560")


class ArtworkVisualPriorityTests(unittest.TestCase):
    @staticmethod
    def png(side, color):
        image = QImage(side, side, QImage.Format.Format_RGB32)
        image.fill(color)
        data, buffer = QByteArray(), QBuffer()
        buffer.setBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        return bytes(data)

    def provider(self, thumbnail):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.fallback = EmptyIslandDataProvider(empty_visual_data())
        provider.lyrics_store = FakeLyricsStore()
        provider._last_data = None
        provider._last_ready_at = provider._source_app_foreground_at = 0.0
        provider._last_raw_timeline_track_id = provider._local_seek_track_id = ""
        provider._last_raw_timeline_position = provider._local_seek_position_seconds = None
        provider._last_raw_timeline_at = provider._local_seek_at = 0.0
        provider._art_cache = {}
        provider.netease_position = provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_music_player_mode": "netease", "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False, "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda *_args: None
        provider._is_source_app_foreground = lambda *_args: False
        provider._read_thumbnail = lambda *_args: asyncio.sleep(0, result=thumbnail)
        provider._colors_from_art = lambda _art, fallback, secondary: (fallback, secondary)
        provider._get_session = lambda: asyncio.sleep(0, result=FakeSession("cloudmusic.exe", "Song", "Artist", True, 12, 240))
        provider._schedule_current_netease_art_lookup = Mock(return_value=True)
        return provider

    def test_caa_bytes_have_caa_provenance_and_retain_it(self):
        original = self.png(1200, 0xFF203060)
        provider = self.provider(self.png(48, 0xFF806020))
        first = asyncio.run(provider._read_visual_data())
        provider._last_data = first
        provider._current_art_url_by_track_id = {first.track.track_id: ART_URL}
        provider._current_art_lookup_sources = {first.track.track_id: "cover-art-archive"}
        provider._queue_album_art_bytes = lambda _track: original
        second = asyncio.run(provider._read_visual_data())
        self.assertEqual(second.track.album_art_source, "cover-art-archive-original")
        provider._last_data = second
        provider._current_art_url_by_track_id = {}
        third = asyncio.run(provider._read_visual_data())
        self.assertEqual(third.track.album_art_source, "cover-art-archive-original")
        self.assertEqual(third.track.album_art_bytes, original)

    def test_player_original_does_not_require_requested_2560_to_win_search(self):
        player_art, fallback_art = self.png(1200, 0xFF203060), self.png(1600, 0xFF806020)
        provider = self.provider(b"")
        provider._player_window_track_metadata = lambda *_args: SimpleNamespace(title="Song", artist="Artist", album_title="Album", album_art_bytes=player_art)
        provider._queue_album_art_bytes = lambda _track: player_art
        provider._current_online_album_art_bytes = lambda _track: fallback_art
        data = asyncio.run(provider._read_visual_data())
        self.assertEqual(data.track.album_art_bytes, player_art)
        self.assertEqual(data.track.album_art_source, "original")
        self.assertEqual(data.track.album_art_lookup_state, "ready")
        provider._schedule_current_netease_art_lookup.assert_not_called()

    def test_new_subtarget_player_original_keeps_larger_retained_fallback(self):
        player_art, fallback_art = self.png(1200, 0xFF203060), self.png(1600, 0xFF806020)
        provider = self.provider(b"")
        provider._current_online_album_art_bytes = lambda _track: fallback_art
        first = asyncio.run(provider._read_visual_data())
        provider._last_data = first
        provider._player_window_track_metadata = lambda *_args: SimpleNamespace(title="Song", artist="Artist", album_title="", album_art_bytes=player_art)
        provider._queue_album_art_bytes = lambda _track: player_art
        second = asyncio.run(provider._read_visual_data())
        self.assertEqual(second.track.track_id, first.track.track_id)
        self.assertEqual(second.track.album_art_bytes, fallback_art)

    def test_target_size_player_original_wins_larger_fallback(self):
        player_art, fallback_art = self.png(2560, 0xFF203060), self.png(3200, 0xFF806020)
        provider = self.provider(b"")
        provider._player_window_track_metadata = lambda *_args: SimpleNamespace(title="Song", artist="Artist", album_title="Album", album_art_bytes=player_art)
        provider._queue_album_art_bytes = lambda _track: player_art
        provider._current_online_album_art_bytes = lambda _track: fallback_art
        data = asyncio.run(provider._read_visual_data())
        self.assertEqual(data.track.album_art_bytes, player_art)
        self.assertEqual(data.track.album_art_source, "original")
        provider._schedule_current_netease_art_lookup.assert_not_called()

    def test_tiny_player_bytes_remain_thumbnail_until_original_arrives(self):
        tiny = self.png(100, 0xFF203060)
        provider = self.provider(b"")
        provider._player_window_track_metadata = lambda *_args: SimpleNamespace(
            title="Song", artist="Artist", album_title="Album", album_art_bytes=tiny,
        )
        provider._queue_album_art_bytes = lambda _track: tiny
        first = asyncio.run(provider._read_visual_data())
        self.assertEqual(first.track.album_art_source, "thumbnail")
        self.assertNotEqual(first.track.album_art_lookup_state, "ready")
        provider._schedule_current_netease_art_lookup.assert_called_once()

    def test_1417_source_original_is_ready_without_new_search(self):
        original = self.png(1417, 0xFF203060)
        provider = self.provider(b"")
        provider._player_window_track_metadata = lambda *_args: SimpleNamespace(
            title="Song", artist="Artist", album_title="Album", album_art_bytes=original,
        )
        provider._queue_album_art_bytes = lambda _track: original
        data = asyncio.run(provider._read_visual_data())
        self.assertEqual(data.track.album_art_source, "original")
        self.assertEqual(data.track.album_art_lookup_state, "ready")
        provider._schedule_current_netease_art_lookup.assert_not_called()


class ArtworkPlayerUrlUpgradeTests(unittest.TestCase):
    thumb_url = "https://p1.music.126.net/cover/123.jpg?param=100y100"
    original_url = "https://p1.music.126.net/cover/123.jpg?param=2560y2560"

    def setUp(self):
        self.provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        self.provider._queue_art_cache = {}
        self.provider._queue_art_disk_path = lambda _url: None
        self.provider._persist_queue_art = lambda *_args: None
        self.preview = ArtworkVisualPriorityTests.png(100, 0xFF203060)
        self.original = ArtworkVisualPriorityTests.png(1417, 0xFF806020)
        self.track = SimpleNamespace(album_art_bytes=self.preview, album_art_url=self.thumb_url)
        ManualThread.jobs = []
        self.thread_patch = patch("api.providers.threading.Thread", ManualThread)
        self.thread_patch.start()
        self.addCleanup(self.thread_patch.stop)

    def test_normalized_original_cache_wins_supplied_preview(self):
        self.provider._queue_art_cache[self.original_url] = self.original
        self.assertEqual(self.provider._queue_album_art_bytes(self.track), self.original)
        self.assertEqual(ManualThread.jobs, [])

    def test_preview_kept_while_known_original_download_is_singleflight(self):
        self.provider._queue_art_cache[self.thumb_url] = self.preview
        with patch("api.providers.urlopen", side_effect=AssertionError("network entered media poll")):
            self.assertEqual(self.provider._queue_album_art_bytes(self.track), self.preview)
            self.assertEqual(self.provider._queue_album_art_bytes(self.track), self.preview)
        self.assertEqual(len(ManualThread.jobs), 1)
        self.assertEqual(self.provider._queue_art_inflight, {self.original_url})

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with patch("api.providers.urlopen", return_value=Response(self.original)) as opener:
            ManualThread.jobs.pop(0)()
            self.assertEqual(opener.call_args.args[0].full_url, self.original_url)
            self.assertEqual(self.provider._queue_album_art_bytes(self.track), self.original)
            self.assertFalse(self.provider._schedule_queue_album_art_prefetch(self.original_url))
        self.assertEqual(ManualThread.jobs, [])

    def test_rejected_normalized_size_can_download_untouched_url(self):
        self.provider._queue_art_failed_until = {self.original_url: 130.0}
        with patch("api.providers.time.monotonic", return_value=100.0):
            self.assertEqual(self.provider._queue_album_art_bytes(self.track), self.preview)
            self.assertEqual(self.provider._queue_album_art_bytes(self.track), self.preview)
        self.assertEqual(self.provider._queue_art_inflight, {self.thumb_url})
        self.assertEqual(len(ManualThread.jobs), 1)

    def test_original_retry_is_allowed_when_failure_cooldown_expires(self):
        self.provider._queue_art_failed_until = {self.original_url: 130.0}
        self.provider._queue_art_cache[self.thumb_url] = self.preview
        with patch("api.providers.time.monotonic", return_value=100.0):
            self.assertEqual(self.provider._queue_album_art_bytes(self.track), self.preview)
        self.assertEqual(ManualThread.jobs, [])
        with patch("api.providers.time.monotonic", return_value=130.1):
            self.assertEqual(self.provider._queue_album_art_bytes(self.track), self.preview)
        self.assertEqual(len(ManualThread.jobs), 1)
        self.assertEqual(self.provider._queue_art_inflight, {self.original_url})

    def test_tiny_cached_source_is_evicted_and_can_recover_after_cooldown(self):
        provider = self.provider
        provider._online_current_art_lookup_enabled = True
        provider._music_player_adapter = "netease"
        provider._current_art_url_by_track_id = {"track": self.original_url}
        provider._queue_art_cache[self.original_url] = self.preview
        provider._resolve_netease_art_url = Mock(return_value=self.original_url)
        provider._musicbrainz_artwork_resolver = SimpleNamespace(resolve=Mock(return_value=""))
        original_track = SimpleNamespace(album_art_bytes=self.preview, album_art_url=self.original_url)
        ManualTimer.timers = []
        clock = Clock()

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with tempfile.TemporaryDirectory() as root, patch("api.providers.time.monotonic", clock), patch(
            "api.providers.threading.Timer", ManualTimer
        ):
            disk_path = Path(root) / "cover.img"
            disk_path.write_bytes(self.preview)
            provider._queue_art_disk_path = lambda _url: disk_path
            self.assertTrue(provider._schedule_current_netease_art_lookup("track", "Song", "Artist"))
            self.assertNotIn(self.original_url, provider._queue_art_cache)
            self.assertFalse(disk_path.exists())
            ManualThread.jobs.pop(0)()
            self.assertEqual(provider._queue_album_art_bytes(original_track), self.preview)
            self.assertFalse(provider._schedule_current_netease_art_lookup("track", "Song", "Artist"))
            self.assertEqual(ManualThread.jobs, [])

            clock.now += 45.1
            self.assertTrue(provider._schedule_current_netease_art_lookup("track", "Song", "Artist"))
            ManualThread.jobs.pop(0)()
            self.assertEqual(provider._current_art_url_by_track_id["track"], self.original_url)
            with patch("api.providers.urlopen", return_value=Response(self.original)):
                ManualThread.jobs.pop(0)()
            self.assertEqual(provider._queue_album_art_bytes(original_track), self.original)
            self.assertEqual(
                provider._current_artwork_lookup_state("track", self.original, eligible=True, source="netease-original"),
                "ready",
            )
            self.assertEqual(ManualThread.jobs, [])
