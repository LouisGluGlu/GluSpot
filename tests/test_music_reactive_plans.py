from __future__ import annotations

import unittest

from local.music_reactive_plans import (
    background_motion_plan,
    bilateral_motion_plan,
    bilateral_waveform_paint_plan,
    duotone_interference_paint_plan,
    duotone_motion_plan,
    duotone_side_wave_paint_plan,
    duotone_split_band_paint_plan,
    reactive_motion_drives,
    sea_level_motion_plan,
    sea_level_wave_paint_plan,
    solid_accent_motion_plan,
)


class MusicReactivePlanTests(unittest.TestCase):
    def test_motion_drives_prefer_semantic_values_over_raw_pulse_fallback(self):
        drives = reactive_motion_drives(
            {
                "pulse": 0.10,
                "energy": 0.20,
                "low": 0.30,
                "beat_drive": 0.82,
                "glow_drive": 0.74,
                "lift_drive": 0.66,
            }
        )

        self.assertAlmostEqual(drives.beat, 0.82)
        self.assertAlmostEqual(drives.glow, 0.74)
        self.assertAlmostEqual(drives.lift, 0.66)

    def test_motion_drives_keep_legacy_response_usable_when_semantic_keys_are_missing(self):
        drives = reactive_motion_drives({"pulse": 0.42, "surface_glow": 0.36, "energy": 0.90, "low": 0.70})

        self.assertAlmostEqual(drives.beat, 0.42)
        self.assertAlmostEqual(drives.glow, 0.495)
        self.assertAlmostEqual(drives.lift, 0.35)

    def test_sea_level_plan_turns_semantic_drives_into_water_motion_not_template_state(self):
        neutral = sea_level_motion_plan({})
        driven = sea_level_motion_plan({"beat_drive": 0.90, "glow_drive": 0.80, "lift_drive": 0.70})

        self.assertGreater(driven.phase_rate, neutral.phase_rate)
        self.assertGreater(driven.glow_height_scale, neutral.glow_height_scale)
        self.assertGreater(driven.amplitude_scale, neutral.amplitude_scale)
        self.assertGreater(driven.thickness_scale, neutral.thickness_scale)

    def test_sea_level_wave_paint_plan_owns_lower_water_geometry(self):
        quiet = sea_level_wave_paint_plan({}, bands=(0.05, 0.05, 0.05), flow=0.40, expanded=False)
        driven = sea_level_wave_paint_plan(
            {"wave_height": 1.46, "wave_speed": 1.30, "beat_drive": 0.86, "glow_drive": 0.78, "lift_drive": 0.72},
            bands=(0.72, 0.60, 0.36),
            flow=0.40,
            expanded=False,
        )
        paused = sea_level_wave_paint_plan(
            {"wave_height": 1.46, "wave_speed": 1.30, "beat_drive": 0.86, "glow_drive": 0.78, "lift_drive": 0.72},
            bands=(0.72, 0.60, 0.36),
            flow=0.40,
            expanded=False,
            media_is_playing=False,
        )

        self.assertAlmostEqual(quiet.center_y_ratio, 0.60, delta=0.001)
        self.assertGreater(driven.phase_time_rate, quiet.phase_time_rate)
        self.assertGreater(driven.amplitude_units, quiet.amplitude_units)
        self.assertGreater(driven.thickness_units, quiet.thickness_units)
        self.assertLess(paused.flow, driven.flow)
        self.assertLess(paused.amplitude_units, driven.amplitude_units)

    def test_duotone_plan_keeps_side_motion_and_interference_as_explicit_outputs(self):
        neutral = duotone_motion_plan({}, base_flow=0.20)
        driven = duotone_motion_plan({"beat_drive": 0.86, "glow_drive": 0.80, "lift_drive": 0.64}, base_flow=0.20)

        self.assertGreater(driven.side_flow, neutral.side_flow)
        self.assertGreater(driven.side_alpha_scale, neutral.side_alpha_scale)
        self.assertGreater(driven.side_amplitude_scale, neutral.side_amplitude_scale)
        self.assertGreater(driven.interference_alpha_scale, neutral.interference_alpha_scale)

    def test_duotone_split_band_paint_plan_owns_vertical_layer_geometry(self):
        neutral = duotone_split_band_paint_plan({}, transition_height_scale=1.0)
        soft_shifted = duotone_split_band_paint_plan(
            {"transition_shift": 0.04, "transition_softness": 0.10},
            transition_height_scale=1.22,
        )

        self.assertGreater(neutral.bridge_in_stop, neutral.upper_solid_stop)
        self.assertGreater(neutral.bridge_out_stop, neutral.bridge_in_stop)
        self.assertGreater(neutral.lower_solid_stop, neutral.bridge_out_stop)
        self.assertGreater(soft_shifted.transition_height_ratio, neutral.transition_height_ratio)
        self.assertGreater(soft_shifted.transition_top_ratio, neutral.transition_top_ratio - 0.01)

    def test_duotone_side_wave_paint_plan_owns_edge_wave_geometry(self):
        quiet = duotone_side_wave_paint_plan({}, flow=0.0, expanded=False, intensity=1.0, opacity_boost=1.0)
        compact = duotone_side_wave_paint_plan(
            {"side_wave_alpha": 0.54, "side_wave_height": 0.72, "side_wave_width": 1.18},
            flow=0.42,
            expanded=False,
            intensity=0.9,
            opacity_boost=1.1,
            alpha_scale=1.2,
            amplitude_scale=1.3,
        )
        expanded = duotone_side_wave_paint_plan(
            {"side_wave_alpha": 0.54, "side_wave_height": 0.72, "side_wave_width": 1.18},
            flow=0.42,
            expanded=True,
            intensity=0.9,
            opacity_boost=1.1,
            alpha_scale=1.2,
            amplitude_scale=1.3,
        )

        self.assertFalse(quiet.enabled)
        self.assertTrue(compact.enabled)
        self.assertEqual(compact.point_count, 26)
        self.assertEqual(expanded.point_count, 34)
        self.assertGreater(compact.alpha, 2)
        self.assertGreater(compact.phase_time_rate, 0.20)
        self.assertAlmostEqual(compact.y_top_ratio, 0.28)
        self.assertAlmostEqual(expanded.y_top_ratio, 0.24)
        self.assertGreater(compact.amplitude_ratio, 0.022)
        self.assertGreater(compact.halo_width_units, compact.line_width_units)

    def test_duotone_interference_paint_plan_owns_blob_motion_geometry(self):
        quiet = duotone_interference_paint_plan(flow=0.0, now=12.0, bands=(0.40, 0.50, 0.60))
        driven = duotone_interference_paint_plan(
            flow=0.44,
            now=12.0,
            bands=(0.40, 0.50, 0.60),
            intensity=0.80,
            opacity_boost=1.10,
        )
        stronger = duotone_interference_paint_plan(
            flow=0.44,
            now=12.0,
            bands=(0.40, 0.50, 0.60),
            intensity=1.0,
            opacity_boost=1.25,
        )

        self.assertFalse(quiet.enabled)
        self.assertEqual(quiet.blobs, ())
        self.assertTrue(driven.enabled)
        self.assertEqual([blob.role for blob in driven.blobs], ["accent", "main", "mix", "highlight"])
        self.assertTrue(all(blob.alpha > 0 for blob in driven.blobs))
        self.assertGreater(stronger.blobs[0].alpha, driven.blobs[0].alpha)
        self.assertGreater(driven.blobs[0].radius_x_ratio, driven.blobs[3].radius_x_ratio)
        self.assertGreater(driven.blobs[2].bridge_mix_ratio, 0.0)
        self.assertGreater(driven.blobs[3].bridge_mix_ratio, driven.blobs[2].bridge_mix_ratio)

    def test_bilateral_plan_keeps_frequency_waveform_controls_separate(self):
        neutral = bilateral_motion_plan({}, base_flow=0.38, frequency=0.50)
        driven = bilateral_motion_plan({"beat_drive": 0.90, "glow_drive": 0.72, "lift_drive": 0.68}, base_flow=0.38, frequency=0.50)

        self.assertGreater(driven.flow, neutral.flow)
        self.assertGreater(driven.ring_speed_scale, neutral.ring_speed_scale)
        self.assertGreater(driven.ring_alpha_scale, neutral.ring_alpha_scale)
        self.assertGreater(driven.waveform_alpha_scale, neutral.waveform_alpha_scale)
        self.assertGreater(driven.waveform_amplitude_scale, neutral.waveform_amplitude_scale)

    def test_bilateral_waveform_paint_plan_owns_frequency_waveform_geometry(self):
        quiet = bilateral_waveform_paint_plan({}, frequency=0.0, expanded=False)
        compact = bilateral_waveform_paint_plan(
            {"side_wave_alpha": 0.52, "side_wave_height": 0.64, "mid": 0.50, "high": 0.72},
            frequency=0.62,
            expanded=False,
            intensity=0.82,
            opacity_boost=1.10,
            alpha_scale=1.24,
            amplitude_scale=1.30,
        )
        expanded = bilateral_waveform_paint_plan(
            {"side_wave_alpha": 0.52, "side_wave_height": 0.64, "mid": 0.50, "high": 0.72},
            frequency=0.62,
            expanded=True,
            intensity=0.82,
            opacity_boost=1.10,
            alpha_scale=1.24,
            amplitude_scale=1.30,
        )

        self.assertFalse(quiet.enabled)
        self.assertTrue(compact.enabled)
        self.assertGreater(compact.point_count, 30)
        self.assertGreater(compact.alpha, 2)
        self.assertGreater(compact.amplitude_ratio, 0.026)
        self.assertGreater(compact.carrier_cycles, 1.8)
        self.assertGreater(compact.overtone_cycles, 4.2)
        self.assertAlmostEqual(compact.y_top_ratio, 0.21)
        self.assertAlmostEqual(expanded.y_top_ratio, 0.17)
        self.assertGreater(compact.halo_width_units, compact.line_width_units)

    def test_solid_accent_plan_keeps_pure_surfaces_as_edge_and_lower_motion(self):
        quiet = solid_accent_motion_plan({}, light_surface=True)
        dark = solid_accent_motion_plan(
            {"solid_accent_alpha": 0.62, "beat_drive": 0.80, "glow_drive": 0.70, "lift_drive": 0.50},
            light_surface=False,
        )
        light = solid_accent_motion_plan(
            {"solid_accent_alpha": 0.62, "beat_drive": 0.80, "glow_drive": 0.70, "lift_drive": 0.50},
            light_surface=True,
        )

        self.assertEqual(quiet.alpha_amount, 0.0)
        self.assertAlmostEqual(dark.alpha_amount, 0.62)
        self.assertGreater(dark.edge_alpha_scale, light.edge_alpha_scale)
        self.assertGreater(dark.wave_alpha_scale, light.wave_alpha_scale)
        self.assertGreater(light.center_protection, dark.center_protection)
        self.assertGreater(light.wave_amplitude_scale, quiet.wave_amplitude_scale)

    def test_background_motion_plan_selects_style_native_motion_family(self):
        sea = background_motion_plan("sea_level", {"beat_drive": 0.90})
        duotone = background_motion_plan("duotone_full", {"glow_drive": 0.80}, base_flow=0.24)
        bilateral = background_motion_plan(
            "bilateral_diffraction",
            {"beat_drive": 0.90, "glow_drive": 0.78},
            base_flow=0.36,
            frequency=0.60,
        )
        solid = background_motion_plan("pure_white", {"solid_accent_alpha": 0.50}, light_surface=True)
        unknown = background_motion_plan("unknown", {})

        self.assertEqual(sea.motion_family, "layered_water")
        self.assertEqual(sea.primary_axis, "lower_water_band")
        self.assertIsNotNone(sea.sea_level)
        self.assertIsNone(sea.duotone)
        self.assertEqual(duotone.motion_family, "full_duotone_side_interference")
        self.assertIsNotNone(duotone.duotone)
        self.assertEqual(bilateral.primary_axis, "left_right_edges")
        self.assertIsNotNone(bilateral.bilateral)
        assert bilateral.bilateral is not None
        self.assertGreater(bilateral.bilateral.waveform_amplitude_scale, bilateral.bilateral.center_glow_scale)
        self.assertEqual(solid.motion_family, "solid_edge_accent")
        self.assertIsNotNone(solid.solid)
        self.assertEqual(unknown.motion_family, "none")
        self.assertTrue(all(plan.preserves_lyric_center for plan in (sea, duotone, bilateral, solid, unknown)))


if __name__ == "__main__":
    unittest.main()
