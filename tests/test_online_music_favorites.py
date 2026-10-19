"""Isolated account favorite contracts; no real credentials or account writes."""
from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock

from api.online_music import AccountSnapshot, OnlineMusicError, OnlineMusicService


class FavoriteServiceTests(unittest.TestCase):
    def setUp(self):
        self.backend = Mock()
        self.backend.available.return_value = (True, "")
        self.backend.request.return_value = {"data": {"liked": False}}
        self.store = Mock()
        self.service = OnlineMusicService(self.backend, self.store)
        self.service._accounts["netease"] = AccountSnapshot("netease", True, "42")
        self.service._credentials["netease"] = {"cookie": {"MUSIC_U": "synthetic"}}
        self.addCleanup(self.service.close)

    def error_code(self, callback, code):
        with self.assertRaises(OnlineMusicError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def test_capabilities_only_advertise_verified_writes(self):
        sources = {source.source_id: source for source in self.service.list_sources()}
        self.assertIn("favorite", sources["netease"].capabilities)
        self.assertIn("playlist_write", sources["netease"].capabilities)
        for source in ("qq", "kugou", "qishui"):
            self.assertNotIn("favorite", sources[source].capabilities)
            self.error_code(lambda: self.service.favorite_status(source, "qq-mid"), "unsupported")
        self.backend.request.assert_not_called()

    def test_favorite_false_is_real_service_state_and_unknown_is_error(self):
        self.assertIs(self.service.favorite_status("netease", "9", expected_user_id="42"), False)
        self.assertEqual(self.backend.request.call_args.args[:3],
                         ("netease", "favorite_status", {"song_id": "9", "user_id": "42"}))
        for data in ({}, {"liked": 0}, {"liked": "false"}, {"liked": None}):
            self.backend.request.return_value = {"data": data}
            self.error_code(lambda: self.service.favorite_status("netease", "9"), "response")

    def test_explicit_like_target_is_only_confirmed_after_matching_ack(self):
        for liked in (False, True):
            self.backend.request.return_value = {"data": {"ok": True, "liked": liked}}
            self.assertIs(self.service.set_favorite("netease", "9", liked), liked)
        for data in ({"liked": True}, {"ok": 1, "liked": True}, {"ok": True, "liked": False}):
            self.backend.request.return_value = {"data": data}
            self.error_code(lambda: self.service.set_favorite("netease", "9", True), "response")

    def test_invalid_identifiers_and_states_do_not_dispatch(self):
        for value in ("0", "01", "-1", "a,b", "dj-audio:9", "9\n", "1" * 21, 9, None):
            self.error_code(lambda: self.service.favorite_status("netease", value), "argument")
        for value in (1, "false", None):
            self.error_code(lambda: self.service.set_favorite("netease", "9", value), "argument")
        self.backend.request.assert_not_called()

    def test_old_dialog_account_is_rejected_before_backend_dispatch(self):
        self.error_code(lambda: self.service.set_favorite("netease", "9", True, expected_user_id="old"), "cancelled")
        self.error_code(lambda: self.service.add_song_to_playlist("netease", "9", "12", expected_user_id="old"), "cancelled")
        self.backend.request.assert_not_called()

    def test_writable_lists_keep_only_actual_creator_and_original_page_cursor(self):
        self.backend.request.return_value = {"data": {"code": 200, "more": True, "playlist": [
            {"id": 10, "name": "own", "creator": {"userId": 42}, "trackCount": 3},
            {"id": 11, "name": "foreign", "creator": {"userId": 99}},
            {"id": 12, "name": "subscribed", "creator": {"userId": 42}, "subscribed": True},
            {"id": 13, "name": "unknown"},
            {"id": 10, "name": "duplicate", "creator": {"userId": 42}},
        ]}}
        page = self.service.writable_playlists("netease", limit=5, offset=5, expected_user_id="42")
        self.assertEqual([(row.playlist_id, row.title) for row in page.items], [("10", "own")])
        self.assertEqual((page.next_offset, page.has_more), (10, True))
        self.backend.request.return_value = {"data": {"code": 200, "more": True, "playlist": [
            {"id": 11, "name": "foreign", "creator": {"userId": 99}},
        ]}}
        page = self.service.writable_playlists("netease", limit=1)
        self.assertFalse(page.items)
        self.assertTrue(page.has_more)
        self.assertEqual(page.next_offset, 1)

    def test_empty_or_malformed_owned_page_never_claims_a_destination(self):
        self.backend.request.return_value = {"data": {"code": 200, "more": True, "playlist": []}}
        self.assertFalse(self.service.writable_playlists("netease").has_more)
        for data in ({"code": 500, "playlist": []}, {"code": 200}, {"code": 200, "playlist": {}}):
            self.backend.request.return_value = {"data": data}
            self.error_code(lambda: self.service.writable_playlists("netease"), "response")

    def test_add_to_playlist_dispatches_one_explicit_write_and_requires_ack(self):
        self.backend.request.return_value = {"data": {"ok": True}}
        self.assertIsNone(self.service.add_song_to_playlist("netease", "9", "12", expected_user_id="42"))
        self.assertEqual(self.backend.request.call_args.args[:3], ("netease", "add_song_to_playlist",
                         {"song_id": "9", "playlist_id": "12", "user_id": "42"}))
        self.backend.request.side_effect = OnlineMusicError("timeout", "netease", "synthetic")
        self.error_code(lambda: self.service.add_song_to_playlist("netease", "9", "12"), "timeout")
        self.assertEqual(self.backend.request.call_count, 2, "uncertain writes are never retried")

    def test_logout_during_read_discards_late_response(self):
        entered, release = threading.Event(), threading.Event()
        def request(_source, operation, _params, _credential=None):
            if operation == "favorite_status":
                entered.set()
                release.wait(2)
                return {"data": {"liked": True}, "credential": {"cookie": {"MUSIC_U": "old"}}}
            return {"data": {}}
        self.backend.request.side_effect = request
        results = []
        def run():
            try:
                results.append(self.service.favorite_status("netease", "9"))
            except OnlineMusicError as error:
                results.append(error.code)
        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(entered.wait(1))
        self.service.logout("netease")
        release.set()
        worker.join(2)
        self.assertEqual(results, ["cancelled"])
        self.store.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
