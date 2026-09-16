from __future__ import annotations

from dataclasses import dataclass
import unittest

from local.audio_reactive_engine import (
    AudioReactiveInputSnapshot,
    AudioReactiveEngine,
    AudioReactiveRuntime,
    ReactiveFrame,
    audio_band_sample_plan,
    audio_band_sampling_request_plan,
    audio_reactive_style_plan,
    live_audio_availability_request_plan,
    reactive_audio_cache_hit,
)


@dataclass(frozen=True)
class BandFrame:
    low: float
    mid: float
    high: float


class AudioReactiveEngineTests(unittest.TestCase):
    def test_state_scales_bands_by_intensity(self):
        engine = AudioReactiveEngine(enabled=True, intensity_percent=50)

        engine.advance((0.8, 0.5, 0.2), now=10.0)
        state = engine.state()

        self.assertTrue(state["enabled"])
        self.assertAlmostEqual(state["low"], 0.4, delta=0.001)
        self.assertAlmostEqual(state["mid"], 0.25, delta=0.001)
        self.assertAlmostEqual(state["high"], 0.1, delta=0.001)
        self.assertGreater(state["energy"], 0.0)

    def test_read_frame_is_read_only_between_advances(self):
        engine = AudioReactiveEngine(enabled=True, intensity_percent=100)

        engine.advance((0.95, 0.72, 0.36), now=10.0)
        first = engine.read_frame()
        second = engine.read_frame()

        self.assertEqual(first, second)
        self.assertGreater(first.energy, 0.65)

    def test_advance_smooths_short_band_drops(self):
        engine = AudioReactiveEngine(enabled=True, intensity_percent=100)

        first = engine.advance((0.95, 0.72, 0.36), now=10.0)
        second = engine.advance((0.0, 0.0, 0.0), now=10.04)
        later = engine.advance((0.0, 0.0, 0.0), now=10.60)

        self.assertGreater(first.energy, 0.65)
        self.assertGreater(second.energy, 0.45)
        self.assertLess(later.energy, second.energy)

    def test_pulse_rises_on_onset_and_decays_without_new_energy(self):
        engine = AudioReactiveEngine(enabled=True, intensity_percent=100)

        engine.advance((0.2, 0.1, 0.05), now=10.0)
        pulse = engine.advance((0.95, 0.35, 0.12), now=10.05).pulse
        decayed = engine.advance((0.95, 0.35, 0.12), now=10.30).pulse

        self.assertGreater(pulse, 0.0)
        self.assertLess(decayed, pulse)

    def test_motion_drives_separate_beat_glow_and_lift_from_raw_bands(self):
        engine = AudioReactiveEngine(enabled=True, intensity_percent=100)

        engine.advance((0.16, 0.08, 0.03), now=10.0)
        hit = engine.advance((0.95, 0.38, 0.14), now=10.05)
        dropped = engine.advance((0.0, 0.0, 0.0), now=10.09)

        self.assertGreater(hit.beat, 0.30)
        self.assertGreater(hit.glow, 0.18)
        self.assertGreater(hit.lift, 0.14)
        self.assertLess(dropped.beat, hit.beat)
        self.assertGreater(dropped.beat, 0.20)
        self.assertGreater(dropped.glow, hit.glow * 0.82)
        self.assertGreater(dropped.lift, hit.lift * 0.78)

    def test_motion_drives_are_exported_in_runtime_state(self):
        engine = AudioReactiveEngine(enabled=True, intensity_percent=100)

        engine.advance((0.16, 0.08, 0.03), now=10.0)
        engine.advance((0.95, 0.38, 0.14), now=10.05)
        state = engine.state()

        self.assertIn("beat", state)
        self.assertIn("glow", state)
        self.assertIn("lift", state)
        self.assertGreater(state["beat"], 0.30)
        self.assertGreater(state["glow"], 0.18)

    def test_style_response_cache_is_cleared_only_on_advance(self):
        engine = AudioReactiveEngine(enabled=True, intensity_percent=100)
        engine.advance((0.82, 0.58, 0.34), now=10.0)

        first = engine.style_response("duotone_full")
        second = engine.style_response("duotone_full")
        engine.advance((0.90, 0.62, 0.38), now=10.08)
        third = engine.style_response("duotone_full")

        self.assertEqual(first, second)
        self.assertNotEqual(first["phase"], third["phase"])
        self.assertGreater(third["side_wave_height"], 0.0)

    def test_style_plan_gives_native_backgrounds_distinct_motion_families(self):
        frame = ReactiveFrame(
            enabled=True,
            intensity=1.0,
            low=0.82,
            mid=0.58,
            high=0.34,
            energy=0.70,
            bass=0.72,
            shimmer=0.50,
            phase=1.25,
            pulse=0.20,
            sequence=1,
        )

        sea = audio_reactive_style_plan(frame, "sea_level")
        duotone = audio_reactive_style_plan(frame, "duotone_full")
        bilateral = audio_reactive_style_plan(frame, "bilateral_diffraction")
        solid = audio_reactive_style_plan(frame, "pure_white")

        self.assertEqual(sea.motion_family, "layered_water")
        self.assertEqual(sea.primary_axis, "horizontal_surface")
        self.assertTrue(sea.preserves_lyric_center)
        self.assertGreater(sea.response["wave_height"], 1.0)
        self.assertGreater(sea.response["lower_alpha"], 1.0)
        self.assertEqual(duotone.motion_family, "full_duotone_interference")
        self.assertEqual(duotone.primary_axis, "lower_band")
        self.assertGreater(duotone.response["interference_flow"], 0.0)
        self.assertEqual(bilateral.motion_family, "side_frequency_waves")
        self.assertEqual(bilateral.primary_axis, "bilateral")
        self.assertGreater(bilateral.response["side_wave_height"], duotone.response["side_wave_height"])
        self.assertEqual(solid.motion_family, "solid_album_accent")
        self.assertEqual(solid.primary_axis, "edge_accent")
        self.assertGreater(solid.response["solid_accent_alpha"], 0.0)

    def test_style_response_keeps_legacy_numeric_shape_from_style_plan(self):
        engine = AudioReactiveEngine(enabled=True, intensity_percent=100)

        frame = engine.advance((0.82, 0.58, 0.34), now=10.0)
        response = engine.style_response("bilateral_diffraction")
        plan = audio_reactive_style_plan(frame, "bilateral_diffraction")

        self.assertEqual(response, plan.as_response())
        self.assertNotIn("motion_family", response)
        self.assertGreater(response["side_wave_alpha"], 0.0)

    def test_style_plan_consumes_semantic_motion_drives(self):
        frame = ReactiveFrame(
            enabled=True,
            intensity=1.0,
            low=0.05,
            mid=0.04,
            high=0.03,
            energy=0.05,
            bass=0.04,
            shimmer=0.03,
            phase=1.0,
            pulse=0.0,
            sequence=1,
            beat=0.62,
            glow=0.48,
            lift=0.36,
        )

        sea = audio_reactive_style_plan(frame, "sea_level").response
        duotone = audio_reactive_style_plan(frame, "duotone_full").response
        solid = audio_reactive_style_plan(frame, "pure_white").response

        self.assertAlmostEqual(sea["beat_drive"], 0.62, delta=0.001)
        self.assertAlmostEqual(sea["glow_drive"], 0.48, delta=0.001)
        self.assertAlmostEqual(sea["lift_drive"], 0.36, delta=0.001)
        self.assertGreater(sea["wave_height"], 1.16)
        self.assertGreater(duotone["side_wave_height"], 0.22)
        self.assertGreater(duotone["interference_alpha"], 1.10)
        self.assertGreater(solid["solid_accent_alpha"], 0.20)

    def test_wave_values_follow_elapsed_time_not_tick_count(self):
        short_frame = AudioReactiveEngine(enabled=False, intensity_percent=100)
        long_frame = AudioReactiveEngine(enabled=False, intensity_percent=100)

        short = short_frame.advance_wave_values([0.2] * 8, (0.9, 0.5, 0.8), 0.0, now=10.016, previous_at=10.0)
        long = long_frame.advance_wave_values([0.2] * 8, (0.9, 0.5, 0.8), 0.0, now=10.10, previous_at=10.0)

        self.assertLess(short.values[3], long.values[3])
        self.assertGreater(short.values[3], 0.2)

    def test_wave_values_use_smoothed_reactive_frame_when_enabled(self):
        engine = AudioReactiveEngine(enabled=True, intensity_percent=100)

        engine.advance_wave_values([0.2] * 8, (0.95, 0.72, 0.36), 0.0, now=10.0, previous_at=10.0 - 1.0 / 60.0)
        dropped = engine.advance_wave_values([0.2] * 8, (0.0, 0.0, 0.0), 0.0, now=10.04, previous_at=10.0)

        self.assertGreater(dropped.frame.energy, 0.45)
        self.assertGreater(max(dropped.targets), 0.20)

    def test_disabled_engine_returns_zero_frame_and_default_response(self):
        engine = AudioReactiveEngine(enabled=False, intensity_percent=100)

        frame = engine.advance((1.0, 1.0, 1.0), now=10.0)
        response = engine.style_response("sea_level")

        self.assertEqual(frame, ReactiveFrame())
        self.assertFalse(engine.state()["enabled"])
        self.assertEqual(response["energy"], 0.0)
        self.assertEqual(response["wave_height"], 1.0)

    def test_runtime_configures_engine_and_keeps_read_path_read_only(self):
        runtime = AudioReactiveRuntime(enabled=False, intensity_percent=100)

        self.assertFalse(runtime.state()["enabled"])
        runtime.configure(enabled=True, intensity_percent=50)
        cold = runtime.read_frame()
        advanced = runtime.advance_frame((0.8, 0.5, 0.2), now=10.0)
        read_back = runtime.read_frame()

        self.assertEqual(cold, ReactiveFrame())
        self.assertTrue(advanced.enabled)
        self.assertAlmostEqual(advanced.low, 0.4, delta=0.001)
        self.assertEqual(read_back, advanced)

    def test_runtime_advances_wave_values_through_configured_engine(self):
        runtime = AudioReactiveRuntime(enabled=True, intensity_percent=100)

        first = runtime.advance_wave_values(
            [0.2] * 8,
            (0.95, 0.72, 0.36),
            0.0,
            now=10.0,
            previous_at=10.0 - 1.0 / 60.0,
        )
        dropped = runtime.advance_wave_values(
            [0.2] * 8,
            (0.0, 0.0, 0.0),
            0.0,
            now=10.04,
            previous_at=10.0,
        )

        self.assertTrue(first.frame.enabled)
        self.assertGreater(dropped.frame.energy, 0.45)
        self.assertGreater(max(dropped.targets), 0.20)

    def test_runtime_style_response_uses_last_advanced_frame(self):
        runtime = AudioReactiveRuntime(enabled=True, intensity_percent=100)

        cold = runtime.style_response("sea_level")
        runtime.advance_frame((0.9, 0.65, 0.42), now=10.0)
        warm = runtime.style_response("sea_level")

        self.assertEqual(cold["wave_height"], 1.0)
        self.assertGreater(warm["wave_height"], 1.0)

    def test_runtime_snapshot_carries_frame_and_requested_style_responses(self):
        runtime = AudioReactiveRuntime(enabled=True, intensity_percent=100)

        advanced = runtime.advance_frame((0.9, 0.65, 0.42), now=10.0)
        snapshot = runtime.snapshot(("sea_level", "duotone_full", "sea_level"))

        self.assertEqual(snapshot.frame, advanced)
        self.assertEqual(snapshot.state(), advanced.as_dict())
        self.assertGreater(snapshot.style_response("sea_level")["wave_height"], 1.0)
        self.assertGreater(snapshot.style_response("duotone_full")["interference_alpha"], 1.0)
        self.assertEqual(snapshot.style_response("unknown"), {})

    def test_runtime_snapshot_cold_read_uses_default_style_response(self):
        runtime = AudioReactiveRuntime(enabled=True, intensity_percent=100)

        snapshot = runtime.snapshot(("sea_level",))

        self.assertEqual(snapshot.frame, ReactiveFrame())
        self.assertFalse(snapshot.state()["enabled"])
        self.assertEqual(snapshot.style_response("sea_level")["wave_height"], 1.0)
        self.assertEqual(snapshot.style_response("sea_level")["energy"], 0.0)

    def test_runtime_snapshot_style_responses_are_defensive_copies(self):
        runtime = AudioReactiveRuntime(enabled=True, intensity_percent=100)
        runtime.advance_frame((0.9, 0.65, 0.42), now=10.0)

        snapshot = runtime.snapshot(("sea_level",))
        stored = snapshot.style_response("sea_level")
        response = snapshot.style_response("sea_level")
        response["wave_height"] = 99.0

        self.assertNotEqual(snapshot.style_response("sea_level")["wave_height"], 99.0)
        self.assertEqual(snapshot.style_response("sea_level"), stored)

    def test_runtime_input_snapshot_owns_sampling_cache_and_live_availability(self):
        runtime = AudioReactiveRuntime(enabled=True, intensity_percent=100)
        frame_calls = 0
        probe_calls = 0

        def live_frame():
            nonlocal frame_calls
            frame_calls += 1
            return BandFrame(0.9, 0.5, 0.2)

        def live_probe():
            nonlocal probe_calls
            probe_calls += 1
            raise AssertionError("fresh live frame cache should avoid availability probe")

        first = runtime.input_snapshot(
            now=12.0,
            live_frame_reader=live_frame,
            live_audio_probe=live_probe,
            media_is_playing=True,
        )
        second = runtime.input_snapshot(
            now=12.10,
            live_frame_reader=live_frame,
            live_audio_probe=live_probe,
            media_is_playing=True,
        )

        self.assertIsInstance(first, AudioReactiveInputSnapshot)
        self.assertEqual(first.sampled_bands, (0.9, 0.5, 0.2))
        self.assertTrue(first.live_audio_available)
        self.assertEqual(first.band_source, "live_frame")
        self.assertEqual(first.live_audio_source, "cache")
        self.assertEqual(second.sampled_bands, (0.9, 0.5, 0.2))
        self.assertEqual(second.band_source, "cache")
        self.assertEqual(frame_calls, 1)
        self.assertEqual(probe_calls, 0)

    def test_runtime_input_diagnostics_reports_empty_read_only_state_before_sampling(self):
        runtime = AudioReactiveRuntime(enabled=True, intensity_percent=50)

        diagnostics = runtime.input_diagnostics(now=12.0)

        self.assertTrue(diagnostics["reactive_enabled"])
        self.assertAlmostEqual(diagnostics["intensity"], 0.5, delta=0.001)
        self.assertFalse(diagnostics["has_snapshot"])
        self.assertFalse(diagnostics["cache_has_bands"])
        self.assertEqual(diagnostics["sampled_bands"], (0.0, 0.0, 0.0))
        self.assertEqual(diagnostics["band_source"], "")
        self.assertIsNone(diagnostics["sample_age_ms"])
        self.assertIsNone(diagnostics["live_audio_check_age_ms"])

    def test_runtime_input_diagnostics_reports_live_frame_cache_source(self):
        runtime = AudioReactiveRuntime(enabled=True, intensity_percent=100)

        runtime.input_snapshot(
            now=12.0,
            live_frame_reader=lambda: BandFrame(0.9, 0.5, 0.2),
            live_audio_probe=lambda: (_ for _ in ()).throw(AssertionError("live frame should seed live cache")),
            media_is_playing=True,
        )
        runtime.input_snapshot(
            now=12.10,
            live_frame_reader=lambda: (_ for _ in ()).throw(AssertionError("recent band cache should be reused")),
            live_audio_probe=lambda: (_ for _ in ()).throw(AssertionError("recent live cache should be reused")),
            media_is_playing=True,
        )
        diagnostics = runtime.input_diagnostics(now=12.16)

        self.assertTrue(diagnostics["has_snapshot"])
        self.assertTrue(diagnostics["cache_has_bands"])
        self.assertEqual(diagnostics["sampled_bands"], (0.9, 0.5, 0.2))
        self.assertEqual(diagnostics["band_source"], "cache")
        self.assertTrue(diagnostics["live_audio_available"])
        self.assertEqual(diagnostics["live_audio_source"], "cache")
        self.assertAlmostEqual(diagnostics["sampled_at"], 12.0, delta=0.001)
        self.assertAlmostEqual(diagnostics["sample_age_ms"], 160.0, delta=0.001)
        self.assertAlmostEqual(diagnostics["live_audio_check_age_ms"], 160.0, delta=0.001)

    def test_runtime_input_snapshot_probes_live_audio_when_reactive_sampling_is_disabled(self):
        runtime = AudioReactiveRuntime(enabled=False, intensity_percent=100)
        probe_calls = 0

        def live_frame():
            raise AssertionError("disabled reactive sampling should not request live frame")

        def live_probe():
            nonlocal probe_calls
            probe_calls += 1
            return True

        first = runtime.input_snapshot(
            now=12.0,
            live_frame_reader=live_frame,
            live_audio_probe=live_probe,
            media_is_playing=True,
            ambient_phase=0.25,
        )
        second = runtime.input_snapshot(
            now=12.10,
            live_frame_reader=live_frame,
            live_audio_probe=live_probe,
            media_is_playing=True,
            ambient_phase=0.25,
        )

        self.assertEqual(first.band_source, "playing_fallback")
        self.assertTrue(first.live_audio_available)
        self.assertEqual(first.live_audio_source, "probe")
        self.assertTrue(second.live_audio_available)
        self.assertEqual(second.live_audio_source, "cache")
        self.assertEqual(probe_calls, 1)

    def test_runtime_input_diagnostics_reports_disabled_fallback_and_probe(self):
        runtime = AudioReactiveRuntime(enabled=False, intensity_percent=100)

        runtime.input_snapshot(
            now=12.0,
            live_frame_reader=lambda: (_ for _ in ()).throw(AssertionError("disabled sampling should skip live frame")),
            live_audio_probe=lambda: True,
            media_is_playing=True,
        )
        diagnostics = runtime.input_diagnostics(now=12.10)

        self.assertFalse(diagnostics["reactive_enabled"])
        self.assertFalse(diagnostics["cache_has_bands"])
        self.assertEqual(diagnostics["sampled_bands"], (0.10, 0.08, 0.05))
        self.assertEqual(diagnostics["band_source"], "playing_fallback")
        self.assertTrue(diagnostics["live_audio_available"])
        self.assertEqual(diagnostics["live_audio_source"], "probe")
        self.assertIsNone(diagnostics["sample_age_ms"])
        self.assertAlmostEqual(diagnostics["live_audio_check_age_ms"], 100.0, delta=0.001)

    def test_reactive_audio_cache_hit_reuses_recent_bands_only(self):
        recent = reactive_audio_cache_hit(
            now=10.10,
            reactive_enabled=True,
            cached_bands=(1.2, 0.5, -0.2),
            sampled_at=10.0,
        )
        stale = reactive_audio_cache_hit(
            now=10.30,
            reactive_enabled=True,
            cached_bands=(0.8, 0.5, 0.2),
            sampled_at=10.0,
        )
        disabled = reactive_audio_cache_hit(
            now=10.10,
            reactive_enabled=False,
            cached_bands=(0.8, 0.5, 0.2),
            sampled_at=10.0,
        )

        self.assertEqual(recent, (1.0, 0.5, 0.0))
        self.assertIsNone(stale)
        self.assertIsNone(disabled)

    def test_audio_band_sampling_request_plan_reuses_cache_without_provider_probe(self):
        plan = audio_band_sampling_request_plan(
            now=10.10,
            reactive_enabled=True,
            cached_bands=(0.8, 0.5, 0.2),
            sampled_at=10.0,
        )

        self.assertEqual(plan.cached_bands, (0.8, 0.5, 0.2))
        self.assertFalse(plan.request_live_frame)
        self.assertTrue(plan.reactive_enabled)
        self.assertEqual(plan.source, "cache")

    def test_audio_band_sampling_request_plan_requests_live_frame_only_when_enabled(self):
        disabled = audio_band_sampling_request_plan(
            now=10.30,
            reactive_enabled=False,
            cached_bands=(0.8, 0.5, 0.2),
            sampled_at=10.0,
        )
        stale = audio_band_sampling_request_plan(
            now=10.30,
            reactive_enabled=True,
            cached_bands=(0.8, 0.5, 0.2),
            sampled_at=10.0,
        )

        self.assertIsNone(disabled.cached_bands)
        self.assertFalse(disabled.request_live_frame)
        self.assertFalse(disabled.reactive_enabled)
        self.assertIsNone(stale.cached_bands)
        self.assertTrue(stale.request_live_frame)
        self.assertTrue(stale.reactive_enabled)

    def test_live_audio_availability_request_plan_reuses_recent_cache(self):
        plan = live_audio_availability_request_plan(
            now=10.20,
            cached_available=True,
            checked_at=10.0,
        )

        self.assertTrue(plan.cached_available)
        self.assertFalse(plan.request_probe)
        self.assertEqual(plan.source, "cache")

    def test_live_audio_availability_request_plan_requests_probe_when_stale_or_invalid(self):
        stale = live_audio_availability_request_plan(
            now=10.60,
            cached_available=True,
            checked_at=10.0,
        )
        invalid = live_audio_availability_request_plan(
            now=10.0,
            cached_available=True,
            checked_at="bad",
        )

        self.assertTrue(stale.cached_available)
        self.assertTrue(stale.request_probe)
        self.assertEqual(stale.source, "probe")
        self.assertFalse(invalid.cached_available)
        self.assertTrue(invalid.request_probe)

    def test_audio_band_sample_plan_uses_live_frame_and_marks_live_cache(self):
        plan = audio_band_sample_plan(
            now=12.0,
            reactive_enabled=True,
            live_frame=BandFrame(1.2, 0.4, -0.5),
            audio_frames=[],
            audio_started_at=0.0,
            media_is_playing=True,
            ambient_phase=0.0,
        )

        self.assertEqual(plan.bands, (1.0, 0.4, 0.0))
        self.assertEqual(plan.cache_bands, plan.bands)
        self.assertEqual(plan.cache_sampled_at, 12.0)
        self.assertTrue(plan.live_audio_available)
        self.assertEqual(plan.live_audio_checked_at, 12.0)

    def test_audio_band_sample_plan_reactive_fallback_updates_cache_without_live_probe_loop(self):
        plan = audio_band_sample_plan(
            now=12.0,
            reactive_enabled=True,
            live_frame=None,
            audio_frames=[],
            audio_started_at=0.0,
            media_is_playing=True,
            ambient_phase=0.0,
        )

        self.assertEqual(plan.bands, (0.10, 0.08, 0.05))
        self.assertEqual(plan.cache_bands, plan.bands)
        self.assertEqual(plan.cache_sampled_at, 12.0)
        self.assertFalse(plan.live_audio_available)
        self.assertEqual(plan.live_audio_checked_at, 12.0)

    def test_audio_band_sample_plan_interpolates_sequence_without_reactive_cache(self):
        frames = [BandFrame(0.0, 0.2, 0.4), BandFrame(1.0, 0.8, 0.2)]

        plan = audio_band_sample_plan(
            now=10.5,
            reactive_enabled=True,
            live_frame=None,
            audio_frames=frames,
            audio_started_at=10.0,
            media_is_playing=True,
            ambient_phase=0.0,
        )

        self.assertEqual(plan.source, "sequence")
        self.assertGreater(plan.bands[0], 0.0)
        self.assertLess(plan.bands[0], 1.0)
        self.assertIsNone(plan.cache_bands)
        self.assertFalse(plan.live_audio_available)

    def test_audio_band_sample_plan_keeps_synthetic_live_path_pure(self):
        plan = audio_band_sample_plan(
            now=12.0,
            reactive_enabled=False,
            live_frame=None,
            audio_frames=[],
            audio_started_at=0.0,
            media_is_playing=True,
            ambient_phase=1.25,
            live_audio_available=True,
        )

        self.assertEqual(plan.source, "synthetic_live")
        self.assertTrue(all(0.0 <= band <= 1.0 for band in plan.bands))
        self.assertIsNone(plan.cache_bands)


if __name__ == "__main__":
    unittest.main()
