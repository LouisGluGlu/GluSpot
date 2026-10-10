from __future__ import annotations

import io
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

from api.lyrics import LocalLyricsStore
from api.providers import WindowsMediaProvider


class JsonResponse(io.BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.headers = Message()
        self.headers["Content-Type"] = "application/json; charset=utf-8"


CATALOG = {"code": 200, "result": {"songs": [
    {"id": 12, "name": "Song (Live)", "ar": [{"name": "Artist"}], "al": {"name": "Album", "picUrl": "https://example.test/wrong.jpg"}},
    {"id": 34, "name": "Song", "ar": [{"name": "Artist"}], "al": {"name": "Album", "picUrl": "https://example.test/right.jpg"}, "dt": 180000},
]}}


class NeteaseCatalogRecoveryTests(unittest.TestCase):
    def test_artwork_search_posts_metadata_and_preserves_recording_identity(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        with patch("api.providers.urlopen", return_value=JsonResponse(CATALOG)) as fetch:
            result = provider._resolve_netease_art_url("Song", "Artist", album_title="Album", duration_seconds=180)
        self.assertEqual(result, "https://example.test/right.jpg")
        request = fetch.call_args.args[0]
        self.assertEqual(request.full_url, "https://music.163.com/api/cloudsearch/pc")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(parse_qs(request.data.decode())["s"], ["Song Artist"])

    def test_encrypted_or_malformed_catalog_is_a_bounded_failure(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        for payload in ({"code": 200, "result": "encrypted"}, {"code": 200, "result": None}, {"code": -462}):
            with self.subTest(payload=payload), patch("api.providers.urlopen", return_value=JsonResponse(payload)):
                with self.assertRaises(ValueError):
                    provider._resolve_netease_art_url("Song", "Artist")

    def test_lyrics_search_and_latest_version_request_use_actual_transport(self):
        requests = []

        def fetch(request, **_kwargs):
            requests.append(request)
            if len(requests) == 1:
                return JsonResponse(CATALOG)
            # Source version 1 is a valid newly requested song; request -1
            # must retrieve it instead of treating version 1 as already held.
            params = parse_qs(request.full_url.split("?", 1)[1])
            return JsonResponse({"code": 200, "lrc": {"version": 1, "lyric":
                "[00:01.00]line one\n[00:03.00]line two" if params["lv"] == ["-1"] else ""}})

        with tempfile.TemporaryDirectory() as folder:
            store = LocalLyricsStore(Path(folder))
            with patch("api.lyrics.urlopen", side_effect=fetch):
                result = store._resolve_netease("Song", "Artist", "Album", 180)
        self.assertIsNotNone(result)
        self.assertEqual(len(result.timed_lyrics), 2)
        self.assertEqual(requests[0].get_method(), "POST")
        self.assertEqual(requests[0].full_url, "https://music.163.com/api/cloudsearch/pc")
        self.assertEqual(requests[1].get_method(), "GET")
        query = parse_qs(requests[1].full_url.split("?", 1)[1])
        self.assertEqual(query["id"], ["34"])
        self.assertTrue(all(query[key] == ["-1"] for key in ("lv", "kv", "tv", "rv", "yv")))

    def test_cloud_catalog_album_alias_is_retained_for_matching(self):
        store = LocalLyricsStore.__new__(LocalLyricsStore)
        self.assertEqual(store._netease_candidate_text(CATALOG["result"]["songs"][1]), ("Song", "Artist", "Album"))
