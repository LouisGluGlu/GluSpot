import io
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from api import netease_chorus
from local import sea_climax as climax

RATE = climax.RATE


def _song(parts, gap=4.0, start=10.0):
    """Timed lines for a song built from sections of lines, ``gap`` s apart."""
    out, t = [], start
    for section in parts:
        for line in section:
            out.append((t, line))
            t += gap
    return out


VERSE1 = ["walking down the empty road", "nobody knows my name tonight", "streetlights fading one by one",
          "i keep the radio low"]
VERSE2 = ["morning finds me on the stairs", "coffee cold and nothing said", "you were always somewhere else",
          "i should have known it then"]
PRE = ["and i hold on", "i hold on to you"]
CHORUS = ["we are the fire in the rain", "burning brighter every day", "never let it fade away",
          "we are the fire in the rain"]
BRIDGE = ["silence in the hall", "echoes when you call"]


class LyricRepetitionTests(unittest.TestCase):
    def test_first_chorus_after_the_verse(self):
        lines = _song([VERSE1, PRE, CHORUS, VERSE2, PRE, CHORUS, BRIDGE, CHORUS])
        start = climax.lyric_chorus(lines, 200.0)
        self.assertEqual(start, 10.0 + 4.0 * 6)         # after verse 1 and the pre-chorus

    def test_pre_chorus_left_out_once_is_the_lead_in(self):
        lines = _song([VERSE1, PRE, CHORUS, VERSE2, PRE, CHORUS, BRIDGE, CHORUS])
        section = climax.lyric_section(lines, 220.0)
        self.assertEqual(section["head"], 10.0 + 4.0 * 4)   # the pre-chorus opens the occurrence ...
        self.assertEqual(section["pick"], 10.0 + 4.0 * 6)   # ... the chorus proper starts after it
        self.assertFalse(section["unbroken"])

    def test_pre_chorus_sung_every_time_cannot_be_split_by_the_lyric(self):
        lines = _song([VERSE1, PRE, CHORUS, VERSE2, PRE, CHORUS, BRIDGE, PRE, CHORUS])
        section = climax.lyric_section(lines, 220.0)
        self.assertTrue(section["unbroken"])
        self.assertEqual(section["pick"], section["head"])
        # NetEase's time inside the occurrence splits it when the arrangement
        # lifts there (the chorus proper) ...
        chorus = 10.0 + 4.0 * 6
        step = (np.arange(int(220 / climax.STEP)) * climax.STEP >= chorus).astype(float)
        found = {"section": section, "netease": chorus, "audio": None, "full": step}
        self.assertEqual(climax.choose(found, ()), (chorus, "netease"))
        # ... and not when it does not (NetEase's highlight in the chorus's second half).
        self.assertEqual(climax.choose(dict(found, full=np.zeros(440)), ()), (section["head"], "lyrics"))

    def test_song_that_opens_with_its_chorus_lands_on_the_second_one(self):
        lines = _song([CHORUS, VERSE1, CHORUS, VERSE2, CHORUS])
        self.assertEqual(climax.lyric_chorus(lines, 200.0), 10.0 + 4.0 * 8)

    def test_credits_fillers_and_asides_do_not_count(self):
        lines = [(0.5, "作词 : 某人"), (1.0, "Produced by A and B at C"), (2.0, "Oh, oh"), (3.0, "(yeah)")]
        lines += _song([VERSE1, CHORUS, VERSE2, ["We are the fire in the rain (fire)", "Burning brighter every day!",
                                                   "never let it fade away", "we are the fire in the rain"]])
        self.assertEqual([t for t, _ in climax.lyric_lines(lines)][:1], [10.0])
        self.assertEqual(climax.lyric_chorus(lines, 200.0), 10.0 + 4.0 * 4)

    def test_a_lone_repeated_hook_line_is_not_a_chorus(self):
        lines = _song([VERSE1, ["baby i love you"], VERSE2, ["baby i love you"], BRIDGE])
        self.assertIsNone(climax.lyric_chorus(lines, 200.0))

    def test_a_section_sung_twice_in_a_row_stays_two_occurrences(self):
        lines = _song([VERSE1, VERSE2, CHORUS, CHORUS, BRIDGE])
        groups = climax.repeated_blocks([t for _s, t in climax.lyric_lines(lines)])
        self.assertTrue(any(len(group) >= 2 for group in groups))
        self.assertEqual(climax.lyric_chorus(lines, 200.0), 10.0 + 4.0 * 8)

    def test_netease_wins_inside_the_lead_in_but_not_on_the_second_half(self):
        lines = _song([VERSE1, PRE, CHORUS, VERSE2, PRE, CHORUS, BRIDGE, PRE, CHORUS])
        section = climax.lyric_section(lines, 220.0)
        found = {"section": section, "netease": None, "audio": None, "full": np.zeros(0)}
        chorus = section["pick"]
        self.assertEqual(climax.choose(dict(found, netease=chorus - 0.4), ())[1], "netease")
        self.assertEqual(climax.choose(dict(found, netease=chorus + 9.0), ()), (chorus, "lyrics"))
        self.assertEqual(climax.choose(dict(found, netease=150.0), ()), (chorus, "lyrics"))
        self.assertEqual(climax.choose(dict(found, section=None, netease=150.0), ()), (150.0, "netease"))

    def test_snaps_to_a_strong_drum_within_half_a_second(self):
        drums = ((40.1, 0.4), (40.35, 0.9), (41.2, 1.0))
        self.assertEqual(climax.snap(40.0, drums), 40.35)
        self.assertEqual(climax.snap(39.0, drums), 39.0)


