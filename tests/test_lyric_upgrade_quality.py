"""Whole-source upgrades preserve identity, content and real timing coverage."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from api.lyric_metadata import is_explicit_lyric_credit
from api.lyric_timeline import has_sufficient_word_timing, lyric_timing_quality
from api.lyrics import LocalLyricsStore, LyricsMatch
from api.models import TimedLyricLine, TimedLyricUnit


IDENTITY = ("netease", "123", "Authored Song", "Music / Singer", "Album", 180.0)


def row(at, text, *, measured=False):
    pieces = text.split(" ")
    units = tuple(TimedLyricUnit(at + i * .1, at + (i + 1) * .1,
        value + (" " if i < len(pieces)-1 else "")) for i, value in enumerate(pieces)) if measured else ()
    return TimedLyricLine(at, text, units, "unit" if measured else "line")


def match(rows, source, *, verified=False):
    rows = tuple(rows)
    return LyricsMatch(rows, tuple(item.text for item in rows), source,
        verified_identity=IDENTITY if verified else None)


class LyricUpgradeQualityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.store = LocalLyricsStore(self.root / "manual", generated_root=self.root / "cache")
        # Authored text, deliberately split differently by two valid sources.
        self.original = [row(10 + i * 10, f"Verse{i} keep the light alive today") for i in range(10)]
        self.word_rows = [row(item.time_seconds + .2, item.text, measured=True) for item in self.original]
        self.word_rows[4:5] = [row(50.2, "Verse4 keep the", measured=True), row(51.2, "light alive today", measured=True)]

    def test_one_measured_row_does_not_stop_the_finite_online_source_loop(self):
        partial = match([self.word_rows[0], *self.original[1:]], "netease")
        complete = match([row(item.time_seconds, item.text, measured=True) for item in self.original], "qq")
        self.assertLess(lyric_timing_quality(partial.timed_lyrics).word_coverage, .95)
        self.store._resolve_netease = Mock(return_value=partial)
        self.store._resolve_qq = Mock(return_value=complete)
        self.store._resolve_lrclib = Mock(return_value=None)
        self.store._resolve_amll = Mock(return_value=None)
        early = []
        result = self.store._resolve_online("Authored Song", "Singer", "Album", 180, early.append)
        self.assertIs(result, complete)
        self.assertEqual(early, [partial])
        self.store._resolve_qq.assert_called_once()

    def test_later_complete_source_upgrades_partial_real_units_without_downgrading(self):
        partial = match([row(item.time_seconds, item.text, measured=(i == 0))
            for i, item in enumerate(self.original)], "netease")
        complete = match([row(item.time_seconds, item.text, measured=True) for item in self.original], "qq")
        self.assertTrue(self.store.is_compatible_lyric_upgrade(partial, complete))
        self.assertFalse(self.store.is_compatible_lyric_upgrade(complete, partial))
        self.assertFalse(self.store.is_compatible_lyric_upgrade(complete, complete))

    def test_first_cross_source_word_excerpt_cannot_discard_visible_verses(self):
        original = match(self.original, "netease")
        excerpt = match([self.word_rows[0]], "qq")
        complete = match([row(item.time_seconds + .2, item.text, measured=True)
            for item in self.original], "amll:netease:123")
        self.assertEqual(lyric_timing_quality(excerpt.timed_lyrics).word_coverage, 1.)
        for final, expected in ((None, original), (complete, complete)):
            with self.subTest(has_compatible_backup=final is not None):
                self.store._resolve_netease = Mock(return_value=original)
                self.store._resolve_qq = Mock(return_value=excerpt)
                self.store._resolve_lrclib = Mock(return_value=None)
                self.store._resolve_amll = Mock(return_value=final)
                early = []
                self.assertIs(self.store._resolve_online("Song", "Singer", "Album", 180., early.append), expected)
                self.assertEqual(early, [original])
                self.store._resolve_amll.assert_called_once()

    def test_synthetic_original_keeps_real_lrc_priority_and_can_gain_complete_word_clock(self):
        original = match([replace(item, time_seconds=i * 3., timing_kind="synthetic")
            for i, item in enumerate(self.original)], "lrclib")
        measured = match([row(item.time_seconds, item.text, measured=True)
            for item in self.original], "qq")
        real_lines = match(self.original, "qq")
        self.assertTrue(self.store.is_compatible_lyric_upgrade(original, measured))
        self.assertFalse(self.store.is_compatible_lyric_upgrade(original,
            match(measured.timed_lyrics[:1], "qq")))
        for candidate in (real_lines, measured):
            with self.subTest(timing_kind=candidate.timed_lyrics[0].timing_kind):
                self.store._resolve_netease = Mock(return_value=original)
                self.store._resolve_qq = Mock(return_value=candidate)
                self.store._resolve_lrclib = Mock(return_value=None)
                self.store._resolve_amll = Mock(return_value=None)
                self.assertIs(self.store._resolve_online("Song", "Singer", "Album", 180.), candidate)

    def test_exact_verified_recording_allows_credits_and_changed_row_breaks(self):
        credits = [row(0, "作词 Lyricist: Author"), row(1, "编程: Programmer"),
            row(2, "音频工程师: Engineer"), row(3, "人声录音工程师: Engineer"), row(4, "人声录音: Studio")]
        old = match([*credits, *self.original], "netease", verified=True)
        new = match(self.word_rows, "amll:netease:123", verified=True)
        self.assertFalse(self.store._compatible_source_rows(old, new))
        self.assertTrue(self.store.is_compatible_lyric_upgrade(old, new))
        self.assertEqual(old.timed_lyrics[:len(credits)], tuple(credits))
        self.assertIs(self.store._retain_matching_subrows(new, old), new)
        self.assertFalse(self.store.is_compatible_lyric_upgrade(replace(old, verified_identity=None), new))
        self.assertFalse(self.store.is_compatible_lyric_upgrade(old, replace(new, verified_identity=None)))

    def test_complete_original_content_and_clock_are_checked_even_for_same_id(self):
        old = match(self.original, "netease", verified=True)
        new = match(self.word_rows, "amll:netease:123", verified=True)
        wrong_words = [*self.word_rows]
        wrong_words[2] = row(wrong_words[2].time_seconds, "A wholly unrelated different verse arrives", measured=True)
        for rows in (self.word_rows[:-2], wrong_words,
                [replace(item, time_seconds=item.time_seconds+1) for item in self.word_rows]):
            with self.subTest(rows=rows):
                self.assertFalse(self.store.is_compatible_lyric_upgrade(old, replace(new, timed_lyrics=tuple(rows))))
        for identity in (("netease", "456", *IDENTITY[2:]),
                (*IDENTITY[:2], "Authored Song (Live)", *IDENTITY[3:]),
                (*IDENTITY[:2], "Authored Song (Instrumental)", *IDENTITY[3:]),
                (*IDENTITY[:3], "Unrelated Cover Singer", *IDENTITY[4:]),
                (*IDENTITY[:5], 240.0)):
            with self.subTest(identity=identity):
                self.assertFalse(self.store.is_compatible_lyric_upgrade(old, replace(new, verified_identity=identity)))

    def test_sung_colons_performer_labels_parentheses_and_unknown_roles_remain(self):
        values = ("鼓起勇气：向前走", "他说：我爱你", "Music: keep singing", "(backing vocals: keep singing)",
            "Unknown Engineer: Name", "人声: Singer", "(keep singing)")
        old = match([row(i, text) for i, text in enumerate(values)], "netease", verified=True)
        self.assertEqual([item.text for item in self.store._verified_vocal_rows(old)],
            ["鼓起勇气：向前走", "他说：我爱你", "keep singing", "(backing vocals: keep singing)",
                "Unknown Engineer: Name", "人声: Singer", "(keep singing)"])
        for role in ("编程", "音频工程师", "人声录音工程师", "人声录音"):
            self.assertTrue(is_explicit_lyric_credit(f"{role}: Author"))
        self.assertFalse(is_explicit_lyric_credit("人声: Singer"))

    def test_failing_richer_sources_keep_partial_lyrics_visible(self):
        partial = match([self.word_rows[0], *self.original[1:]], "netease")
        self.store._resolve_netease = Mock(return_value=partial)
        self.store._resolve_qq = Mock(side_effect=TimeoutError)
        self.store._resolve_lrclib = Mock(return_value=None)
        self.store._resolve_amll = Mock(side_effect=ValueError)
        early = []
        self.assertIs(self.store._resolve_online("Song", "Singer", "", 180, early.append), partial)
        self.assertEqual(early, [partial])

    def test_partial_generated_cache_can_retry_but_manual_file_stays_authoritative(self):
        partial = match([self.word_rows[0], *self.original[1:]], "netease")
        self.store._persist_match("Song", "Singer", "", partial)
        cached = self.store.resolve_cached_or_local("Song", "Singer")
        self.assertFalse(has_sufficient_word_timing(cached.timed_lyrics))
        event = threading.Event()
        def resolve(*_args):
            event.set()
            return partial
        self.store._resolve_online = resolve
        self.assertTrue(self.store.enrich_in_background("Song", "Singer", unit_timing_only=True))
        self.assertTrue(event.wait(2))
        for _ in range(100):
            if not self.store._enrich_pending_keys:
                break
            time.sleep(.005)
        self.assertFalse(self.store.enrich_in_background("Song", "Singer", unit_timing_only=True))

    def test_reparse_keeps_verified_identity_without_serializing_a_trust_claim(self):
        baseline = self.store.parse_online_lyrics(raw_lrc="[00:01]keep singing", source="netease")
        baseline = replace(baseline, verified_identity=IDENTITY)
        self.assertEqual(self.store.reparse_source_match(baseline).verified_identity, IDENTITY)
        self.store._persist_match("Song", "Singer", "", baseline)
        stored = next((self.root / "cache").glob("*.lrc"))
        self.assertIsNone(self.store._read_lrc_match(stored).verified_identity)

    def test_reloaded_generated_cache_rebinds_only_to_a_new_verified_raw_original(self):
        title, artist, album, duration = IDENTITY[2:]
        raw_lrc = "\n".join(f"[{int(item.time_seconds)//60:02}:{int(item.time_seconds)%60:02}]" + item.text
            for item in self.original)
        raw_lrc = "[00:00]作词: Author\n[00:01]作曲: Composer\n[00:02]编程: Programmer\n" + raw_lrc
        old = self.store.parse_online_lyrics(raw_lrc=raw_lrc, source="netease")
        candidate = match(self.word_rows, "amll:netease:123", verified=True)
        self.store._persist_match(title, artist, album, old, duration_seconds=duration)
        cached = self.store.resolve_cached_or_local(title, artist, album, duration)
        self.assertIsNone(cached.verified_identity)
        self.assertFalse(self.store.is_compatible_lyric_upgrade(cached, candidate))
        official = replace(old, verified_identity=IDENTITY)
        def resolve(_title, _artist, _album, _duration, publish):
            publish(official)
            return candidate
        self.store._resolve_online = resolve
        self.store._enrich_background(self.store._cache_key(title, artist, album, duration), title, artist, album, duration, True)
        result = self.store.resolve_cached_or_local(title, artist, album, duration)
        self.assertEqual(result.source, "amll:netease:123")
        self.assertTrue(has_sufficient_word_timing(result.timed_lyrics))

    def test_exact_id_alone_cannot_certify_missing_or_shortened_source_content(self):
        old = match(self.original, "netease", verified=True)
        # Four unchanged rows certify 80% of row count but less than half of
        # vocal text when the unpaired rap verse carries most of the words.
        long = row(120, " ".join(f"word{index}" for index in range(100)))
        old = replace(old, timed_lyrics=tuple([*old.timed_lyrics[:4], long]))
        candidate = match([row(item.time_seconds, item.text, measured=True)
            for item in self.original[:4]], "amll:netease:123", verified=True)
        self.assertTrue(self.store._compatible_source_rows(old, candidate))
        self.assertFalse(self.store.is_compatible_lyric_upgrade(old, candidate))


if __name__ == "__main__":
    unittest.main()
