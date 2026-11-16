"""Readiness/degraded attach and saved-image analysis without any PC action."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

def load(name, path):
    spec = spec_from_file_location(name, ROOT / path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

probe = load("t156_probe_tests", "handoff/narrow-v4/pc_verify/native_probe.py")
metrics = load("t156_metrics_tests", "handoff/narrow-v4/pc_verify_t156/metrics.py")

class AttachReadinessTests(unittest.TestCase):
    def sample(self, **changes):
        values = dict(playing=True, duration=120., position_source="capture-model",
                      source_lines=2, position=10.2, initial_position=10., elapsed=2.)
        values.update(changes)
        return probe.attach_readiness(**values)

    def test_full_requires_advancing_real_playback_and_lyrics(self):
        self.assertEqual(self.sample(), "full")
        self.assertIsNone(self.sample(position=10.))
        self.assertIsNone(self.sample(initial_position=None))

    def test_missing_lyrics_wait_independently_until_45_seconds(self):
        self.assertIsNone(self.sample(source_lines=0, elapsed=44.999))
        self.assertEqual(self.sample(source_lines=0, elapsed=45.), "degraded")

    def test_lyrics_arriving_at_deadline_remain_full(self):
        self.assertEqual(self.sample(elapsed=45.), "full")

    def test_degraded_does_not_relax_any_playback_gate(self):
        for changes in (dict(playing=False), dict(duration=0), dict(position=10.),
                        dict(initial_position=None), dict(position_source=""),
                        dict(position_source="none"), dict(position_source="capture-unavailable")):
            with self.subTest(changes=changes), self.assertRaisesRegex(
                    RuntimeError, "No advancing real playback with source lyrics; physical acceptance unavailable"):
                self.sample(source_lines=0, elapsed=45., **changes)

class SavedCaptureTests(unittest.TestCase):
    def test_centerline_flat_image_has_no_seam(self):
        report = metrics.seam_scan(Image.new("RGB", (360, 640), (45, 45, 45)))
        self.assertEqual(report["veil_seam_max_step"], 0.)
        self.assertEqual(report["status"], "PASS")

    def test_centerline_detects_hard_plate(self):
        image = Image.new("RGB", (480, 800), (25, 25, 25))
        image.paste((80, 80, 80), (0, 214, 480, 445))
        report = metrics.seam_scan(image)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["max_step_row"], 214)

    def test_pixel_evidence_records_empty_control_patch(self):
        state = dict(geometry=[0, 0, 360, 640], resize_chrome_hold=True, visibility=1.,
                     rects={"play": [10, 10, 25, 25]})
        report = metrics.chrome_pixels(Image.new("RGB", (720, 1280), "black"), state)
        self.assertFalse(report["patches"]["play"]["nonempty"])
        self.assertTrue(report["hold"])

    def test_timing_records_complete_then_return_and_fast_floor(self):
        history = [dict(state="SPLITTING", at=0., duration=.5),
                   dict(state="MERGING", at=.5, duration=.5),
                   dict(state="NARROW", at=1., duration=.5),
                   dict(state="SPLITTING", at=2., duration=.2),
                   dict(state="WIDE", at=2.2, duration=.2)]
        report = metrics.transition_timing(history + history)
        self.assertEqual(len(report["transitions"]), 3)
        self.assertTrue(report["slow_seen"] and report["fast_seen"])
        self.assertTrue(all(row["status"] == "PASS" for row in report["transitions"]))

    def test_early_transition_is_fail_and_incomplete_is_unverified(self):
        report = metrics.transition_timing([dict(state="SPLITTING", at=0., duration=.5),
            dict(state="MERGING", at=.1, duration=.5)])
        self.assertEqual([row["status"] for row in report["transitions"]], ["FAIL", "UNVERIFIED"])

    def test_repeated_same_state_diagnostics_do_not_shorten_animation(self):
        report = metrics.transition_timing([dict(state="SPLITTING", at=0., duration=.5),
            dict(state="SPLITTING", at=.03, duration=.5), dict(state="WIDE", at=.5, duration=.5)])
        self.assertEqual(len(report["transitions"]), 1)
        self.assertEqual(report["transitions"][0]["actual_seconds"], .5)

if __name__ == "__main__":
    unittest.main()
