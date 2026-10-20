"""Check benchmark arithmetic and its physical-pixel contract without Qt."""
import unittest
from types import SimpleNamespace

from tests.perf_music_surface import distribution, glyph_resource_snapshot, physical_size, source_manifest, validate_prepared


class MusicSurfacePerformanceHarnessTests(unittest.TestCase):
    def test_percentiles_use_nearest_rank_and_preserve_tail(self):
        stats = distribution(list(range(1, 101)))
        self.assertEqual((stats["p50_ms"], stats["p95_ms"], stats["p99_ms"], stats["max_ms"]),
                         (50.5, 95, 99, 100))
        self.assertEqual(stats["over_8_33_ms"], 92)
        self.assertEqual(stats["over_16_67_ms"], 84)

    def test_empty_samples_are_distinct_from_zero_cost(self):
        self.assertEqual(distribution([]), {"n": 0})
        self.assertEqual(distribution([0])["max_ms"], 0)

    def test_target_size_scales_both_axes_for_dpr(self):
        self.assertEqual(physical_size(960, 540, 2), (1920, 1080))
        self.assertEqual(physical_size(1920, 1080, 1), (1920, 1080))
        self.assertEqual(physical_size(330, 400, 1.25), (412, 500))
        with self.assertRaises(ValueError):
            physical_size(0, 540, 2)

    def test_source_manifest_freezes_production_without_generated_results(self):
        manifest = source_manifest()
        self.assertIn("local/music_surface_window.py", manifest)
        self.assertIn("local/music_surface_background.py", manifest)
        self.assertTrue(all(len(digest) == 64 for digest in manifest.values()))
        self.assertFalse(any("logs/" in path or "tests/" in path for path in manifest))

    def test_loading_or_empty_async_results_cannot_be_measured_as_fast_lyrics(self):
        self.assertFalse(validate_prepared(None, [(0., "Song")], []))
        self.assertFalse(validate_prepared(SimpleNamespace(timed=()), [(0., "Song")], []))

    def test_async_gate_rejects_dropped_source_rows_or_genuine_time_anchors(self):
        timed = ((0., "Song"), (5., "Line"))
        units = (("anchor-a",), ("anchor-b",))
        prepared = SimpleNamespace(timed=timed, source=SimpleNamespace(timed=timed, units=units), units=units)
        self.assertTrue(validate_prepared(prepared, timed, units))
        prepared.units = ((), ())
        self.assertFalse(validate_prepared(prepared, timed, units))
        prepared.units = units
        prepared.timed = timed[:1]
        self.assertFalse(validate_prepared(prepared, timed, units))

    def test_glyph_accounting_separates_cold_current_partial_and_shared_alias(self):
        class Image:
            def __init__(self, size, key):
                self.size, self.key = size, key
            def sizeInBytes(self):
                return self.size
            def cacheKey(self):
                return self.key

        cold, current, partial = Image(32, 1), Image(48, 2), Image(60, 3)
        window = SimpleNamespace(
            _emphasis_protected_glyph_runs={"current"}, _emphasis_protected_source_keys={2},
            _emphasis_protected_tint_color=9, _glyph_cache_key="current", _glyph_cache=(current, ()),
            _glyph_run_cache={"cold": (cold, ()), "current": (current, ())}, _glyph_run_cache_bytes=80,
            _isolated_glyph_build_last_key="partial", _isolated_glyph_builds={
                "partial": {"result": [(partial, 0.)], "bytes": 60}},
            _isolated_glyph_builds_bytes=60, _glyph_tint_cache={(2, 9): current, (2, 8): cold})
        result = glyph_resource_snapshot(window)
        self.assertEqual(result["run"]["cold_bytes"], 32)
        self.assertEqual(result["run"]["current_bytes"], 48)
        self.assertEqual(result["run"]["bytes"], result["run"]["counter_bytes"])
        self.assertEqual(result["partial"]["working_bytes"], 60)
        self.assertEqual(result["tint"]["current_bytes"], 48)
        self.assertEqual(result["tint"]["cold_bytes"], 32)
        self.assertTrue(result["singleton_run"]["aliases_retained_run"])


if __name__ == "__main__":
    unittest.main()
