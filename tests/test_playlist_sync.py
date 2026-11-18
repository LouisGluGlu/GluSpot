"""Playlist sync between NetEase and Apple Music (api.apple_music_sync, 6.52 rules).

Rules confirmed by the user on 2026-11-17:
- Both one-way directions are open; the chosen source playlist is the authority.
- Source additions are added; source removals are removed (manual mode asks first).
- Target-only additions and removals wait for the user's choice.
- Apple Music cannot remove songs: those are listed, never attempted.
- Several or uncertain candidates are listed to listen to; pick one or skip.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from api.apple_music_catalog import SongIdentity, rank_candidates
from api.apple_music_sync import (
    FileLinkStore, MemoryLinkStore, PlaylistLink, SyncDecisions, apply_sync, find_for_playlist, plan_sync, song_key,
)
from api.online_music import CatalogPage, OnlineMusicError, OnlinePlaylist, OnlineSong
from local.apple_music_sync_flow import (
    auto_summary, build_sync_preview, collect_songs, execute_sync, online_song, preview_summary, result_lines,
    run_auto_sync, save_mode, song_identity,
)


import local.apple_music_sync_flow as _flow

_flow.VERIFY_WAITS = (0.0, 0.0)  # the read-back runs at once in tests


def ne(song_id, title, artist="周杰伦", duration=200):
    return OnlineSong("netease", song_id, title, artist, "", duration)


def am(catalog_id, title, artist="周杰伦", duration=200):
    return OnlineSong("apple_music", catalog_id, title, artist, "", duration, catalog_id=catalog_id)


class FakeService:
    """Two catalogs and owned playlists. Apple playlist rows use library ids like the real page."""

    def __init__(self):
        self.writes = []
        self.catalog = {
            "netease": [ne("n1", "晴天"), ne("n2", "七里香"), ne("n3", "稻香"), ne("n4", "夜曲")],
            "apple_music": [am("c1", "晴天"), am("c2", "七里香"), am("c3", "稻香"), am("c4", "夜曲")],
        }
        self.tracks = {("netease", "npl"): [], ("apple_music", "apl"): []}
        self._library = 0
        self.fail_ids = set()

    # reads
    def playlist_tracks(self, source_id, playlist_id, limit=100, offset=0):
        rows = self.tracks.get((source_id, playlist_id), [])
        window = tuple(rows[offset:offset + limit])
        return CatalogPage(window, offset + len(window), offset + len(window) < len(rows))

    def search(self, source_id, query, kind="songs", limit=8, offset=0):
        del kind, offset
        found = [song for song in self.catalog[source_id] if song.title in query]
        return CatalogPage(tuple(found[:limit]), limit, False)

    def playlists(self, source_id, limit=100, offset=0):
        playlist_id = "npl" if source_id == "netease" else "apl"
        return CatalogPage((OnlinePlaylist(source_id, playlist_id, "歌单"),), 1, False)

    def writable_playlists(self, source_id, limit=100, offset=0):
        return CatalogPage((OnlinePlaylist(source_id, "npl", "我的"),), 1, False)

    # writes
    def create_playlist(self, source_id, title, expected_user_id=""):
        self.writes.append(("create", source_id, title))
        self.tracks[(source_id, "created")] = []
        return "created"

    def append_playlist_tracks(self, source_id, playlist_id, song_ids, expected_user_id=""):
        ids = tuple(song_ids)
        if self.fail_ids & set(ids):
            raise OnlineMusicError("response", source_id, "失败")
        self.writes.append(("add", source_id, playlist_id, ids))
        for song_id in ids:
            original = next(song for song in self.catalog[source_id] if song.song_id == song_id)
            if source_id == "apple_music":
                self._library += 1
                original = OnlineSong("apple_music", f"i.{self._library}", original.title, original.artist,
                                      "", original.duration_seconds, catalog_id=song_id)
            self.tracks.setdefault((source_id, playlist_id), []).append(original)
        return len(ids)

    def remove_playlist_tracks(self, source_id, playlist_id, song_ids, expected_user_id=""):
        if source_id != "netease":
            raise OnlineMusicError("unsupported", source_id, "不能删")
        self.writes.append(("remove", source_id, playlist_id, tuple(song_ids)))
        rows = self.tracks[(source_id, playlist_id)]
        self.tracks[(source_id, playlist_id)] = [song for song in rows if song.song_id not in set(song_ids)]
        return len(song_ids)

    def ids(self, source_id, playlist_id):
        return [song.catalog_id or song.song_id for song in self.tracks[(source_id, playlist_id)]]


def sync(service, store, direction, source, target, decisions=None, **kwargs):
    preview = build_sync_preview(service, direction, source, target, store)
    return preview, execute_sync(service, preview, decisions or SyncDecisions(), store, **kwargs)


class NeteaseToAppleTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeService()
        self.store = MemoryLinkStore()

    def test_preview_writes_nothing_and_first_sync_creates_the_playlist(self):
        self.service.tracks[("netease", "npl")] = [ne("n1", "晴天"), ne("n2", "七里香")]
        preview = build_sync_preview(self.service, "netease_to_apple", "npl", "", self.store)
        self.assertEqual(self.service.writes, [])
        self.assertEqual([song_key(item.target) for item in preview.plan.additions], ["c1", "c2"])
        result = execute_sync(self.service, preview, SyncDecisions(), self.store, new_name="搬来的")
        self.assertEqual(self.service.writes[0], ("create", "apple_music", "搬来的"))
        self.assertEqual(self.service.writes[1], ("add", "apple_music", "created", ("c1", "c2")))
        self.assertEqual(result.added, 2)
        self.assertEqual(self.store.find("netease", "npl", "apple_music", "created").pairs, (("n1", "c1"), ("n2", "c2")))

    def test_second_sync_matches_library_rows_by_catalog_id(self):
        """Apple returns library ids (i.N) on read; they must not look like new or missing songs."""
        self.service.tracks[("netease", "npl")] = [ne("n1", "晴天"), ne("n2", "七里香")]
        sync(self.service, self.store, "netease_to_apple", "npl", "apl")
        again = build_sync_preview(self.service, "netease_to_apple", "npl", "apl", self.store)
        plan = again.plan
        self.assertEqual((plan.additions, plan.removals, plan.cannot_remove, plan.target_added, plan.target_removed),
                         ((), (), (), (), ()))
        self.assertEqual(plan.in_sync, 2)

    def test_existing_target_song_is_paired_not_added_twice(self):
        self.service.tracks[("netease", "npl")] = [ne("n1", "晴天")]
        self.service.append_playlist_tracks("apple_music", "apl", ("c1",))
        self.service.writes.clear()
        preview, result = sync(self.service, self.store, "netease_to_apple", "npl", "apl")
        self.assertEqual(len(preview.plan.pair_only), 1)
        self.assertEqual(self.service.writes, [])
        self.assertEqual(result.link.pairs, (("n1", "c1"),))
        self.assertEqual(self.service.ids("apple_music", "apl"), ["c1"])

    def test_two_source_versions_of_one_song_write_it_once(self):
        self.service.tracks[("netease", "npl")] = [ne("n1", "晴天"), ne("n9", "晴天")]
        sync(self.service, self.store, "netease_to_apple", "npl", "apl")
        self.assertEqual(self.service.ids("apple_music", "apl"), ["c1"])

    def test_source_removal_cannot_be_written_to_apple_and_is_listed(self):
        self.service.tracks[("netease", "npl")] = [ne("n1", "晴天"), ne("n2", "七里香")]
        sync(self.service, self.store, "netease_to_apple", "npl", "apl")
        self.service.tracks[("netease", "npl")].pop(0)
        preview, result = sync(self.service, self.store, "netease_to_apple", "npl", "apl",
                               SyncDecisions(confirm_removals=True))
        self.assertEqual(preview.plan.removals, ())
        self.assertEqual([item.target.title for item in preview.plan.cannot_remove], ["晴天"])
        self.assertFalse(any(write[0] == "remove" for write in self.service.writes))
        self.assertIn("Apple Music 里手动删", result_lines(result))
        self.assertIn("Apple Music 不让 GluSpot 删", preview_summary(preview))

    def test_rebuild_writes_a_new_apple_playlist_from_the_source_and_leaves_the_old_one(self):
        self.service.tracks[("netease", "npl")] = [ne("n1", "晴天"), ne("n2", "七里香"), ne("n3", "稻香")]
        sync(self.service, self.store, "netease_to_apple", "npl", "apl")
        self.service.tracks[("netease", "npl")] = [ne("n2", "七里香"), ne("n3", "稻香"), ne("n4", "夜曲")]
        self.service.writes.clear()
        preview = build_sync_preview(self.service, "netease_to_apple", "npl", "apl", self.store)
        self.assertEqual([item.target.catalog_id for item in preview.plan.cannot_remove], ["c1"])
        result = execute_sync(self.service, preview, SyncDecisions(rebuild=True), self.store, new_name="云（重建）")
        self.assertEqual(self.service.writes[0], ("create", "apple_music", "云（重建）"))
        self.assertEqual(self.service.ids("apple_music", "created"), ["c2", "c3", "c4"])
        self.assertEqual(self.service.ids("apple_music", "apl"), ["c1", "c2", "c3"], "the old playlist is not touched")
        self.assertEqual(result.added, 3)
        link = self.store.find("netease", "npl", "apple_music", "created")
        self.assertEqual(sorted(link.pairs), [("n2", "c2"), ("n3", "c3"), ("n4", "c4")])
        from api.apple_music_sync import find_for_playlist
        self.assertEqual(find_for_playlist(self.store, "netease", "npl").right_playlist_id, "created")

    def test_a_write_that_claims_success_but_never_lands_is_reported_and_not_paired(self):
        """What the broken Apple page did on 2026-11-18: "ok", but nothing in the playlist."""
        self.service.tracks[("netease", "npl")] = [ne("n1", "晴天"), ne("n2", "七里香")]
        self.service.append_playlist_tracks = lambda source, playlist, ids, expected_user_id="": len(tuple(ids))
        with self.assertLogs("GluSpot.apple", level="INFO") as logs:
            _preview, result = sync(self.service, self.store, "netease_to_apple", "npl", "apl")
        self.assertEqual((result.added, result.failed), (0, 2))
        self.assertEqual(self.store.find("netease", "npl", "apple_music", "apl").pairs, ())
        self.assertIn("回读歌单时不在里面", result_lines(result))
        self.assertIn("stage=verify result=missing", " ".join(logs.output))
        again = build_sync_preview(self.service, "netease_to_apple", "npl", "apl", self.store)
        self.assertEqual(len(again.plan.additions), 2, "offered again next time")

    def test_failed_add_is_not_retried_or_paired_and_returns_next_time(self):
        self.service.tracks[("netease", "npl")] = [ne("n1", "晴天")]
        self.service.fail_ids = {"c1"}
        _preview, result = sync(self.service, self.store, "netease_to_apple", "npl", "apl")
        self.assertEqual((result.added, result.failed), (0, 1))
        self.assertEqual(result.link.pairs, ())
        self.service.fail_ids = set()
        again = build_sync_preview(self.service, "netease_to_apple", "npl", "apl", self.store)
        self.assertEqual(len(again.plan.additions), 1)


class AppleToNeteaseTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeService()
        self.store = MemoryLinkStore()
        self.service.tracks[("apple_music", "apl")] = []
        self.service.append_playlist_tracks("apple_music", "apl", ("c1", "c2", "c3"))
        self.service.writes.clear()
        sync(self.service, self.store, "apple_to_netease", "apl", "npl")
        self.service.writes.clear()

    def drop_from_apple(self, catalog_id):
        rows = self.service.tracks[("apple_music", "apl")]
        self.service.tracks[("apple_music", "apl")] = [song for song in rows if song.catalog_id != catalog_id]

    def test_reverse_direction_adds_to_netease_and_pairs_in_netease_apple_order(self):
        self.assertEqual(self.service.ids("netease", "npl"), ["n1", "n2", "n3"])
        link = self.store.find("netease", "npl", "apple_music", "apl")
        self.assertEqual(link.pairs, (("n1", "c1"), ("n2", "c2"), ("n3", "c3")))
        self.assertEqual(link.direction, "apple_to_netease")

    def test_source_removal_waits_for_confirmation_in_manual_mode(self):
        self.drop_from_apple("c2")
        preview, result = sync(self.service, self.store, "apple_to_netease", "apl", "npl")
        self.assertEqual([item.target.song_id for item in preview.plan.removals], ["n2"])
        self.assertEqual(self.service.writes, [])
        self.assertEqual(result.pending, 1)
        with self.assertLogs("GluSpot.apple", level="INFO") as logs:
            _preview, result = sync(self.service, self.store, "apple_to_netease", "apl", "npl",
                                    SyncDecisions(confirm_removals=True))
        blob = " ".join(logs.output)
        self.assertIn("removed=1", blob)
        self.assertNotIn("apl", blob)
        self.assertEqual(result.removed, 1)
        self.assertEqual(self.service.ids("netease", "npl"), ["n1", "n3"])
        self.assertNotIn(("n2", "c2"), self.store.find("netease", "npl", "apple_music", "apl").pairs)

    def test_target_only_song_keep_is_remembered_and_remove_removes(self):
        self.service.tracks[("netease", "npl")].append(ne("n4", "夜曲"))
        self.service.tracks[("netease", "npl")].append(ne("n5", "别的", "别人"))
        preview = build_sync_preview(self.service, "apple_to_netease", "apl", "npl", self.store)
        self.assertEqual(sorted(item.target.song_id for item in preview.plan.target_added), ["n4", "n5"])
        execute_sync(self.service, preview, SyncDecisions(target_added={"n4": "keep", "n5": "remove"}), self.store)
        self.assertEqual(self.service.writes, [("remove", "netease", "npl", ("n5",))])
        again = build_sync_preview(self.service, "apple_to_netease", "apl", "npl", self.store)
        self.assertEqual(again.plan.target_added, ())

    def test_target_removed_song_readd_or_accept(self):
        self.service.tracks[("netease", "npl")] = [ne("n1", "晴天")]
        preview = build_sync_preview(self.service, "apple_to_netease", "apl", "npl", self.store)
        self.assertEqual(sorted(song_key(item.source) for item in preview.plan.target_removed), ["c2", "c3"])
        execute_sync(self.service, preview, SyncDecisions(target_removed={"c2": "readd", "c3": "accept"}), self.store)
        self.assertEqual(self.service.ids("netease", "npl"), ["n1", "n2"])
        again = build_sync_preview(self.service, "apple_to_netease", "apl", "npl", self.store)
        self.assertEqual((again.plan.target_removed, again.plan.additions), ((), ()))
        self.assertEqual(again.plan.skipped, 1)  # the accepted removal stays out, reported as skipped
        self.assertIn("apple_music:c3", self.store.find("netease", "npl", "apple_music", "apl").ignored)


class ChoiceTests(unittest.TestCase):
    """Rules of 2026-11-18: title and artist agreeing is taken at once; otherwise only the most similar song is asked."""

    def setUp(self):
        self.service = FakeService()
        self.store = MemoryLinkStore()
        # The artist is written another way on Apple Music, so only a fuzzy match is possible.
        self.service.catalog["apple_music"] = [
            am("x1", "晴天", "Jay Chou", 200), am("x2", "晴天", "Jay Chou", 260), am("x3", "晴天 (Live)", "Jay Chou", 330),
        ]
        self.service.tracks[("netease", "npl")] = [ne("n1", "晴天")]

    def test_a_fuzzy_match_offers_only_the_most_similar_song_and_writes_the_pick(self):
        preview = build_sync_preview(self.service, "netease_to_apple", "npl", "apl", self.store)
        self.assertEqual(preview.plan.additions, ())
        choice = preview.plan.choices[0]
        self.assertEqual([song.song_id for song in choice.candidates], ["x1"])
        self.assertIn("歌手名不一样", choice.reason)
        execute_sync(self.service, preview, SyncDecisions(picks={"n1": choice.candidates[0]}), self.store)
        self.assertEqual(self.service.writes, [("add", "apple_music", "apl", ("x1",))])

    def test_title_and_artist_agreeing_is_taken_without_asking(self):
        self.service.catalog["apple_music"] = [
            am("x1", "晴天", "周杰伦", 200), am("x2", "晴天", "周杰伦", 201), am("x3", "晴天 (Live)", "周杰伦", 330),
        ]
        preview = build_sync_preview(self.service, "netease_to_apple", "npl", "apl", self.store)
        self.assertEqual(preview.plan.choices, ())
        self.assertEqual([song_key(item.target) for item in preview.plan.additions], ["x1"])

    def test_an_artist_written_differently_on_each_platform_still_agrees(self):
        from api.apple_music_catalog import classify_song
        source = SongIdentity("netease", "n1", "X", "刘柏辛Lexie", "TEENAGE RAMBLE", 199)
        row = classify_song(source, (SongIdentity("apple_music", "a1", "X", "Lexie Liu", "TEENAGE RAMBLE", 199),))
        self.assertEqual(row.state, "matched")
        live = classify_song(SongIdentity("netease", "n2", "晴天", "周杰伦", "", 269),
                             (SongIdentity("apple_music", "a2", "晴天 (Live)", "周杰伦", "", 280),))
        self.assertEqual(live.state, "uncertain", "a Live version is never taken as the studio song")

    def test_a_suggestion_must_plausibly_be_the_same_song(self):
        from api.apple_music_catalog import classify_song
        # From the user's test playlist (2026-11-18): these used to be suggested, wrongly.
        cases = (
            (("Round Town", "Adrian Daniel / Asiah", 168), ("Down", "Adrian Daniel", 200)),
            (("The List", "Moonchild", 306), ("MY POWER", "Tierra Whack, Moonchild Sanelly", 260)),
            (("like this.", "Thomas Ng", 140), ("Love Like This", "Faith Evans", 276)),
            (("Just Friends", "Hayden James / Boy Matthews", 239),
             ("Just Friends (Originally Performed By Hayden James & Boy Matthews) [Karaoke]", "Singer's Edge Karaoke", 239)),
        )
        for (title, artist, seconds), (other, other_artist, other_seconds) in cases:
            row = classify_song(SongIdentity("netease", "n", title, artist, "", seconds),
                                (SongIdentity("apple_music", "a", other, other_artist, "", other_seconds),))
            self.assertEqual(row.state, "unmatched", title)
        cover = classify_song(SongIdentity("netease", "n", "Careless Whisper", "Conor Maynard", "", 150),
                              (SongIdentity("apple_music", "a", "Careless Whisper", "George Michael", "", 392),))
        self.assertEqual(cover.state, "uncertain", "the same title by someone else is worth asking")

    def test_a_title_only_search_runs_when_title_and_artist_find_nothing(self):
        self.service.catalog["apple_music"] = [am("x1", "晴天", "Jay Chou", 200)]
        searched = []
        original = self.service.search

        def search(source_id, query, **kwargs):
            searched.append(query)
            return original(source_id, query, **kwargs) if query == "晴天" else CatalogPage((), 0, False)

        self.service.search = search
        preview = build_sync_preview(self.service, "netease_to_apple", "npl", "apl", self.store)
        self.assertEqual(searched, ["晴天 周杰伦", "晴天"])
        self.assertEqual(len(preview.plan.choices), 1)

    def test_progress_reports_every_source_song(self):
        self.service.tracks[("netease", "npl")] = [ne("n1", "晴天"), ne("n2", "七里香")]
        seen = []
        build_sync_preview(self.service, "netease_to_apple", "npl", "apl", self.store,
                           progress=lambda done, total, song: seen.append((done, total, song.song_id if song else None)))
        self.assertEqual(seen, [(0, 2, None), (1, 2, "n1"), (2, 2, "n2")])

    def test_skip_is_remembered_and_unanswered_choice_asks_again(self):
        preview, result = sync(self.service, self.store, "netease_to_apple", "npl", "apl")
        self.assertEqual(result.pending, 1)
        again = build_sync_preview(self.service, "netease_to_apple", "npl", "apl", self.store)
        self.assertEqual(len(again.plan.choices), 1)
        execute_sync(self.service, again, SyncDecisions(skip=frozenset({"n1"})), self.store)
        third = build_sync_preview(self.service, "netease_to_apple", "npl", "apl", self.store)
        self.assertEqual((third.plan.choices, third.plan.skipped), ((), 1))

    def test_same_title_artist_and_album_is_taken_at_once(self):
        from api.apple_music_catalog import classify_song
        source = SongIdentity("netease", "n1", "Keep Me Warm", "Chace", "Keep Me Warm", 218)
        row = classify_song(source, (
            SongIdentity("apple_music", "a2", "Keep Me Warm", "Chace", "Anti-People Pleaser", 218),
            SongIdentity("apple_music", "a1", "Keep Me Warm", "Chace", "Keep Me Warm - Single", 218),
            SongIdentity("apple_music", "a3", "Keep Me Warm", "Chace", "Anti-People Pleaser, Pt. 1 - EP", 218),
        ))
        self.assertEqual((row.state, row.target.song_id), ("matched", "a1"))
        exact = classify_song(SongIdentity("netease", "n2", "Tears", "Kyle Dion", "SOULAR", 192), (
            SongIdentity("apple_music", "b1", "Tears", "Kyle Dion", "SOULAR (Deluxe)", 192),
            SongIdentity("apple_music", "b2", "Tears", "Kyle Dion", "SOULAR", 192),
        ))
        self.assertEqual(exact.target.song_id, "b2", "the identical album name wins over an edition")
        # Without an album, title and artist agreeing is still enough (2026-11-18).
        tie = classify_song(SongIdentity("netease", "n3", "Keep Me Warm", "Chace", "", 218), (
            SongIdentity("apple_music", "a1", "Keep Me Warm", "Chace", "Keep Me Warm - Single", 218),
            SongIdentity("apple_music", "a2", "Keep Me Warm", "Chace", "Anti-People Pleaser", 218),
        ))
        self.assertEqual(tie.state, "matched")

    def test_rank_candidates_drops_unrelated_titles_and_orders_best_first(self):
        source = SongIdentity("netease", "n1", "晴天", "周杰伦", "", 200)
        ranked = rank_candidates(source, (
            SongIdentity("apple_music", "z", "完全不同", "周杰伦", "", 200),
            SongIdentity("apple_music", "far", "晴天", "别人", "", 400),
            SongIdentity("apple_music", "best", "晴天", "周杰伦", "", 200),
        ), limit=5)
        self.assertEqual([song.song_id for song in ranked][:2], ["best", "far"])

    def test_unmatched_song_can_be_skipped(self):
        self.service.tracks[("netease", "npl")] = [ne("n7", "没人唱过")]
        preview, _result = sync(self.service, self.store, "netease_to_apple", "npl", "apl",
                                SyncDecisions(skip=frozenset({"n7"})))
        self.assertEqual(len(preview.plan.unmatched), 1)
        again = build_sync_preview(self.service, "netease_to_apple", "npl", "apl", self.store)
        self.assertEqual(again.plan.unmatched, ())


class AutoAndStoreTests(unittest.TestCase):
    def test_auto_mode_adds_and_removes_but_leaves_target_changes_for_the_user(self):
        service, store = FakeService(), MemoryLinkStore()
        service.append_playlist_tracks("apple_music", "apl", ("c1", "c2"))
        sync(service, store, "apple_to_netease", "apl", "npl", mode="auto")
        service.writes.clear()
        rows = service.tracks[("apple_music", "apl")]
        service.tracks[("apple_music", "apl")] = [song for song in rows if song.catalog_id != "c1"]
        service.append_playlist_tracks("apple_music", "apl", ("c3",))
        service.tracks[("netease", "npl")].append(ne("n4", "夜曲"))
        service.writes.clear()
        done = run_auto_sync(service, store)
        self.assertEqual(len(done), 1)
        result = done[0][1]
        self.assertEqual((result.added, result.removed, result.pending), (1, 1, 1))
        self.assertEqual(sorted(service.ids("netease", "npl")), ["n2", "n3", "n4"])
        self.assertIn("加了 1 首，删了 1 首", auto_summary(done))
        self.assertIn("1 首等你", auto_summary(done))

    def test_manual_pairs_are_left_alone_by_auto_sync(self):
        service, store = FakeService(), MemoryLinkStore()
        service.tracks[("netease", "npl")] = [ne("n1", "晴天")]
        sync(service, store, "netease_to_apple", "npl", "apl")
        service.tracks[("netease", "npl")].append(ne("n2", "七里香"))
        service.writes.clear()
        self.assertEqual(run_auto_sync(service, store), ())
        self.assertEqual(service.writes, [])
        self.assertEqual(auto_summary(()), "")

    def test_save_mode_keeps_pairs_and_find_for_playlist(self):
        service, store = FakeService(), MemoryLinkStore()
        service.tracks[("netease", "npl")] = [ne("n1", "晴天")]
        sync(service, store, "netease_to_apple", "npl", "apl")
        save_mode(store, "netease_to_apple", "npl", "apl", "auto")
        link = find_for_playlist(store, "netease", "npl")
        self.assertEqual((link.mode, link.pairs), ("auto", (("n1", "c1"),)))
        self.assertIsNone(save_mode(store, "netease_to_apple", "npl", "", "auto"))

    def test_file_store_reads_version_one_and_writes_ids_only(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "apple-music-sync.json"
        path.write_text(json.dumps({"version": 1, "links": [{
            "left_source": "netease", "left_playlist_id": "npl", "right_source": "apple_music",
            "right_playlist_id": "apl", "pairs": [{"left": "n1", "right": "c1"}],
        }]}), encoding="utf-8")
        store = FileLinkStore(path)
        old = store.find("netease", "npl", "apple_music", "apl")
        self.assertEqual((old.direction, old.mode, old.ignored), ("netease_to_apple", "manual", ()))
        store.save(PlaylistLink("netease", "npl", "apple_music", "apl", (("n1", "c1"),), "apple_to_netease", "auto",
                                ("netease:n9",)))
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("token", text.lower())
        self.assertNotIn("cookie", text.lower())
        again = FileLinkStore(path).find("netease", "npl", "apple_music", "apl")
        self.assertEqual((again.direction, again.mode, again.ignored), ("apple_to_netease", "auto", ("netease:n9",)))

    def test_long_playlist_is_reported_as_truncated(self):
        service = FakeService()
        service.tracks[("netease", "npl")] = [ne(f"n{index}", f"歌{index}") for index in range(250)]
        songs, cut = collect_songs(service, "netease", "npl", page_limit=2)
        self.assertEqual((len(songs), cut), (200, True))
        songs, cut = collect_songs(service, "netease", "npl")
        self.assertEqual((len(songs), cut), (250, False))

    def test_apple_identity_keeps_catalog_id_and_audition_song_is_playable(self):
        identity = song_identity(OnlineSong("apple_music", "i.9", "晴天", "周杰伦", "", 200, catalog_id="c1"))
        self.assertEqual(song_key(identity), "c1")
        played = online_song(identity)
        self.assertEqual((played.song_id, played.catalog_id, played.media_kind), ("i.9", "c1", "song"))

    def test_apply_refuses_apple_removal_even_if_asked(self):
        plan = plan_sync(None, "netease_to_apple", [], [song_identity(am("c1", "晴天"))], ())
        self.assertEqual(len(plan.target_added), 1)
        calls = []
        result = apply_sync(plan, SyncDecisions(target_added={"c1": "remove"}),
                            PlaylistLink("netease", "npl", "apple_music", "apl"),
                            add_writer=calls.append, remove_writer=calls.append)
        self.assertEqual((calls, result.removed), ([], 0))


def _THEME_KEY(key):
    from local.apple_music_sync_dialog import _t
    return _t(key)


class DialogAndControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def preview(self):
        service, store = FakeService(), MemoryLinkStore()
        service.catalog["apple_music"] = [am("x1", "晴天", "Jay Chou", 200)]
        service.tracks[("netease", "npl")] = [ne("n1", "晴天")]
        service.tracks[("apple_music", "apl")] = []
        return build_sync_preview(service, "netease_to_apple", "npl", "apl", store)

    def test_dialog_opens_both_directions_and_gates_sync_on_a_preview(self):
        from local.apple_music_sync_dialog import PlaylistSyncDialog
        dialog = PlaylistSyncDialog()
        self.addCleanup(dialog.close)
        dialog.set_source_playlists([("npl", "午后")])
        dialog.set_target_playlists([("apl", "已有")])
        self.assertFalse(dialog.confirm_button.isEnabled())
        dialog.set_direction("apple_to_netease")
        self.assertEqual(dialog.direction(), "apple_to_netease")
        self.assertIn("Apple Music", dialog.source_label.text())
        dialog.set_direction("netease_to_apple")
        dialog.set_source_playlists([("npl", "午后")])
        dialog.set_target_playlists([("apl", "已有")], select="apl")
        self.assertEqual(dialog.request()["target_playlist_id"], "apl")
        self.assertTrue(dialog.name_row.isHidden())
        preview = self.preview()
        dialog.set_preview(preview, preview_summary(preview))
        self.assertTrue(dialog.confirm_button.isEnabled())
        self.assertEqual(dialog.decisions().picks, {})
        group = dialog._choices["n1"]
        heard = []
        dialog.auditionRequested.connect(lambda song, start, end: heard.append(song))
        self.assertEqual(len(group.cards), 1, "only the most similar song is asked about")
        group.cards[0].line.listen_button.click()
        self.assertEqual(heard[0].song_id, preview.plan.choices[0].candidates[0].song_id)
        # The whole row is the click target; a second click clears the choice.
        group.cards[0].click()
        self.assertEqual(dialog.decisions().picks["n1"].song_id, heard[0].song_id)
        group.cards[0].click()
        self.assertEqual(dialog.decisions().picks, {})
        group.pills[0].click()
        self.assertEqual(dialog.decisions().skip, frozenset({"n1"}))
        dialog.set_mode("auto")
        self.assertTrue(dialog.decisions().confirm_removals)

    def test_controller_declined_confirmation_writes_no_removal(self):
        from local.apple_music_sync_dialog import PlaylistSyncDialog
        from local.playback_entry_controller import PlaybackEntryController
        service, store = FakeService(), MemoryLinkStore()
        service.append_playlist_tracks("apple_music", "apl", ("c1", "c2"))
        sync(service, store, "apple_to_netease", "apl", "npl")
        service.tracks[("apple_music", "apl")].pop(0)
        service.writes.clear()
        dialog = PlaylistSyncDialog()
        self.addCleanup(dialog.close)
        dialog.set_direction("apple_to_netease")
        dialog.set_source_playlists([("apl", "果")])
        dialog.set_target_playlists([("npl", "云")], select="npl")
        preview = build_sync_preview(service, "apple_to_netease", "apl", "npl", store)
        dialog.set_preview(preview)
        asked = []
        dialog.confirm_removals = lambda songs: asked.append(tuple(songs)) or False
        controller = PlaybackEntryController.__new__(PlaybackEntryController)
        controller._closed = False
        controller._generation = 1
        controller._sync_dialog = dialog
        controller._sync_links = store
        controller.service = service
        jobs = []
        controller.tasks = type("Tasks", (), {"submit": staticmethod(lambda token, action: jobs.append(action) or True)})()
        controller._request_sync_write()
        self.assertEqual([song.song_id for song in asked[0]], ["n1"])
        self.assertIn("删了 0 首", jobs[0]())
        self.assertEqual(service.writes, [])

    def test_controller_lists_owned_netease_playlists_as_targets(self):
        from local.apple_music_sync_dialog import PlaylistSyncDialog
        from local.playback_entry_controller import PlaybackEntryController
        controller = PlaybackEntryController.__new__(PlaybackEntryController)
        controller.service = FakeService()
        controller._sync_links = MemoryLinkStore()
        lists = controller._load_sync_lists()
        self.assertEqual(lists["netease_writable"], (("npl", "我的"),))
        dialog = PlaylistSyncDialog()
        self.addCleanup(dialog.close)
        dialog.set_direction("apple_to_netease")
        controller._sync_dialog = dialog
        controller._sync_completed("sync_lists", lists, None)
        self.assertEqual(dialog.target_list.itemData(1), "npl")
        self.assertEqual(dialog.target_list.itemText(1), "我的")

    def test_library_entries_open_sync_with_the_account_platform(self):
        from local.music_library_controller import MusicLibraryController
        from local.music_library_window import LibraryCollection
        opened = []
        controller = MusicLibraryController.__new__(MusicLibraryController)
        controller.entry = type("Entry", (), {"open_playlist_sync": staticmethod(lambda *args: opened.append(args))})()
        controller.window = type("Window", (), {"_account": type("Account", (), {"source_id": "apple_music"})()})()
        controller.open_playlist_sync(None)
        controller.open_playlist_sync(LibraryCollection("playlist:apple_music:p.1", "playlist", "通勤", "", "p.1"))
        self.assertEqual(opened, [("apple_music", ""), ("apple_music", "p.1")])

    def test_narrow_window_keeps_every_listen_button_inside_and_has_no_title_bar(self):
        from PySide6.QtCore import QPoint, Qt
        from local.apple_music_sync_dialog import PlaylistSyncDialog
        dialog = PlaylistSyncDialog()
        self.addCleanup(dialog.close)
        self.assertTrue(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)
        long = SongIdentity("apple_music", "x9", "一首名字特别特别特别特别特别特别特别特别长的歌" * 2, "很长的歌手名" * 4,
                            "很长的专辑名" * 6, 200, catalog_id="x9")
        from api.apple_music_sync import PlaylistLink, SyncItem, SyncPlan
        from local.apple_music_sync_flow import SyncPreview
        source = SongIdentity("netease", "n9", long.title, long.artist, "别的专辑", 200)
        plan = SyncPlan("netease_to_apple", choices=(SyncItem("choose", source, None, (long, long)),))
        dialog.set_preview(SyncPreview("netease_to_apple", "npl", "apl", plan, PlaylistLink("netease", "npl", "apple_music", "apl")))
        dialog.resize(520, 700)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        dialog.show()
        for _ in range(4):
            self.app.processEvents()
        viewport = dialog.scroll.viewport()
        for card in dialog._choices["n9"].cards:
            button = card.line.listen_button
            right = button.mapTo(viewport, QPoint(button.width(), 0)).x()
            self.assertLessEqual(right, viewport.width(), "the listen button stays inside the window")
            self.assertTrue(card.line.title.text().endswith("…"))

    def test_window_follows_the_theme_and_uses_ios_rows_with_a_tick(self):
        from unittest.mock import patch
        from local.apple_music_sync_dialog import AppleSegmentedControl, PlaylistSyncDialog, _THEME
        with patch("local.ui_theme.surface_is_dark", return_value=False):
            light = PlaylistSyncDialog(settings_get=lambda key, default=None: default)
        self.addCleanup(light.close)
        self.assertEqual(_THEME["text"], "#1d1d1f")
        self.assertFalse(light._settings_dark_mode())
        with patch("local.ui_theme.surface_is_dark", return_value=True):
            dialog = PlaylistSyncDialog(settings_get=lambda key, default=None: default)
        self.addCleanup(dialog.close)
        self.assertEqual(_THEME["text"], "#f5f5f7")
        self.assertEqual(dialog.confirm_button.objectName(), "Primary")
        dialog.set_preview(self.preview())
        group = dialog._choices["n1"]
        group.cards[0].click()
        self.assertTrue(group.cards[0].isChecked())
        group.pills[0].click()
        self.assertFalse(group.cards[0].isChecked())
        self.assertEqual(dialog.decisions().skip, frozenset({"n1"}))
        switch = dialog.direction_switch
        self.assertIsInstance(switch, AppleSegmentedControl)
        heard = []
        dialog.directionChanged.connect(heard.append)
        switch.set_current("apple_to_netease", notify=True)
        self.assertEqual((heard, dialog.direction()), (["apple_to_netease"], "apple_to_netease"))

    def test_checking_shows_centred_dots_and_skeleton_then_clears(self):
        from PySide6.QtCore import Qt
        from local.apple_music_sync_dialog import PlaylistSyncDialog, SkeletonRows
        dialog = PlaylistSyncDialog()
        self.addCleanup(dialog.close)
        dialog.resize(700, 700)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        dialog.show()
        self.app.processEvents()
        dialog.set_busy(True, "正在比对")
        dialog.start_loading(skeleton=True)
        self.assertTrue(dialog.loading.isVisible())
        centre = dialog.loading.geometry().center()
        area = dialog.scroll.geometry().center()
        self.assertLessEqual(abs(centre.x() - area.x()), 2)
        self.assertLessEqual(abs(centre.y() - area.y()), 2)
        self.assertTrue(dialog.results.findChildren(SkeletonRows))
        dialog.set_busy(False)
        self.assertFalse(dialog.loading.isVisible())
        dialog.set_preview(self.preview())
        self.assertFalse([row for row in dialog.results.findChildren(SkeletonRows) if row.isVisible()])

    def test_checking_shows_covers_count_and_bar_per_song(self):
        from PySide6.QtCore import Qt
        from local.apple_music_sync_dialog import PlaylistSyncDialog
        dialog = PlaylistSyncDialog()
        self.addCleanup(dialog.close)
        dialog.resize(700, 700)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        dialog.show()
        dialog.set_busy(True, "正在比对")
        dialog.start_loading(skeleton=True)
        self.assertTrue(dialog.loading.isVisible())
        dialog.set_scan_progress(0, 3, None)
        self.assertTrue(dialog.scan.isVisible())
        self.assertFalse(dialog.loading.isVisible())
        song = SongIdentity("netease", "n1", "Keep Me Warm", "Chace", "Keep Me Warm", 218)
        dialog.set_scan_progress(1, 3, song)
        self.assertEqual((dialog.scan.done, dialog.scan.total, dialog.scan.current), (1, 3, song))
        self.assertIn("1 / 3", dialog.status.text())
        centre = dialog.scan.geometry().center()
        self.assertLessEqual(abs(centre.x() - dialog.scroll.geometry().center().x()), 2)
        dialog.set_busy(False)
        self.assertFalse(dialog.scan.isVisible())
        dialog.set_scan_progress(2, 3, song)
        self.assertFalse(dialog.scan.isVisible(), "late progress after the check is ignored")

    def test_a_song_to_confirm_is_a_side_by_side_comparison_with_two_clear_keys(self):
        from local.apple_music_sync_dialog import CompareChoice, PlaylistSyncDialog, StatusPill
        dialog = PlaylistSyncDialog()
        self.addCleanup(dialog.close)
        dialog.set_preview(self.preview())
        compare = dialog.results.findChildren(CompareChoice)[0]
        self.assertEqual(compare.source_tile.song.source_id, "netease")
        card = compare.group.cards[0]
        self.assertEqual(card.song.source_id, "apple_music")
        self.assertEqual(card.line.meta.styleSheet().count(_THEME_KEY("orange")), 1, "the differing artist is marked")
        compare.accept.click()
        self.assertTrue(card.isChecked())
        self.assertEqual(dialog.decisions().picks["n1"].song_id, card.song.song_id)
        compare.group.pills[0].click()
        self.assertFalse(compare.accept.isChecked())
        self.assertEqual(dialog.decisions().skip, frozenset({"n1"}))
        pills = {pill.text().split(" · ")[0]: pill.styleSheet() for pill in dialog.results.findChildren(StatusPill)}
        self.assertIn(_THEME_KEY("yellow"), pills["待确认"])

    def test_states_are_red_yellow_green_and_apple_removals_offer_a_rebuild(self):
        from local.apple_music_sync_dialog import PlaylistSyncDialog, StatusPill
        service, store = FakeService(), MemoryLinkStore()
        service.tracks[("netease", "npl")] = [ne("n1", "晴天"), ne("n2", "七里香")]
        sync(service, store, "netease_to_apple", "npl", "apl")
        service.tracks[("netease", "npl")] = [ne("n2", "七里香"), ne("n3", "稻香"), ne("n7", "没人唱过")]
        preview = build_sync_preview(service, "netease_to_apple", "npl", "apl", store)
        dialog = PlaylistSyncDialog()
        self.addCleanup(dialog.close)
        dialog.set_source_playlists([("npl", "云")])
        dialog.set_target_playlists([("apl", "果")], select="apl")
        dialog.set_preview(preview)
        pills = {pill.text().split(" · ")[0]: pill.styleSheet() for pill in dialog.results.findChildren(StatusPill)}
        self.assertIn(_THEME_KEY("red"), pills["没找到"])
        self.assertIn(_THEME_KEY("green"), pills["预添加到Apple Music"])
        self.assertIn(_THEME_KEY("red"), pills["Apple Music 删不掉的歌"])
        self.assertFalse(dialog.decisions().rebuild, "adding only is the default")
        dialog._rebuild.pills[1].click()
        self.assertTrue(dialog.decisions().rebuild)
        self.assertIn("重建", dialog.request()["new_name"])
        self.assertTrue(dialog.request()["new_name"].startswith("果"))

    def test_popups_use_the_settings_menu_style_and_share_one_edge(self):
        from local.apple_music_sync_dialog import PlaylistSyncDialog
        dialog = PlaylistSyncDialog()
        self.addCleanup(dialog.close)
        self.assertIn("QAbstractItemView", dialog.source_list.view().styleSheet() + dialog.styleSheet())
        self.assertEqual(dialog.source_list.width(), dialog.target_list.width())
        self.assertEqual(dialog.source_list.width(), dialog.name_edit.width())

    def test_platform_is_named_on_source_and_every_candidate(self):
        from local.apple_music_sync_dialog import PlatformBadge, PlaylistSyncDialog
        dialog = PlaylistSyncDialog()
        self.addCleanup(dialog.close)
        dialog.set_preview(self.preview())
        badges = [badge.text() for badge in dialog.results.findChildren(PlatformBadge)]
        self.assertIn("网易云", badges)
        self.assertGreaterEqual(badges.count("Apple Music"), 1)

    def test_audition_plays_in_the_window_and_pauses_the_main_queue(self):
        from local.apple_music_sync_dialog import PlaylistSyncDialog
        from local.playback_entry_controller import PlaybackEntryController
        dialog = PlaylistSyncDialog()
        self.addCleanup(dialog.close)
        preview = self.preview()
        dialog.set_preview(preview)
        controller = PlaybackEntryController.__new__(PlaybackEntryController)
        controller._closed = False
        controller._generation = 1
        controller._sync_dialog = dialog
        controller.service = FakeService()
        jobs = []
        controller.tasks = type("Tasks", (), {"submit": staticmethod(lambda token, action: jobs.append((token, action)) or True)})()
        paused = []
        controller.pause_main_callback = lambda: paused.append(True)
        dialog.auditionRequested.connect(controller._sync_audition)
        dialog.auditionStarted.connect(controller._pause_main_for_audition)
        played = []
        dialog.audition.play = lambda key, url, start=0.0, end=0.0: played.append((url, start, end))
        candidate = preview.plan.choices[0].candidates[0]
        candidate = SongIdentity(**{**candidate.__dict__, "preview_url": "https://audio.example/x1.m4a"})
        dialog._listen(candidate, 0.0, 0.0)
        self.assertEqual(jobs, [], "a known Apple preview plays without waiting for the task queue")
        self.assertEqual(played, [("https://audio.example/x1.m4a", 0.0, 0.0)])
        self.assertEqual(paused, [True])
        from api.online_music import StreamInfo
        controller.service.stream_url = lambda source, song_id: StreamInfo(source, song_id, "https://m.example/n1.mp3", True)
        source = preview.plan.choices[0].source
        dialog._listen(source, 0.0, 0.0)
        self.assertEqual(jobs[0][0][0], "sync_audition")
        controller._sync_completed("sync_audition", jobs[0][1](), None)
        self.assertEqual(played[-1], ("https://m.example/n1.mp3", 0.0, 0.0))
        # A late answer for a song the user no longer wants is dropped.
        dialog.play_audition(SongIdentity("apple_music", "zz", "别的", "", "", 1), "https://x/y", 0, 0)
        self.assertEqual(len(played), 2)

    def test_audition_follows_the_main_players_output_device_and_volume(self):
        from PySide6.QtMultimedia import QMediaDevices
        from local.apple_music_sync_dialog import PlaylistSyncDialog
        devices = QMediaDevices.audioOutputs()
        if not devices:
            self.skipTest("no audio output device on this machine")
        chosen = devices[-1]
        dialog = PlaylistSyncDialog()
        self.addCleanup(dialog.close)
        dialog.audition.output_provider = lambda: (chosen, 0.4)
        with self.assertLogs("GluSpot.sync", level="INFO") as logs:
            dialog.audition.play("k", "file:///Z:/missing-gluspot-audition.mp3")
            dialog.audition.stop()
        self.assertEqual(dialog.audition.output.device().id(), chosen.id())
        self.assertAlmostEqual(dialog.audition.output.volume(), 0.4, places=2)
        self.assertIn("stage=audition result=start device=main", " ".join(logs.output))

    def test_netease_audition_uses_the_stream_address(self):
        from local.playlist_audio_compare import AudioSource
        from api.online_music import StreamInfo
        service = FakeService()
        service.stream_url = lambda source, song_id: StreamInfo(source, song_id, "https://m.example/n1.mp3", True)
        self.assertEqual(AudioSource(service).url(song_identity(ne("n1", "晴天"))), "https://m.example/n1.mp3")
        service.stream_url = lambda source, song_id: StreamInfo(source, song_id, "", False)
        self.assertEqual(AudioSource(service).url(song_identity(ne("n1", "晴天"))), "")


if __name__ == "__main__":
    unittest.main()