def _tone(freqs, seconds):
    t = np.arange(int(RATE * seconds)) / RATE
    return sum(np.sin(2 * np.pi * f * t) for f in freqs).astype(np.float32) / len(freqs)


class AudioRepetitionTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(5)

        def verse(seed):
            local = np.random.default_rng(seed)
            notes = local.choice([196.0, 220.0, 247.0, 262.0, 294.0, 330.0, 349.0], size=10)
            return np.concatenate([_tone([n, n * 1.5], 1.5) * 0.35 for n in notes])

        chorus_notes = [523.0, 440.0, 392.0, 349.0, 392.0, 440.0, 523.0, 587.0, 659.0, 587.0, 523.0, 440.0]
        chorus = np.concatenate([_tone([n, n * 1.25, n / 2], 1.5) for n in chorus_notes])
        noise = lambda n: (rng.standard_normal(n) * 0.01).astype(np.float32)
        parts = [verse(1), verse(2), chorus, verse(3), verse(4), chorus, verse(5), chorus, verse(6)]
        self.starts = np.cumsum([0] + [len(p) for p in parts]) / RATE
        pcm = np.concatenate(parts)
        self.pcm = pcm + noise(len(pcm))

    def test_finds_the_repeated_section(self):
        segments = climax.repeated_segments(climax.chroma(self.pcm))
        self.assertTrue(segments)
        chorus_lag = self.starts[5] - self.starts[2]
        self.assertTrue(any(abs(lag - chorus_lag) < 2.0 for _a, _b, lag in segments))

    def test_audio_chorus_lands_on_the_first_chorus(self):
        full = climax.fullness(self.pcm, np.zeros(0))
        moment = climax.audio_chorus(self.pcm, full, len(self.pcm) / RATE)
        self.assertIsNotNone(moment)
        self.assertLess(abs(moment - self.starts[2]), 3.0)

    def test_pick_without_lyric_or_netease_uses_the_audio_and_stays_fast(self):
        import time
        began = time.perf_counter()
        moment, source = climax.pick(self.pcm, ())
        self.assertEqual(source, "audio")
        self.assertLess(abs(moment - self.starts[2]), 3.0)
        self.assertLess(time.perf_counter() - began, 1.0)


class NeteaseChorusTests(unittest.TestCase):
    def test_reads_the_first_start_and_caches_it(self):
        calls = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout):
            calls.append(request.data)
            body = {"code": 200, "chorus": [{"id": 103841, "startTime": 41844, "endTime": 64356}]}
            return Response(json.dumps(body).encode())

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chorus.json"
            self.assertAlmostEqual(netease_chorus.chorus_start("103841", path=path, opener=opener), 41.844)
            self.assertAlmostEqual(netease_chorus.chorus_start("103841", path=path, opener=opener), 41.844)
            self.assertEqual(len(calls), 1)
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(stored["103841"]), {"start", "at"})
        self.assertIsNone(netease_chorus.chorus_start("not-an-id"))
        self.assertEqual(netease_chorus.song_id("owned:netease:536622304"), "536622304")
        self.assertEqual(netease_chorus.song_id("owned:apple_music:1"), "")
        self.assertIsNone(netease_chorus.parse({"code": 200, "chorus": []}))


class LateLyricTests(unittest.TestCase):
    def test_a_lyric_arriving_after_the_analysis_moves_the_change_before_it_shows(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtGui import QColor
        from local import sea_lyric_background as sea
        rng = np.random.default_rng(1)
        pcm = (rng.standard_normal(RATE * 200) * 0.05).astype(np.float32)
        plan = sea.analyse(pcm, sea.SeaPlan(QColor("navy"), "white", QColor("orange"), "black"))
        self.assertFalse(plan.lyrics_seen)
        lines = _song([VERSE1, PRE, CHORUS, VERSE2, PRE, CHORUS, BRIDGE, CHORUS])
        chorus = climax.lyric_chorus(lines, 200.0)
        plan.audio_seconds = 200.0
        moved = sea.refine_with_lyrics(plan, lines, position=5.0)
        if moved:
            self.assertEqual(plan.change_source, "lyrics")
            self.assertAlmostEqual(plan.change_end, chorus, delta=climax.SNAP_S)
            self.assertIsNotNone(plan.word_switch)
        else:
            self.assertAlmostEqual(plan.change_end, chorus, delta=0.75)
        # Once the change could already show, a late lyric no longer moves it.
        late = sea.analyse(pcm, sea.SeaPlan(QColor("navy"), "white", QColor("orange"), "black"))
        late.audio_seconds = 200.0
        before = late.change_end
        self.assertFalse(sea.refine_with_lyrics(late, lines, position=(before or 0.0)))
        self.assertEqual(late.change_end, before)
        # A lyric that runs past the recording belongs to another song.
        self.assertEqual(sea.usable_lyrics([(t + 400.0, x) for t, x in lines], 200.0), ())


if __name__ == "__main__":
    unittest.main()
