"""System-captured songs: a colour change from the lyric and NetEase's chorus,
drum light from the live audio, and the solid field when neither can help."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from local import sea_lyric_background as sea

VERSE = ["walking down the empty road", "nobody knows my name tonight", "streetlights fading one by one",
         "i keep the radio low"]
VERSE2 = ["morning finds me on the stairs", "coffee cold and nothing said", "you were always somewhere else",
          "i should have known it then"]
CHORUS = ["we are the fire in the rain", "burning brighter every day", "never let it fade away",
          "we are the fire in the rain"]


def lyric():
    out, t = [], 12.0
    for section in (VERSE, CHORUS, VERSE2, CHORUS, CHORUS):
        for line in section:
            out.append((t, line))
            t += 4.0
    return tuple(out)


class CapturedPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def plan(self, **kw):
        analyzer = sea.SeaAnalyzer()
        self.addCleanup(analyzer.shutdown)
        hints = kw.pop("hints", ("", (), ""))
        return analyzer, analyzer.captured_plan("captured:t", (QColor("#2b4f9a"), QColor("#e0a020")), (.6, .4),
                                                None, kw.pop("duration", 200.0), hints=hints, position=0.0)

    def test_the_lyric_places_the_change(self):
        _analyzer, plan = self.plan(hints=("qq:1", lyric(), "QQMusic"))
        self.assertTrue(plan.captured)
        self.assertEqual(plan.change_end, 12.0 + 4 * 4)
        self.assertEqual(plan.change_source, "lyrics")
        self.assertTrue(plan.settled)
        self.assertTrue(plan.pushes)

    def test_netease_chorus_alone_places_it_for_a_netease_song(self):
        plan = sea.SeaPlan(QColor("navy"), "white", QColor("orange"), "black", ready=True, captured=True,
                           created_at=0.0, audio_seconds=200.0)
        plan.climax = {"netease": 61.4, "audio": None, "chorus_done": True}
        sea.settle_captured(plan, (), position=0.0, now=1.0)
        self.assertEqual((plan.change_end, plan.change_source), (61.4, "netease"))
        self.assertTrue(plan.settled)

    def test_nothing_to_go_on_falls_back_after_a_wait(self):
        plan = sea.SeaPlan(QColor("navy"), "white", QColor("orange"), "black", ready=True, captured=True,
                           created_at=0.0, audio_seconds=200.0)
        plan.climax = {"netease": None, "audio": None, "chorus_done": True}
        sea.settle_captured(plan, (), position=0.0, now=2.0)
        self.assertIsNone(plan.change_end)
        self.assertFalse(plan.settled)                       # the lyric may still arrive
        sea.settle_captured(plan, (), position=9.0, now=sea.CAPTURED_SETTLE_S + 1)
        self.assertTrue(plan.settled)                        # -> the solid field

    def test_a_pending_chorus_lookup_holds_the_fallback(self):
        plan = sea.SeaPlan(QColor("navy"), "white", QColor("orange"), "black", ready=True, captured=True,
                           created_at=0.0, audio_seconds=200.0)
        plan.climax = {"netease": None, "audio": None, "chorus_done": False}
        sea.settle_captured(plan, (), position=0.0, now=60.0)
        self.assertFalse(plan.settled)

    def test_a_late_lyric_moves_the_change_only_before_it_shows(self):
        plan = sea.SeaPlan(QColor("navy"), "white", QColor("orange"), "black", ready=True, captured=True,
                           created_at=0.0, audio_seconds=200.0)
        plan.climax = {"netease": 100.0, "audio": None, "chorus_done": True}
        sea.settle_captured(plan, (), position=0.0, now=1.0)
        self.assertEqual(plan.change_end, 100.0)
        sea.settle_captured(plan, lyric(), position=90.0, now=2.0)     # too late to move back to 28 s
        self.assertEqual(plan.change_end, 100.0)


class LiveBeatTests(unittest.TestCase):
    def test_rises_of_the_low_band_become_spaced_hits_quieter_before_the_change(self):
        plan = sea.SeaPlan(QColor("navy"), "white", QColor("orange"), "black", change_end=60.0)
        beats = sea.LiveBeats()
        position = 10.0
        for _ in range(30):                       # steady level: no hits
            beats.feed(plan, .2, position)
            position += 1 / 60
        self.assertEqual(plan.drums, ())
        self.assertTrue(beats.feed(plan, .6, position))
        self.assertFalse(beats.feed(plan, .7, position + .1))           # too soon
        self.assertEqual(len(plan.drums), 1)
        before = plan.drums[0][1]
        for _ in range(60):
            beats.feed(plan, .2, 70.0)
        self.assertTrue(beats.feed(plan, .6, 70.5))
        self.assertGreater(plan.drums[-1][1], before)                  # after the change: brighter
        # Seeking back drops hits ahead of the new position.
        beats.feed(plan, .2, 20.0)
        self.assertTrue(all(t <= 20.0 for t, _s in plan.drums))


if __name__ == "__main__":
    unittest.main()
