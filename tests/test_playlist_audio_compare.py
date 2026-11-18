"""Audio check for playlist-sync candidates (api.audio_compare, local.playlist_audio_compare).

Rules confirmed by the user on 2026-11-17: compare against Apple's 30-second
preview; a candidate with no audible difference is chosen automatically and
not asked about; differing passages are offered for listening; the check runs
in the background with progress in the library and a chime when done.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication

from api.apple_music_catalog import SongIdentity, preview_url, song_from_resource
from api.apple_music_sync import SyncItem, SyncPlan, apply_audio_verdicts, song_key
from api.audio_compare import RATE, AudioVerdict, compare
from local.playlist_audio_compare import AudioCompareJob, VerdictCache, judge, pair_key


def tune(seed: int, seconds: float) -> np.ndarray:
    """A synthetic song: a random melody of harmonic notes with a little noise."""
    rng = np.random.default_rng(seed)
    out = []
    while sum(len(part) for part in out) < seconds * RATE:
        length = int(rng.uniform(0.2, 0.5) * RATE)
        freq = 110.0 * 2 ** (rng.integers(0, 30) / 12.0)
        t = np.arange(length) / RATE
        note = sum((0.6 ** h) * np.sin(2 * np.pi * freq * (h + 1) * t) for h in range(4))
        out.append(note * np.hanning(length))
    pcm = np.concatenate(out)[: int(seconds * RATE)]
    return (pcm / np.abs(pcm).max() * 0.5 + 0.002 * rng.standard_normal(len(pcm))).astype(np.float32)


class CompareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.full = tune(1, 120)
        cls.other = tune(2, 120)

    def clip(self, start=40, seconds=30, source=None):
        source = self.full if source is None else source
        return source[start * RATE:(start + seconds) * RATE].copy()

    def test_same_recording_is_found_and_judged_same_through_gain_noise_and_fades(self):
        clip = self.clip() * 0.6
        clip[: 2 * RATE] *= np.linspace(0, 1, 2 * RATE, dtype=np.float32)
        clip += 0.003 * np.random.default_rng(9).standard_normal(len(clip)).astype(np.float32)
        verdict = compare(clip, self.full)
        self.assertTrue(verdict.same, verdict)
        self.assertAlmostEqual(verdict.offset_seconds, 40.0, delta=0.2)

    def test_a_different_song_is_not_found(self):
        verdict = compare(self.clip(source=self.other), self.full)
        self.assertFalse(verdict.same)
        self.assertFalse(verdict.found)
        self.assertEqual(verdict.differing, ((0.0, 30.0),))

    def test_a_replaced_passage_is_marked_in_both_timelines(self):
        clip = self.clip()
        clip[10 * RATE:16 * RATE] = self.other[50 * RATE:56 * RATE]
        verdict = compare(clip, self.full)
        self.assertFalse(verdict.same)
        self.assertTrue(verdict.found)
        start, end = verdict.differing[0]
        self.assertAlmostEqual(start, 10.0, delta=1.0)
        self.assertAlmostEqual(end, 16.0, delta=1.0)
        full_start, _full_end = verdict.full_segments()[0]
        self.assertAlmostEqual(full_start, 50.0, delta=1.0)

    def test_a_faster_version_is_not_the_same(self):
        clip = self.clip()
        faster = np.interp(np.arange(0, len(clip), 1.04), np.arange(len(clip)), clip).astype(np.float32)
        self.assertFalse(compare(faster, self.full).same)

    def test_judge_puts_the_apple_clip_inside_the_netease_recording(self):
        apple = SongIdentity("apple_music", "c1", "歌", "人", "", 120)
        netease = SongIdentity("netease", "n1", "歌", "人", "", 120)
        self.assertTrue(judge(netease, self.full, apple, self.clip()).same)
        self.assertTrue(judge(apple, self.clip(), netease, self.full).same)


class PreviewAddressTests(unittest.TestCase):
    def test_catalog_preview_is_read_https_only(self):
        song = song_from_resource({"id": "1", "type": "songs", "attributes": {
            "name": "歌", "artistName": "人", "previews": [{"url": "https://audio.example/p.m4a"}]}})
        self.assertEqual(song.preview_url, "https://audio.example/p.m4a")
        self.assertEqual(preview_url({"previews": [{"url": "http://insecure/p.m4a"}]}), "")
        self.assertEqual(preview_url({"previews": "nope"}), "")


def _song(source, song_id, title="歌"):
    return SongIdentity(source, song_id, title, "人", "", 120.0, catalog_id=song_id if source == "apple_music" else "")


class PlanTests(unittest.TestCase):
    def plan(self):
        source = _song("netease", "n1")
        return SyncPlan("netease_to_apple", choices=(SyncItem("choose", source, None, (_song("apple_music", "a1"), _song("apple_music", "a2"))),),
                        target_added=(SyncItem("target_added", None, _song("apple_music", "a2")),),
                        target_keys=frozenset({"a2"}))

    def test_a_same_sounding_candidate_is_chosen_and_never_asked(self):
        plan = apply_audio_verdicts(self.plan(), {"n1": {"a1": AudioVerdict(True, 0.97)}})
        self.assertEqual(plan.choices, ())
        self.assertEqual([song_key(item.target) for item in plan.additions], ["a1"])
        self.assertIn("音轨一致", plan.additions[0].reason)

    def test_a_same_sounding_candidate_already_in_the_target_is_only_paired(self):
        plan = apply_audio_verdicts(self.plan(), {"n1": {"a1": AudioVerdict(False, 0.5), "a2": AudioVerdict(True, 0.98)}})
        self.assertEqual((plan.choices, plan.additions), ((), ()))
        self.assertEqual([song_key(item.target) for item in plan.pair_only], ["a2"])
        self.assertEqual(plan.target_added, ())

    def test_differing_candidates_stay_with_the_user(self):
        plan = apply_audio_verdicts(self.plan(), {"n1": {"a1": AudioVerdict(False, 0.7, 12.0, ((3.0, 9.0),))}})
        self.assertEqual(len(plan.choices), 1)


class FakeAudio:
    def __init__(self, sounds):
        self.sounds = sounds
        self.fetched = []

    def pcm(self, song):
        self.fetched.append(song.song_id)
        return self.sounds.get(song.song_id)


class JobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.full = tune(3, 90)

    def test_job_reports_progress_verdicts_and_caches_them(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        cache = VerdictCache(Path(folder.name) / "compare.json")
        source = _song("netease", "n1")
        same, other = _song("apple_music", "a1"), _song("apple_music", "a2")
        audio = FakeAudio({"n1": self.full, "a1": self.full[20 * RATE:50 * RATE], "a2": tune(4, 30)})
        item = SyncItem("choose", source, None, (same, other))
        job = AudioCompareJob((item,), audio, cache)
        progress, verdicts, done = [], [], []
        job.progress.connect(lambda a, b: progress.append((a, b)))
        job.verdictReady.connect(lambda s, c, v: verdicts.append((s, c, v.same)))
        job.done.connect(lambda total, resolved: done.append((total, resolved)))
        job.run()
        self.assertEqual(progress, [(0, 1), (1, 1)])
        self.assertEqual(verdicts, [("n1", "a1", True), ("n1", "a2", False)])
        self.assertEqual(done, [(1, 1)])
        again = VerdictCache(Path(folder.name) / "compare.json")
        self.assertTrue(again.get(pair_key(source, same)).same)
        self.assertNotIn("token", (Path(folder.name) / "compare.json").read_text(encoding="utf-8").lower())
        audio.fetched.clear()
        AudioCompareJob((item,), audio, again).run()
        self.assertEqual(audio.fetched, [], "cached pairs are not fetched again")

    def test_missing_audio_is_skipped_without_stopping_the_queue(self):
        cache = VerdictCache(None)
        first = SyncItem("choose", _song("netease", "n1"), None, (_song("apple_music", "a1"),))
        second = SyncItem("choose", _song("netease", "n2"), None, (_song("apple_music", "a2"),))
        audio = FakeAudio({"n2": self.full, "a2": self.full[:30 * RATE]})
        job = AudioCompareJob((first, second), audio, cache)
        done = []
        job.done.connect(lambda total, resolved: done.append((total, resolved)))
        job.run()
        self.assertEqual(done, [(2, 1)])


class DialogVerdictTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_verdicts_label_cards_and_offer_the_differing_passage_on_both_sides(self):
        from local.apple_music_sync_dialog import PlaylistSyncDialog
        from local.apple_music_sync_flow import SyncPreview
        from api.apple_music_sync import PlaylistLink
        source = _song("netease", "n1")
        candidates = (_song("apple_music", "a1"), _song("apple_music", "a2"))
        plan = SyncPlan("netease_to_apple", choices=(SyncItem("choose", source, None, candidates),))
        preview = SyncPreview("netease_to_apple", "npl", "apl", plan, PlaylistLink("netease", "npl", "apple_music", "apl"))
        dialog = PlaylistSyncDialog()
        self.addCleanup(dialog.close)
        dialog.set_preview(preview, verdicts={"n1": {"a1": AudioVerdict(False, 0.7, 40.0, ((10.0, 16.0),))}})
        card = dialog._choices["n1"].cards[0]
        self.assertIn("有区别", card.verdict.text())
        self.assertEqual(len(card.diff_buttons), 2)
        asked = []
        dialog.auditionRequested.connect(lambda song, start, end: asked.append((song.source_id, start, end)))
        card.diff_buttons[0].click()  # the NetEase source, offset to where the clip sits
        card.diff_buttons[1].click()  # the Apple clip, in its own time
        self.assertEqual(asked, [("netease", 50.0, 56.0), ("apple_music", 10.0, 16.0)])
        dialog.show_verdict("n1", "a1", AudioVerdict(False, 0.2, found=False))
        self.assertIn("另一", card.verdict.text())
        self.assertEqual(len(dialog._choices["n1"].cards), 1, "only the most similar song is shown")
        dialog.set_compare_progress(3, 9, True)
        self.assertEqual(dialog.compare_status.text(), "音轨比对 3/9")


class LibraryProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_library_key_shows_progress_then_returns(self):
        from local.music_library_window import MusicLibraryWindow
        window = MusicLibraryWindow()
        self.addCleanup(window.close)
        window.set_sync_progress(4, 10, True)
        self.assertEqual(window.playlist_sync_button.text(), "音轨比对 4/10")
        window.set_sync_progress(10, 10, False)
        self.assertEqual(window.playlist_sync_button.text(), "同步歌单")


if __name__ == "__main__":
    unittest.main()
