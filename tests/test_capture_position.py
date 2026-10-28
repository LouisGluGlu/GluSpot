"""Replay serialized SMTC observations using explicit wall/monotonic clocks."""
import json
import unittest
from dataclasses import replace
from pathlib import Path

from api.capture.contracts import TimelineSample
from api.capture.position import PositionModel


class PositionModelTests(unittest.TestCase):
    def sample(self, **values):
        return replace(TimelineSample('netease', 'A', 10, 100, 1000,
                                      updated_at=999, is_playing=True, end_seconds=200), **values)

    def test_interpolation_uses_source_update_time(self):
        model = PositionModel()
        self.assertTrue(model.observe(self.sample()))
        self.assertEqual(model.position(102), 13)

    def test_repeated_stale_polls_do_not_reanchor(self):
        model = PositionModel()
        model.observe(self.sample())
        self.assertFalse(model.observe(self.sample(observed_at=110, wall_time=1010)))
        self.assertEqual(model.position(110), 21)
        self.assertEqual(model.reanchors, 1)

    def test_pause_and_resume_repeated_anchor(self):
        model = PositionModel()
        model.observe(self.sample())
        model.observe(self.sample(observed_at=105, wall_time=1005, is_playing=False))
        self.assertEqual(model.position(115), 16)
        model.observe(self.sample(observed_at=115, wall_time=1015))
        self.assertEqual(model.position(117), 18)

    def test_backwards_seek_and_track_session_changes(self):
        model = PositionModel()
        model.observe(self.sample())
        model.observe(self.sample(position_seconds=2, updated_at=1005, observed_at=105, wall_time=1005))
        self.assertEqual(model.position(106), 3)
        for changes in ({'track_id': 'B'}, {'session_id': 'qq'}, {'track_id': 'A'}):
            model.observe(self.sample(position_seconds=0, updated_at=1010, observed_at=110, wall_time=1010, **changes))
            self.assertEqual(model.position(110), 0)

    def test_end_start_lyric_bound_and_rate(self):
        model = PositionModel()
        model.observe(self.sample(start_seconds=5, end_seconds=30, rate=2), lyric_duration=25)
        self.assertEqual(model.position(101), 14)
        self.assertEqual(model.position(200), 25)
        self.assertEqual(model.position(0), 12)

    def test_clock_jump_does_not_change_prediction(self):
        for jump in (3600, -3600):
            model = PositionModel()
            model.observe(self.sample())
            model.observe(self.sample(observed_at=101, wall_time=1001+jump))
            self.assertEqual(model.position(102), 13)
            model.observe(self.sample(position_seconds=15, updated_at=1003+jump, observed_at=103, wall_time=1003+jump))
            self.assertEqual(model.position(104), 16)

    def test_small_drift_is_not_reanchored(self):
        model = PositionModel()
        model.observe(self.sample())
        self.assertFalse(model.observe(self.sample(position_seconds=12.8, updated_at=1002, observed_at=102, wall_time=1002)))
        self.assertEqual(model.position(102), 13)

    def test_missing_future_timestamp_and_invalid_out_of_order_sample(self):
        model = PositionModel()
        model.observe(self.sample(updated_at=None))
        self.assertEqual(model.position(101), 11)
        self.assertFalse(model.observe(self.sample(observed_at=99)))
        self.assertFalse(model.observe(self.sample(rate=float('nan'))))
        model.observe(self.sample(track_id='B', updated_at=9000))
        self.assertEqual(model.position(101), 11)

    def test_recorded_timeline_replay(self):
        root = Path(__file__).parent / 'fixtures' / 'capture'
        recordings = sorted(root.glob('*.json'))
        self.assertTrue(recordings, 'Serialized timeline recordings are required')
        for path in recordings:
            recording = json.loads(path.read_text())
            with self.subTest(path=path.name):
                model = PositionModel()
                for event in recording['events']:
                    if 'sample' in event:
                        model.observe(TimelineSample(**event['sample']))
                    self.assertAlmostEqual(model.position(event['now']), event['expected'], places=5)
