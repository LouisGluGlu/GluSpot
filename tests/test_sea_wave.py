import unittest
from unittest.mock import patch

from local.sea_wave import sea_intensity

from local.sea_wave import SeaWaveState


class SeaWaveTests(unittest.TestCase):
    def test_gain_matches_loud_and_quiet(self):
        states = [SeaWaveState(), SeaWaveState()]
        for n in range(180):
            for state, level in zip(states, (0.02, 0.2)):
                state.update((level, level, level), n / 60)
        self.assertAlmostEqual(states[0].bands[0], states[1].bands[0], places=3)

    def test_attack_release_and_silence(self):
        state = SeaWaveState()
        state.update((0, 0, 0), 0)
        state.update((.2, .2, .2), .03)
        self.assertGreater(state.bands[0], .30)
        state.update((0, 0, 0), .06)
        self.assertGreater(state.bands[0], .4)
        for n in range(1, 400):
            state.update((0, 0, 0), .06 + n / 60)
        self.assertLess(state.bands[0], .001)
        self.assertLess(abs(state.displacement(.5, 0, 'strong')), .1)

    def test_environment_and_onset(self):
        with patch.dict("os.environ", {"GLUSPOT_SEA_INTENSITY": "strong"}):
            self.assertEqual(sea_intensity(), "strong")
            self.assertEqual(sea_intensity("gentle"), "gentle")
        state = SeaWaveState()
        for n in range(60):
            state.update((0.0, 0.0, 0.0), n / 60)
        state.update((0.2, 0.0, 0.0), 1.0)
        self.assertGreater(state.impulse, 0.0)
        for n in range(1, 180):
            state.update((0.0, 0.0, 0.0), 1.0 + n / 60)
        self.assertLess(state.impulse, .001)
        self.assertLess(abs(state.swell), .001)

    def test_impulse_lifts_entire_surface_and_then_decays(self):
        state = SeaWaveState()
        for n in range(60):
            state.update((0.0, 0.0, 0.0), n / 60)
        state.update((0.2, 0.0, 0.0), 1.0)
        with_impulse = [state.displacement(p, 0.4, 'strong') for p in (0, .25, .5, .75, 1)]
        impulse = state.impulse
        state.impulse = 0
        without_impulse = [state.displacement(p, 0.4, 'strong') for p in (0, .25, .5, .75, 1)]
        self.assertGreater(impulse, 0)
        for raised, base in zip(with_impulse, without_impulse):
            self.assertGreaterEqual(max(with_impulse), max(without_impulse) - 0.001)
        for n in range(1, 240):
            state.update((0.0, 0.0, 0.0), 1.0 + n / 60)
        self.assertLess(abs(state.displacement(.5, .4, 'strong')), .01)

    def test_local_shock_propagates_away_from_injection(self):
        state = SeaWaveState()
        state.velocity[state.point_count // 2] = -24.0
        center = state.point_count // 2
        for frame in range(20):
            state.update((0, 0, 0), frame / 60)
        self.assertGreater(abs(state.surface[center - 3]), .01)
        self.assertGreater(abs(state.surface[center + 3]), .01)
        self.assertGreater(abs(state.surface[center - 6]) + abs(state.surface[center + 6]), 0.0)

    def test_stats_center_jump_and_impulse_correlation(self):
        from tools.sea_stats import summarize
        rows = [dict(bands=[i, 0, 0], water_y=[100 - i] * 5, impulse=i)
                for i in (0, 1, 3, 2)]
        stats = summarize(rows)
        self.assertEqual(stats['center_jump_p99'], 2)
        self.assertAlmostEqual(stats['corr_impulse_center_rise'], 1)

    def test_vocal_response_follows_voice_not_bass_and_settles(self):
        vocal = SeaWaveState()
        bass = SeaWaveState()
        for n in range(120):
            vocal.update((0.0, 0.0, 0.0), n / 60, vocal=0.1)
            bass.update((0.3, 0.0, 0.0), n / 60, vocal=0.0, intensity="vocal")
        self.assertGreater(vocal.vocal, .8)
        self.assertGreater(max(vocal.displacement(pos / 100, 0, 'vocal') for pos in range(101)), 10)
        self.assertLess(abs(bass.displacement(.5, 0, 'vocal')), .5)
        for n in range(1, 400):
            vocal.update((0.0, 0.0, 0.0), 2 + n / 60, vocal=0.0)
        self.assertLess(abs(vocal.displacement(.5, 0, 'vocal')), .01)

    def test_intensity_and_limit(self):
        state = SeaWaveState()
        for n in range(60):
            state.update((.2, .1, .08), n / 60)
        values = [max(state.displacement(pos / 100, 0, name) for pos in range(101))
                  for name in ('gentle', 'medium', 'strong')]
        self.assertLess(values[0], values[1])
        self.assertLess(values[1], values[2])
        for pos in range(101):
            self.assertGreaterEqual(state.displacement(pos / 100, 3, 'strong'), -3)
            self.assertLessEqual(state.displacement(pos / 100, 3, 'strong'), 49)


if __name__ == '__main__':
    unittest.main()

class SeaRenderIdempotenceTests(unittest.TestCase):
    def test_same_timestamp_does_not_advance_surface(self):
        state = SeaWaveState()
        state.update((.4, .2, .1), 1.0)
        before = (tuple(state.surface), tuple(state.velocity), state.updated_at)
        first = state.displacement(.5, 0.2, "medium")
        second = state.displacement(.5, 0.2, "medium")
        self.assertEqual(first, second)
        self.assertEqual(before, (tuple(state.surface), tuple(state.velocity), state.updated_at))
