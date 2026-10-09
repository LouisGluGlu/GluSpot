from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import inspect
import json
import math
import re
import tempfile
import time
import unittest
from unittest.mock import patch

from api.models import AlbumQueueEntry, AudioFrame, ColorToken, IslandVisualData, MessageData, TimedLyricLine, TimedLyricUnit, TrackData
from api.providers import EmptyIslandDataProvider, empty_visual_data
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPixmap
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtWidgets import QApplication

from local.island_stack_visual import BarrageOverlay, FullscreenPluginAlertOverlay, IslandKind, IslandStackVisual, LyricLayout, Message, ReactiveFrame, SpringRect, StackVisual, TaskbarDropOverlay
from local.island_presentation import (
    ActiveVisualAnimationPlan,
    BarrageHoverClockPlan,
    LyricFallbackAdvancePlan,
    MusicLyricTickPlan,
    TickFollowRefreshPlan,
    TickInteractionPlan,
    TickRenderPlan,
    music_wave_input_snapshot as presentation_music_wave_input_snapshot,
    scene_suppressed_tick_plan as presentation_scene_suppressed_tick_plan,
)
from local.audio_reactive_engine import ReactiveSnapshot
from local.mydockfinder import FinderGlassMaterial
from local.music_surface_window import MusicSurfaceWindow


class IslandStackVisualMessageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _png_bytes(self, color: QColor) -> bytes:
        image = QImage(8, 8, QImage.Format.Format_ARGB32)
        image.fill(color)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        return bytes(data)

    def _near_black_placeholder_png_bytes(self) -> bytes:
        image = QImage(16, 16, QImage.Format.Format_ARGB32)
        for y in range(image.height()):
            for x in range(image.width()):
                luma = 10 + ((x * 7 + y * 5) % 18)
                image.setPixelColor(x, y, QColor(luma, luma + (x % 3), luma + (y % 2), 255))
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        return bytes(data)

    def _music_reactive_response(self, **overrides: float) -> dict[str, float]:
        response = {
            "energy": 0.0,
            "low": 0.0,
            "mid": 0.0,
            "high": 0.0,
            "bass": 0.0,
            "shimmer": 0.0,
            "lower_alpha": 1.0,
            "lower_scale": 1.0,
            "wave_height": 1.0,
            "wave_speed": 1.0,
            "color_boost": 0.0,
            "split_shift": 0.0,
            "top_guard": 0.0,
            "transition_shift": 0.0,
            "transition_softness": 0.050,
            "interference_flow": 0.0,
            "interference_alpha": 1.0,
            "side_wave_alpha": 0.0,
            "side_wave_height": 0.0,
            "side_wave_width": 1.0,
            "solid_accent_alpha": 0.0,
            "solid_accent_lightness": 0.0,
            "surface_glow": 0.0,
            "phase": 0.0,
            "pulse": 0.0,
        }
        response.update(overrides)
        return response

    def _reactive_music_background_visual(
        self,
        ambience_style: str,
        *,
        response: dict[str, float],
        bands: tuple[float, float, float],
    ) -> IslandStackVisual:
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = ambience_style
        # Legacy fixture names describe the rendered neutral tone; the
        # product stores only system and tests must not depend on host theme.
        if ambience_style in {"pure_black", "pure_white"}:
            visual._music_system_dark = ambience_style == "pure_black"
        visual.music_expanded_layout_style = "standard"
        visual.album_color = QColor(28, 130, 220)
        visual.secondary_color = QColor(230, 70, 120)
        visual.current_audio_bands = bands
        visual.media_is_playing = True
        visual.ambient_phase = 0.6
        visual.wave_values = [0.18 + 0.64 * abs(math.sin(index * 0.47)) for index in range(36)]
        visual.ambient_intensity = 0.55
        visual.music_flow_intensity = 0.75
        visual.diffraction_frequency = 0.70
        visual.expanded_duotone_balance = 50
        visual.compact_duotone_balance = 50
        visual.music_surface_transition_started_at = 0.0
        visual.music_surface_transition_duration = 0.62
        visual.music_surface_transition_mode = ""
        visual.solid_album_background_fallback = "#16BCFF"
        visual._taskbar_compact_active = lambda: False
        visual._edge_notch_active = lambda: False
        visual._active_lyric_color_fill_paints_background = lambda _style=None: False
        visual._paint_music_compact = lambda *_args, **_kwargs: None
        visual._paint_music_expanded = lambda *_args, **_kwargs: None
        visual._paint_music_border = lambda *_args, **_kwargs: None
        visual._paint_music_surface_transition_wash = lambda *_args, **_kwargs: None
        visual._music_reactive_style_response = lambda _style: dict(response)
        return visual

    def _paint_music_background_image(self, visual: IslandStackVisual, *, expanded: bool = False) -> QImage:
        image = QImage(320, 100, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        with patch("local.music_background_renderer.time.monotonic", return_value=42.0):
            visual._paint_music(painter, QRectF(20.0, 20.0, 260.0, 60.0), expanded)
        painter.end()
        return image

    def _image_region_difference(self, left: QImage, right: QImage, region: QRect) -> int:
        diff = 0
        for y in range(region.top(), region.bottom() + 1, 3):
            for x in range(region.left(), region.right() + 1, 3):
                a = left.pixelColor(x, y)
                b = right.pixelColor(x, y)
                diff += abs(a.red() - b.red())
                diff += abs(a.green() - b.green())
                diff += abs(a.blue() - b.blue())
                diff += abs(a.alpha() - b.alpha())
        return diff

    def _count_dark_pixels(self, image: QImage) -> int:
        dark = 0
        for y in range(0, image.height(), 4):
            for x in range(0, image.width(), 4):
                color = image.pixelColor(x, y)
                luma = color.red() * 0.2126 + color.green() * 0.7152 + color.blue() * 0.0722
                if color.alpha() > 24 and luma < 85:
                    dark += 1
        return dark

    def test_barrage_background_style_adapter_samples_only_when_plan_requests_it(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.barrage_dark_background = False
        visual.barrage_style_checked_at = 9.0
        visual.drag_candidate = False
        visual.last_pointer_press_at = 0.0
        samples: list[QRectF] = []

        def sample(rect: QRectF) -> float:
            samples.append(QRectF(rect))
            return 0.40

        visual._sample_background_luma = sample

        with patch("local.island_stack_visual.time.monotonic", return_value=10.0):
            self.assertTrue(visual._barrage_background_is_dark(QRectF(1.0, 2.0, 30.0, 12.0)))

        self.assertEqual(samples, [QRectF(1.0, 2.0, 30.0, 12.0)])
        self.assertTrue(visual.barrage_dark_background)
        self.assertEqual(visual.barrage_style_checked_at, 10.0)

        visual.drag_candidate = True
        visual._sample_background_luma = lambda _rect: self.fail("sampling should stay gated while dragging")
        with patch("local.island_stack_visual.time.monotonic", return_value=11.0):
            self.assertTrue(visual._barrage_background_is_dark(QRectF(1.0, 2.0, 30.0, 12.0)))

    def _prime_tick_interaction_visual(self, visual: IslandStackVisual):
        visual.last_tick_at = 10.0
        visual.animation_speed = 1.0
        visual.media_is_playing = False
        visual.timed_lyrics = []
        visual.lyrics = []
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.lyric_changed_at = 10.0
        visual.scene_suppressed = False
        visual.pointer_pos = QPointF(-1.0, -1.0)
        visual.barrage_clock = 0.0
        visual.super_topmost_enabled = False
        visual.taskbar_above_follow_active = False
        visual.drag_candidate = False
        visual.hovered_key = "plugin"
        visual.hover_armed_key = "plugin"
        visual.hover_started_at = 10.0
        visual.expanded = True
        visual.plugin_panel_open = False
        visual.left_stack_at = 10.0
        visual.scroll_phase = 0.0
        visual.ambient_phase = 0.0
        visual._sync_music_background_to_theme = lambda: False
        visual._refresh_media_data = lambda _now: False
        visual._update_scene_state = lambda _now: None
        visual._prune_expired_messages = lambda _now: None
        visual._taskbar_compact_active = lambda: False
        visual._ensure_fullscreen_plugin_barrage_messages = lambda _now: None
        visual._sync_fullscreen_plugin_alert_overlay = lambda _now: None
        visual._point_in_barrage = lambda _point: False
        visual._active_visual_animation_plan = lambda _now: ActiveVisualAnimationPlan(active=False)
        visual._has_active_visual_animation = lambda _now: False
        visual._retune_frame_timer = lambda _active: None
        visual._tick_render_plan = lambda *_args, **_kwargs: TickRenderPlan(should_render=False)

    def test_animation_duration_scales_with_global_speed(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.animation_speed = 0.5
        self.assertAlmostEqual(visual._animation_duration(0.24), 0.48, delta=0.001)

        visual.animation_speed = 2.0
        self.assertAlmostEqual(visual._animation_duration(0.24), 0.12, delta=0.001)

    def test_render_frame_due_delegates_interval_rule_without_changing_adapter_fps(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.last_render_update_at = 10.0
        visual.animation_fps = 60
        visual.lyric_animation_fps = 120
        visual.animation_speed = 1.0
        visual.media_is_playing = False
        visual.timed_lyrics = []
        visual.lyrics = []
        visual.prev_lyric_index = 0
        visual.lyric_index = 0
        visual.lyric_changed_at = 0.0
        visual.FRAME_INTERVAL_TOLERANCE_SECONDS = 0.002

        self.assertFalse(visual._render_frame_due(10.010, active_animation=False))
        self.assertTrue(visual._render_frame_due(10.015, active_animation=False))

    def test_mark_render_frame_records_visual_frame_diagnostics(self):
        class Timer:
            def interval(self):
                return 16

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.timer = Timer()

        visual._mark_render_frame(10.000)
        visual._mark_render_frame(10.050, reason="lyric")
        summary = visual._visual_frame_diagnostics_summary()

        self.assertEqual(visual.last_render_update_at, 10.050)
        self.assertEqual(summary["frame_count"], 2)
        self.assertEqual(summary["sample_count"], 1)
        self.assertAlmostEqual(summary["last_interval_ms"], 50.0, delta=0.001)
        self.assertEqual(summary["long_frame_count"], 1)
        self.assertEqual(summary["last_reason"], "lyric")
        self.assertAlmostEqual(summary["target_interval_ms"], 16.0, delta=0.001)

    def test_provider_diagnostics_exports_visual_frame_summary(self):
        class Provider:
            def diagnostics(self):
                return {"provider": "ok"}

        class Timer:
            def interval(self):
                return 20

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.data_provider = Provider()
        visual.timer = Timer()
        visual._mark_render_frame(20.0)
        visual._mark_render_frame(20.02)

        diagnostics = visual.provider_diagnostics()

        self.assertEqual(diagnostics["provider"], "ok")
        self.assertIn("visual_frame_diagnostics", diagnostics)
        frame = diagnostics["visual_frame_diagnostics"]
        self.assertEqual(frame["frame_count"], 2)
        self.assertAlmostEqual(frame["average_interval_ms"], 20.0, delta=0.001)
        self.assertAlmostEqual(frame["average_fps"], 50.0, delta=0.001)

    def test_provider_diagnostics_exports_visual_paint_section_summary(self):
        class Provider:
            def diagnostics(self):
                return {"provider": "ok"}

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.data_provider = Provider()
        visual._record_visual_paint_section("music.total", 30.000, 30.006)
        visual._record_visual_paint_section("music.compact_content", 31.000, 31.003)

        diagnostics = visual.provider_diagnostics()

        self.assertIn("visual_paint_section_diagnostics", diagnostics)
        sections = diagnostics["visual_paint_section_diagnostics"]
        self.assertEqual(sections["section_count"], 2)
        self.assertEqual(sections["last_section"], "music.compact_content")
        self.assertAlmostEqual(sections["sections"]["music.total"]["last_ms"], 6.0, delta=0.001)
        self.assertAlmostEqual(sections["sections"]["music.compact_content"]["last_ms"], 3.0, delta=0.001)
        self.assertIn("groups", sections)
        self.assertAlmostEqual(sections["groups"]["total"]["last_ms"], 6.0, delta=0.001)
        self.assertAlmostEqual(sections["groups"]["content"]["last_ms"], 3.0, delta=0.001)

    def test_render_frame_records_throttled_runtime_diagnostics_trace(self):
        class Timer:
            def interval(self):
                return 16

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.timer = Timer()
        visual._lyric_timeline_diagnostics = lambda _now: {
            "sync_lag_lines": 0,
            "visible_lyric_index": 1,
            "position_active_index": 1,
            "phase_source": "timed",
        }
        visual._music_reactive_input_diagnostics = lambda _now: {
            "reactive_enabled": True,
            "live_audio_available": True,
            "band_source": "cache",
            "sampled_bands": (0.3, 0.2, 0.1),
        }
        visual._record_visual_paint_section("music.reactive_overlay", 9.990, 10.004)

        visual._mark_render_frame(10.0)
        visual._mark_render_frame(10.2)
        visual._mark_render_frame(11.2)

        trace = visual._runtime_diagnostics_trace_summary()

        self.assertEqual(trace["recorded_count"], 2)
        self.assertEqual(trace["sample_count"], 2)
        self.assertEqual(trace["dominant_area"], "reactive_effects")
        self.assertEqual(trace["bottleneck_group"], "reactive_effects")
        self.assertEqual(trace["warning_count"], 2)

    def test_provider_diagnostics_exports_runtime_diagnostics_trace(self):
        class Provider:
            def diagnostics(self):
                return {"provider": "ok"}

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.data_provider = Provider()
        visual.music_ambience_style = "solid_album"
        visual.lyric_flow_style = "gelv_songscatter"
        visual.expanded = True
        visual.location_mode = "free"
        visual.music_reactive_background_enabled = True
        visual.music_reactive_background_intensity = 88
        visual.animation_fps = 75
        visual.lyric_animation_fps = 50
        visual.animation_speed = 1.2
        visual.compact_duotone_balance = 42
        visual.expanded_duotone_balance = 64
        visual._lyric_timeline_diagnostics = lambda _now: {
            "sync_lag_lines": 2,
            "visible_lyric_index": 1,
            "position_active_index": 3,
            "phase_source": "timed",
        }
        visual._visual_frame_diagnostics_summary = lambda: {
            "frame_count": 60,
            "sample_count": 59,
            "average_interval_ms": 16.5,
            "target_interval_ms": 16.0,
            "jitter_ms": 2.0,
            "max_interval_ms": 22.0,
            "long_frame_count": 0,
        }
        visual._visual_paint_section_diagnostics_summary = lambda: {
            "groups": {
                "content": {"average_ms": 2.0, "last_ms": 2.4},
                "reactive_effects": {"average_ms": 1.0, "last_ms": 1.2},
            }
        }
        visual._music_reactive_input_diagnostics = lambda _now: {
            "reactive_enabled": True,
            "live_audio_available": True,
            "band_source": "cache",
        }

        diagnostics = visual.provider_diagnostics()

        self.assertIn("runtime_diagnostics_trace", diagnostics)
        trace = diagnostics["runtime_diagnostics_trace"]
        self.assertEqual(trace["sample_count"], 1)
        self.assertEqual(trace["dominant_area"], "lyric_timeline")
        self.assertEqual(trace["bottleneck_group"], "content")
        self.assertEqual(trace["warning_count"], 1)
        context = diagnostics["visual_runtime_context"]
        self.assertEqual(context["ambience_style"], "solid_album")
        self.assertEqual(context["lyric_flow_style"], "gelv_songscatter")
        self.assertTrue(context["expanded"])
        self.assertTrue(context["reactive_background_enabled"])
        self.assertEqual(context["reactive_background_intensity"], 88)
        self.assertEqual(context["animation_fps"], 75)
        self.assertEqual(context["lyric_animation_fps"], 50)
        report = diagnostics["runtime_verification_report"]
        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["ready"])
        self.assertIn("runtime_trace_samples", {issue["key"] for issue in report["issues"]})
        self.assertEqual(report["suggested_wait_seconds"], 2)
        self.assertIn("约 2 秒", report["next_action"])

    def test_export_runtime_diagnostics_writes_json_support_payload(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.provider_diagnostics = lambda: {
            "runtime_diagnostics_snapshot": {"status": "warning"},
            "runtime_diagnostics_trace": {"sample_count": 2},
            "runtime_verification_report": {"status": "partial", "ready": False},
            "visual_frame_diagnostics": {"average_fps": 45.0},
            "visual_paint_section_diagnostics": {"groups": {"content": {"last_ms": 8.0}}},
            "music_reactive_input_diagnostics": {"sampled_bands": (0.7, 0.3, 0.1)},
            "lyric_timeline_diagnostics": {"sync_lag_lines": 1},
        }

        with tempfile.TemporaryDirectory() as temp:
            target = str(Path(temp) / "diagnostics.json")
            written = IslandStackVisual.export_runtime_diagnostics(visual, target)

            self.assertEqual(written, target)
            with open(written, "r", encoding="utf-8") as handle:
                data = json.load(handle)

        diagnostics = data["diagnostics"]
        self.assertEqual(data["app"]["name"], "GluSpot")
        self.assertEqual(diagnostics["runtime_diagnostics_snapshot"]["status"], "warning")
        self.assertEqual(diagnostics["runtime_diagnostics_trace"]["sample_count"], 2)
        self.assertEqual(diagnostics["runtime_verification_report"]["status"], "partial")
        self.assertEqual(diagnostics["visual_frame_diagnostics"]["average_fps"], 45.0)
        self.assertEqual(diagnostics["music_reactive_input_diagnostics"]["sampled_bands"], [0.7, 0.3, 0.1])
        self.assertIn("support", data)
        self.assertEqual(data["support"]["runtime_experiment_plan"]["key"], "capture_ready_baseline")
        self.assertIn("--compare-after", data["support"]["runtime_experiment_compare"]["command"])

    def test_provider_diagnostics_exports_music_input_without_provider_sampling(self):
        class Provider:
            def diagnostics(self):
                return {"provider": "ok"}

            def get_audio_frame(self):
                raise AssertionError("diagnostics must not sample live audio frames")

            def has_live_audio(self):
                raise AssertionError("diagnostics must not probe live audio availability")

        class Runtime:
            def input_diagnostics(self, *, now=None):
                test.assertIsInstance(now, float)
                return {
                    "has_snapshot": True,
                    "band_source": "cache",
                    "live_audio_source": "cache",
                }

        test = self
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.data_provider = Provider()
        visual._audio_reactive_runtime_for_current_settings = lambda: Runtime()

        diagnostics = visual.provider_diagnostics()

        self.assertEqual(diagnostics["provider"], "ok")
        self.assertEqual(
            diagnostics["music_reactive_input_diagnostics"],
            {
                "has_snapshot": True,
                "band_source": "cache",
                "live_audio_source": "cache",
            },
        )
        self.assertEqual(diagnostics["music_wave_cadence_diagnostics"]["status"], "missing")
        self.assertIn("estimated_update_fps", diagnostics["music_wave_cadence_diagnostics"])

    def test_provider_diagnostics_exports_read_only_lyric_timeline_diagnostics(self):
        class Provider:
            def diagnostics(self):
                return {}

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.data_provider = Provider()
        visual.timed_lyrics = [(0.0, "one"), (4.0, "two"), (8.0, "three")]
        visual.lyrics = ["one", "two", "three"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.lyric_changed_at = 20.0
        visual.lyric_offset_seconds = 0.0
        visual.media_app_lyric_offset_seconds = 0.0
        visual.media_position_seconds = 8.2
        visual.media_position_sync_at = 30.0
        visual.media_duration_seconds = 60.0
        visual.media_is_playing = False
        visual.media_position_source = "test"
        visual.media_position_plan_reason = "small_drift_eased"
        visual.media_position_confidence = 0.7
        visual.media_position_interactive = False
        visual.media_source_foreground = False
        visual.lyric_last_phase_index = 0
        visual.lyric_last_phase_progress = 0.2
        visual.lyric_last_phase_at = 29.0
        visual.lyric_transition_kind = ""
        visual._music_reactive_input_diagnostics = lambda _now: {}

        diagnostics = visual.provider_diagnostics()

        self.assertEqual(visual.lyric_last_phase_index, 0)
        self.assertAlmostEqual(visual.lyric_last_phase_progress, 0.2, delta=0.001)
        self.assertAlmostEqual(visual.lyric_last_phase_at, 29.0, delta=0.001)
        self.assertEqual(diagnostics["visible_lyric_index"], 0)
        self.assertEqual(diagnostics["visible_current_lyric"], "one")
        self.assertEqual(diagnostics["lyric_phase_source"], "timed")
        timeline = diagnostics["lyric_timeline_diagnostics"]
        self.assertEqual(diagnostics["visible_position_reason"], "small_drift_eased")
        self.assertEqual(timeline["position_active_index"], 2)
        self.assertEqual(timeline["sync_lag_lines"], 2)
        self.assertAlmostEqual(timeline["effective_position_seconds"], 8.2, delta=0.001)
        self.assertAlmostEqual(timeline["current_line_start_seconds"], 0.0, delta=0.001)
        self.assertAlmostEqual(timeline["next_line_start_seconds"], 4.0, delta=0.001)

    def test_lyric_phase_uses_paint_state_builder_and_updates_cache(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.timed_lyrics = [(0.0, "one"), (4.0, "two")]
        visual.lyric_index = 0
        visual.lyric_changed_at = 10.0
        visual.lyric_last_phase_index = -1
        visual.lyric_last_phase_progress = 0.0
        visual.lyric_last_phase_at = 10.0
        visual.lyric_offset_seconds = 0.0
        visual.media_app_lyric_offset_seconds = 0.0
        visual.media_position_seconds = 2.0
        visual.media_position_sync_at = 12.0
        visual.media_duration_seconds = 60.0
        visual.media_is_playing = True
        visual.lyric_transition_kind = ""

        phase = visual._lyric_phase(12.0)

        self.assertEqual(visual.lyric_last_phase_index, 0)
        self.assertAlmostEqual(visual.lyric_last_phase_progress, 0.5, delta=0.001)
        self.assertAlmostEqual(visual.lyric_last_phase_at, 12.0, delta=0.001)
        self.assertAlmostEqual(phase["progress"], 0.5, delta=0.001)
        self.assertEqual(phase["source"], "timed")

    def test_tick_render_plan_keeps_frame_due_check_lazy_when_visual_data_changed(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.animation_speed = 1.0
        visual._render_frame_due = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Visual data changes should force render without checking frame due")
        )

        plan = visual._tick_render_plan(10.0, active_animation=False, visual_data_changed=True)

        self.assertTrue(plan.should_render)
        self.assertAlmostEqual(plan.scroll_phase_delta, 0.017, delta=0.0001)
        self.assertAlmostEqual(plan.ambient_phase_delta, 0.024, delta=0.0001)

    def test_handle_tick_frame_execution_orders_retune_render_and_frame_side_effects(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        visual.ambient_phase = 0.0
        visual._wave_values_updated_at = 0.0
        actions: list[str] = []
        visual._active_visual_animation_plan = lambda now: actions.append(f"active:{now}") or ActiveVisualAnimationPlan(
            active=True,
            reason="media",
        )
        visual._retune_frame_timer = lambda active: actions.append(f"retune:{active}")

        def render_plan(now: float, active: bool, changed: bool):
            self.assertEqual(actions, ["active:12.0", "retune:True"])
            actions.append(f"render:{active}:{changed}")
            return TickRenderPlan(should_render=True, scroll_phase_delta=0.11, ambient_phase_delta=0.22)

        visual._tick_render_plan = render_plan
        visual._update_wave_values = lambda: actions.append("wave")
        visual._mark_render_frame = lambda now: actions.append(f"mark:{now}")
        visual.update = lambda: actions.append("update")

        rendered = visual._handle_tick_frame_execution(12.0, visual_data_changed=False)

        self.assertTrue(rendered)
        self.assertEqual(
            actions,
            [
                "active:12.0",
                "retune:True",
                "render:True:False",
                "wave",
                "mark:12.0",
                "update",
            ],
        )
        self.assertAlmostEqual(visual.scroll_phase, 0.11, delta=0.0001)
        self.assertAlmostEqual(visual.ambient_phase, 0.22, delta=0.0001)

    def test_handle_tick_frame_execution_can_render_without_wave_sampling_before_wave_cadence(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        visual.ambient_phase = 0.0
        visual._wave_values_updated_at = 11.990
        actions: list[str] = []
        visual._active_visual_animation_plan = lambda now: actions.append(f"active:{now}") or ActiveVisualAnimationPlan(
            active=True,
            reason="media",
        )
        visual._retune_frame_timer = lambda active: actions.append(f"retune:{active}")
        visual._tick_render_plan = lambda now, active, changed: actions.append(f"render:{active}:{changed}") or TickRenderPlan(
            should_render=True,
            scroll_phase_delta=0.11,
            ambient_phase_delta=0.22,
        )
        visual._update_wave_values = lambda: (_ for _ in ()).throw(
            AssertionError("Music wave sampling should use its own cadence, not lyric/render FPS")
        )
        visual._mark_render_frame = lambda now: actions.append(f"mark:{now}")
        visual.update = lambda: actions.append("update")

        rendered = visual._handle_tick_frame_execution(12.0, visual_data_changed=False)

        self.assertTrue(rendered)
        self.assertEqual(actions, ["active:12.0", "retune:True", "render:True:False", "mark:12.0", "update"])
        self.assertAlmostEqual(visual.scroll_phase, 0.11, delta=0.0001)
        self.assertAlmostEqual(visual.ambient_phase, 0.22, delta=0.0001)

    def test_handle_tick_frame_execution_stable_frame_skips_frame_side_effects(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        visual.ambient_phase = 0.0
        visual._wave_values_updated_at = 12.0
        actions: list[str] = []
        visual._active_visual_animation_plan = lambda now: actions.append(f"active:{now}") or ActiveVisualAnimationPlan(
            active=False,
        )
        visual._retune_frame_timer = lambda active: actions.append(f"retune:{active}")
        visual._tick_render_plan = lambda now, active, changed: actions.append(f"render:{active}:{changed}") or TickRenderPlan(
            should_render=False,
        )
        visual._update_wave_values = lambda: (_ for _ in ()).throw(AssertionError("Stable frame should not update waves"))
        visual._mark_render_frame = lambda _now: (_ for _ in ()).throw(
            AssertionError("Stable frame should not mark render time")
        )
        visual.update = lambda: (_ for _ in ()).throw(AssertionError("Stable frame should not update widget"))

        rendered = visual._handle_tick_frame_execution(12.0, visual_data_changed=False)

        self.assertFalse(rendered)
        self.assertEqual(actions, ["active:12.0", "retune:False", "render:False:False"])
        self.assertEqual(visual.scroll_phase, 0.0)
        self.assertEqual(visual.ambient_phase, 0.0)

    def test_handle_tick_frame_execution_can_sample_wave_without_repaint(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        visual.ambient_phase = 0.0
        visual._wave_values_updated_at = 11.900
        actions: list[str] = []
        visual._active_visual_animation_plan = lambda now: actions.append(f"active:{now}") or ActiveVisualAnimationPlan(
            active=False,
        )
        visual._retune_frame_timer = lambda active: actions.append(f"retune:{active}")
        visual._tick_render_plan = lambda now, active, changed: actions.append(f"render:{active}:{changed}") or TickRenderPlan(
            should_render=False,
        )
        visual._update_wave_values = lambda: actions.append("wave")
        visual._mark_render_frame = lambda _now: (_ for _ in ()).throw(
            AssertionError("Wave-only cadence update should not mark render time")
        )
        visual.update = lambda: (_ for _ in ()).throw(
            AssertionError("Wave-only cadence update should not repaint the widget")
        )

        rendered = visual._handle_tick_frame_execution(12.0, visual_data_changed=False)

        self.assertFalse(rendered)
        self.assertEqual(actions, ["active:12.0", "retune:False", "render:False:False", "wave"])
        self.assertEqual(visual.scroll_phase, 0.0)
        self.assertEqual(visual.ambient_phase, 0.0)

    def test_tick_retunes_timer_before_render_decision_and_executes_frame_work(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        self._prime_tick_interaction_visual(visual)
        actions: list[str] = []
        visual._tick_interaction_plan = lambda _now: TickInteractionPlan()
        visual._active_visual_animation_plan = lambda now: actions.append(f"active:{now}") or ActiveVisualAnimationPlan(
            active=True,
            reason="media",
        )
        visual._retune_frame_timer = lambda active: actions.append(f"retune:{active}")

        def render_plan(now: float, active: bool, changed: bool):
            self.assertEqual(actions, ["active:12.0", "retune:True"])
            actions.append(f"render:{active}:{changed}")
            return TickRenderPlan(should_render=True, scroll_phase_delta=0.11, ambient_phase_delta=0.22)

        visual._tick_render_plan = render_plan
        visual._update_wave_values = lambda: actions.append("wave")
        visual._mark_render_frame = lambda now: actions.append(f"mark:{now}")
        visual.update = lambda: actions.append("update")

        with patch("local.island_stack_visual.time.monotonic", return_value=12.0):
            visual._tick()

        self.assertEqual(
            actions,
            [
                "active:12.0",
                "retune:True",
                "render:True:False",
                "wave",
                "mark:12.0",
                "update",
            ],
        )
        self.assertAlmostEqual(visual.scroll_phase, 0.11, delta=0.0001)
        self.assertAlmostEqual(visual.ambient_phase, 0.22, delta=0.0001)

    def test_tick_stable_frame_returns_before_wave_and_widget_update(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        self._prime_tick_interaction_visual(visual)
        visual._wave_values_updated_at = 12.0
        actions: list[str] = []
        visual._tick_interaction_plan = lambda _now: TickInteractionPlan()
        visual._active_visual_animation_plan = lambda now: actions.append(f"active:{now}") or ActiveVisualAnimationPlan(
            active=False,
        )
        visual._retune_frame_timer = lambda active: actions.append(f"retune:{active}")
        visual._tick_render_plan = lambda now, active, changed: actions.append(f"render:{active}:{changed}") or TickRenderPlan(
            should_render=False,
        )
        visual._update_wave_values = lambda: (_ for _ in ()).throw(AssertionError("Stable frame should not update waves"))
        visual._mark_render_frame = lambda _now: (_ for _ in ()).throw(
            AssertionError("Stable frame should not mark render time")
        )
        visual.update = lambda: (_ for _ in ()).throw(AssertionError("Stable frame should not update widget"))

        with patch("local.island_stack_visual.time.monotonic", return_value=12.0):
            visual._tick()

        self.assertEqual(actions, ["active:12.0", "retune:False", "render:False:False"])
        self.assertEqual(visual.scroll_phase, 0.0)
        self.assertEqual(visual.ambient_phase, 0.0)

    def test_tick_scene_suppressed_branch_uses_narrow_scene_handler_interface(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        self._prime_tick_interaction_visual(visual)
        visual.scene_suppressed = True
        actions: list[str] = []
        visual._handle_scene_suppressed_tick = lambda now: actions.append(f"suppressed:{now}")
        visual._handle_tick_interaction = lambda _now: (_ for _ in ()).throw(
            AssertionError("Scene-suppressed tick should skip normal interaction handling")
        )
        visual._handle_tick_frame_execution = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Scene-suppressed tick should skip normal frame execution")
        )

        with patch("local.island_stack_visual.time.monotonic", return_value=12.0):
            visual._tick()

        self.assertEqual(actions, ["suppressed:12.0"])

    def test_tick_follow_refresh_plan_keeps_due_check_lazy_until_active_and_idle(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        calls: list[float] = []
        visual.drag_candidate = False
        visual._taskbar_follow_refresh_due = lambda now: calls.append(now) or True

        inactive = visual._tick_follow_refresh_plan(10.0, active=False)
        self.assertFalse(inactive.should_refresh)
        self.assertEqual(calls, [])

        visual.drag_candidate = True
        dragging = visual._tick_follow_refresh_plan(11.0, active=True)
        self.assertFalse(dragging.should_refresh)
        self.assertEqual(calls, [])

        visual.drag_candidate = False
        due = visual._tick_follow_refresh_plan(12.0, active=True)
        self.assertTrue(due.should_refresh)
        self.assertEqual(calls, [12.0])

    def test_handle_tick_follow_refresh_preserves_order_between_follow_targets(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_above_follow_active = True
        actions: list[str] = []
        visual._taskbar_compact_active = lambda: actions.append("compact-active") or True

        def follow_plan(now: float, *, active: bool):
            actions.append(f"plan:{now}:{active}")
            return TickFollowRefreshPlan(should_refresh=active)

        visual._tick_follow_refresh_plan = follow_plan
        visual._refresh_taskbar_follow_position = lambda: actions.append("refresh-taskbar")
        visual._refresh_taskbar_above_follow_position = lambda: actions.append("refresh-above")

        visual._handle_tick_follow_refresh(12.0)

        self.assertEqual(
            actions,
            [
                "compact-active",
                "plan:12.0:True",
                "refresh-taskbar",
                "plan:12.0:True",
                "refresh-above",
            ],
        )

    def test_lyric_fallback_advance_plan_uses_runtime_state_without_timed_sync(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_is_playing = True
        visual.timed_lyrics = []
        visual.lyrics = ["first", "second", "third"]
        visual.lyric_index = 2
        visual.lyric_changed_at = 10.0

        due = visual._lyric_fallback_advance_plan(12.46, speed=1.0)

        self.assertTrue(due.should_advance)
        self.assertEqual(due.previous_index, 2)
        self.assertEqual(due.next_index, 0)
        self.assertAlmostEqual(due.changed_at, 12.46, delta=0.0001)

        visual.timed_lyrics = [object()]
        skipped = visual._lyric_fallback_advance_plan(20.0, speed=1.0)

        self.assertFalse(skipped.should_advance)

    def test_music_lyric_tick_plan_uses_runtime_state_for_timed_sync_route(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_is_playing = True
        visual.timed_lyrics = [object()]
        visual.lyrics = ["first", "second"]
        visual.lyric_index = 0
        visual.lyric_changed_at = 10.0

        plan = visual._music_lyric_tick_plan(20.0, speed=1.0)

        self.assertTrue(plan.sync_timed_lyrics)
        self.assertFalse(plan.fallback_advance.should_advance)

    def test_handle_music_lyric_tick_routes_sync_and_fallback_side_effects(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        calls: list[str] = []
        visual.prev_lyric_index = 0
        visual.lyric_index = 0
        visual.lyric_changed_at = 10.0
        visual._music_lyric_tick_plan = lambda now, speed: calls.append(f"plan:{now}:{speed}") or MusicLyricTickPlan(
            sync_timed_lyrics=True,
        )
        visual._sync_timed_lyrics = lambda now: calls.append(f"sync:{now}")

        changed = visual._handle_music_lyric_tick(12.0, 1.5)

        self.assertEqual(calls, ["plan:12.0:1.5", "sync:12.0"])
        self.assertFalse(changed)
        self.assertEqual(visual.prev_lyric_index, 0)
        self.assertEqual(visual.lyric_index, 0)
        self.assertAlmostEqual(visual.lyric_changed_at, 10.0, delta=0.0001)

        visual._sync_timed_lyrics = lambda _now: (_ for _ in ()).throw(
            AssertionError("Fallback advance should not sync timed lyrics")
        )
        visual._music_lyric_tick_plan = lambda now, speed: calls.append(f"fallback-plan:{now}:{speed}") or MusicLyricTickPlan(
            fallback_advance=LyricFallbackAdvancePlan(
                should_advance=True,
                previous_index=0,
                next_index=1,
                changed_at=13.0,
            )
        )

        changed = visual._handle_music_lyric_tick(13.0, 1.0)

        self.assertEqual(calls[-1], "fallback-plan:13.0:1.0")
        self.assertTrue(changed)
        self.assertEqual(visual.prev_lyric_index, 0)
        self.assertEqual(visual.lyric_index, 1)
        self.assertAlmostEqual(visual.lyric_changed_at, 13.0, delta=0.0001)

    def test_sync_timed_lyrics_reports_whether_visible_line_changed(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_is_playing = True
        visual.media_position_seconds = 5.1
        visual.media_position_sync_at = 10.0
        visual.media_duration_seconds = 60.0
        visual.lyric_offset_seconds = 0.0
        visual.media_app_lyric_offset_seconds = 0.0
        visual.timed_lyrics = [(0.0, "first"), (5.0, "second")]
        visual.lyrics = ["first", "second"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.lyric_changed_at = 9.0

        changed = visual._sync_timed_lyrics(10.0)

        self.assertTrue(changed)
        self.assertEqual(visual.prev_lyric_index, 0)
        self.assertEqual(visual.lyric_index, 1)
        self.assertAlmostEqual(visual.lyric_changed_at, 10.0, delta=0.0001)

        unchanged = visual._sync_timed_lyrics(10.01)

        self.assertFalse(unchanged)
        self.assertEqual(visual.lyric_index, 1)

    def test_tick_applies_lyric_fallback_advance_plan_for_untimed_lyrics(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.last_tick_at = 10.0
        visual.animation_speed = 1.0
        visual.animation_fps = 60
        visual.lyric_animation_fps = 60
        visual.FRAME_INTERVAL_TOLERANCE_SECONDS = 0.0
        visual.media_is_playing = True
        visual.timed_lyrics = []
        visual.lyrics = ["first", "second"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.lyric_changed_at = 10.0
        visual.scene_suppressed = False
        visual.pointer_pos = QPointF(-1.0, -1.0)
        visual.barrage_clock = 0.0
        visual.super_topmost_enabled = False
        visual.taskbar_above_follow_active = False
        visual.drag_candidate = False
        visual.hovered_key = ""
        visual.hover_armed_key = ""
        visual.hover_started_at = 0.0
        visual.expanded = False
        visual.plugin_panel_open = False
        visual.left_stack_at = 0.0
        visual.scroll_phase = 0.0
        visual.ambient_phase = 0.0
        visual.last_render_update_at = 0.0
        visual._sync_music_background_to_theme = lambda: False
        visual._refresh_media_data = lambda _now: False
        visual._update_scene_state = lambda _now: None
        visual._prune_expired_messages = lambda _now: None
        visual._taskbar_compact_active = lambda: False
        visual._ensure_fullscreen_plugin_barrage_messages = lambda _now: None
        visual._sync_fullscreen_plugin_alert_overlay = lambda _now: None
        visual._point_in_barrage = lambda _point: False
        visual._maybe_trigger_alarm_row_long_press = lambda _now: False
        visual._active_visual_animation_plan = lambda _now: ActiveVisualAnimationPlan(active=False)
        visual._has_active_visual_animation = lambda _now: False
        visual._retune_frame_timer = lambda _active: None
        visual._update_wave_values = lambda: None
        visual.update = lambda: None

        with patch("local.island_stack_visual.time.monotonic", return_value=12.46):
            visual._tick()

        self.assertEqual(visual.prev_lyric_index, 0)
        self.assertEqual(visual.lyric_index, 1)
        self.assertAlmostEqual(visual.lyric_changed_at, 12.46, delta=0.0001)

    def test_tick_routes_timed_lyrics_to_sync_without_fallback_advance(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.last_tick_at = 10.0
        visual.animation_speed = 1.0
        visual.animation_fps = 60
        visual.lyric_animation_fps = 60
        visual.FRAME_INTERVAL_TOLERANCE_SECONDS = 0.0
        visual.media_is_playing = True
        visual.timed_lyrics = [object()]
        visual.lyrics = ["first", "second"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.lyric_changed_at = 10.0
        visual.scene_suppressed = False
        visual.pointer_pos = QPointF(-1.0, -1.0)
        visual.barrage_clock = 0.0
        visual.super_topmost_enabled = False
        visual.taskbar_above_follow_active = False
        visual.drag_candidate = False
        visual.hovered_key = ""
        visual.hover_armed_key = ""
        visual.hover_started_at = 0.0
        visual.expanded = False
        visual.plugin_panel_open = False
        visual.left_stack_at = 0.0
        visual.scroll_phase = 0.0
        visual.ambient_phase = 0.0
        visual.last_render_update_at = 0.0
        visual._sync_music_background_to_theme = lambda: False
        visual._refresh_media_data = lambda _now: False
        visual._update_scene_state = lambda _now: None
        visual._prune_expired_messages = lambda _now: None
        visual._taskbar_compact_active = lambda: False
        visual._ensure_fullscreen_plugin_barrage_messages = lambda _now: None
        visual._sync_fullscreen_plugin_alert_overlay = lambda _now: None
        visual._point_in_barrage = lambda _point: False
        visual._maybe_trigger_alarm_row_long_press = lambda _now: False
        visual._active_visual_animation_plan = lambda _now: ActiveVisualAnimationPlan(active=False)
        visual._has_active_visual_animation = lambda _now: False
        visual._retune_frame_timer = lambda _active: None
        visual._update_wave_values = lambda: None
        visual.update = lambda: None
        sync_calls: list[float] = []
        visual._sync_timed_lyrics = lambda now: sync_calls.append(now)

        with patch("local.island_stack_visual.time.monotonic", return_value=12.46):
            visual._tick()

        self.assertEqual(sync_calls, [12.46])
        self.assertEqual(visual.prev_lyric_index, 0)
        self.assertEqual(visual.lyric_index, 0)
        self.assertAlmostEqual(visual.lyric_changed_at, 10.0, delta=0.0001)

    def test_tick_renders_lyric_change_even_when_music_wave_cadence_is_not_due(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.last_tick_at = 12.0
        visual.last_render_update_at = 12.0
        visual._wave_values_updated_at = 12.0
        visual.animation_fps = 60
        visual.lyric_animation_fps = 60
        visual.FRAME_INTERVAL_TOLERANCE_SECONDS = 0.0
        visual.animation_speed = 1.0
        visual.location_mode = "free"
        visual.scene_suppressed = False
        visual.pointer_pos = QPointF(-1.0, -1.0)
        visual.barrage_clock = 0.0
        visual.barrage_overlay = None
        visual.super_topmost_enabled = False
        visual.media_is_playing = True
        visual.timed_lyrics = [(0.0, "first"), (5.0, "second")]
        visual.lyrics = ["first", "second"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.lyric_changed_at = 11.0
        visual.media_position_seconds = 5.1
        visual.media_position_sync_at = 12.0
        visual.media_duration_seconds = 60.0
        visual.lyric_offset_seconds = 0.0
        visual.media_app_lyric_offset_seconds = 0.0
        visual.hovered_key = ""
        visual.hover_armed_key = ""
        visual.hover_started_at = 0.0
        visual.hover_bounce_until = 0.0
        visual.stack_bounce_until = 0.0
        visual.expanded = False
        visual.plugin_panel_open = False
        visual.left_stack_at = 0.0
        visual.scroll_phase = 0.0
        visual.ambient_phase = 0.0
        visual.plugin_time_module_animation = {}
        visual.plugin_alarm_editor_spin_animations = {}
        visual.plugin_alarm_editor_page_animation = {}
        visual.plugin_alarm_editor_reflow_animation = {}
        visual.scene_transition_mode = ""
        visual.surface_transition_started_at = 0.0
        visual.album_island_transition_started_at = 0.0
        visual._sync_music_background_to_theme = lambda: False
        visual._refresh_media_data = lambda _now: False
        visual._update_scene_state = lambda _now: None
        visual._prune_expired_messages = lambda _now: None
        visual._ensure_fullscreen_plugin_barrage_messages = lambda _now: None
        visual._sync_fullscreen_plugin_alert_overlay = lambda _now: None
        visual._point_in_barrage = lambda _point: False
        visual._maybe_trigger_alarm_row_long_press = lambda _now: False
        visual._active_visual_animation_plan = lambda _now: ActiveVisualAnimationPlan(active=False)
        visual._has_active_visual_animation = lambda _now: False
        visual._retune_frame_timer = lambda _active: None
        wave_calls: list[str] = []
        update_calls: list[str] = []
        visual._update_wave_values = lambda: wave_calls.append("wave")
        visual.update = lambda: update_calls.append("update")

        with patch("local.island_stack_visual.time.monotonic", return_value=12.010):
            visual._tick()

        self.assertEqual(visual.lyric_index, 1)
        self.assertEqual(wave_calls, [])
        self.assertEqual(update_calls, ["update"])
        self.assertGreater(visual.scroll_phase, 0.0)

    def test_barrage_hover_clock_plan_keeps_hit_test_lazy(self):
        class Overlay:
            barrage_hovered = True

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.barrage_overlay = Overlay()
        visual.scene_suppressed = False
        visual.pointer_pos = QPointF(1.0, 1.0)
        visual._point_in_barrage = lambda _point: (_ for _ in ()).throw(
            AssertionError("Overlay hover should short-circuit pointer hit test")
        )

        plan = visual._barrage_hover_clock_plan(0.12)

        self.assertTrue(plan.hovered)
        self.assertEqual(plan.clock_delta, 0.0)

        visual.barrage_overlay = None
        visual.scene_suppressed = True
        visual._point_in_barrage = lambda _point: (_ for _ in ()).throw(
            AssertionError("Suppressed scene should skip pointer hit test")
        )

        plan = visual._barrage_hover_clock_plan(0.12)

        self.assertFalse(plan.hovered)
        self.assertAlmostEqual(plan.clock_delta, 0.12, delta=0.0001)

    def test_handle_fullscreen_overlay_sync_keeps_barrage_before_plugin_overlay(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        actions: list[str] = []
        visual._ensure_fullscreen_plugin_barrage_messages = lambda now: actions.append(f"ensure:{now}")

        def sync(now: float):
            self.assertEqual(actions, ["ensure:12.0"])
            actions.append(f"sync:{now}")

        visual._sync_fullscreen_plugin_alert_overlay = sync

        visual._handle_fullscreen_overlay_sync(12.0)

        self.assertEqual(actions, ["ensure:12.0", "sync:12.0"])

    def test_handle_tick_overlay_clock_and_topmost_preserves_overlay_before_barrage_order(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.barrage_hovered = True
        visual.barrage_clock = 1.0
        actions: list[str] = []
        visual._ensure_fullscreen_plugin_barrage_messages = lambda now: actions.append(f"ensure:{now}")
        visual._sync_fullscreen_plugin_alert_overlay = lambda now: actions.append(f"sync:{now}")

        def barrage_plan(anim_dt: float):
            self.assertEqual(actions, ["ensure:12.0", "sync:12.0"])
            actions.append(f"barrage:{anim_dt}")
            return BarrageHoverClockPlan(hovered=False, clock_delta=0.25)

        def topmost_due(now: float):
            self.assertEqual(actions, ["ensure:12.0", "sync:12.0", "barrage:0.5"])
            self.assertFalse(visual.barrage_hovered)
            self.assertAlmostEqual(visual.barrage_clock, 1.25, delta=0.0001)
            actions.append(f"topmost:{now}")
            return True

        visual._barrage_hover_clock_plan = barrage_plan
        visual._topmost_reinforcement_due = topmost_due
        visual._force_super_topmost = lambda: actions.append("force-topmost")

        visual._handle_tick_overlay_clock_and_topmost(12.0, 0.5)

        self.assertEqual(
            actions,
            ["ensure:12.0", "sync:12.0", "barrage:0.5", "topmost:12.0", "force-topmost"],
        )
        self.assertFalse(visual.barrage_hovered)
        self.assertAlmostEqual(visual.barrage_clock, 1.25, delta=0.0001)

    def test_topmost_reinforcement_due_keeps_interval_lazy_when_disabled(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.super_topmost_enabled = False
        visual.topmost_reinforced_at = 0.0
        visual._topmost_reinforce_interval = lambda: (_ for _ in ()).throw(
            AssertionError("Disabled topmost reinforcement should not compute interval")
        )

        self.assertFalse(visual._topmost_reinforcement_due(10.0))

    def test_handle_tick_runtime_refresh_orders_theme_media_scene_and_prune(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        actions: list[str] = []
        visual._sync_music_background_to_theme = lambda: actions.append("theme") or True

        def refresh(now: float):
            self.assertEqual(actions, ["theme"])
            actions.append(f"media:{now}")
            return False

        visual._refresh_media_data = refresh
        visual._update_scene_state = lambda now: actions.append(f"scene:{now}")
        visual._prune_expired_messages = lambda now: actions.append(f"prune:{now}")

        changed = visual._handle_tick_runtime_refresh(12.0)

        self.assertTrue(changed)
        self.assertEqual(actions, ["theme", "media:12.0", "scene:12.0", "prune:12.0"])

    def test_handle_tick_runtime_refresh_keeps_media_change_when_theme_is_stable(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._sync_music_background_to_theme = lambda: False
        visual._refresh_media_data = lambda _now: True
        visual._update_scene_state = lambda _now: None
        visual._prune_expired_messages = lambda _now: None

        self.assertTrue(visual._handle_tick_runtime_refresh(12.0))

    def test_scene_suppressed_tick_plan_delegates_frame_due_rule(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        calls: list[tuple[float, bool]] = []
        visual._render_frame_due = lambda now, active: calls.append((now, active)) or active

        skipped = visual._scene_suppressed_tick_plan(12.0, active_animation=False)
        due = visual._scene_suppressed_tick_plan(13.0, active_animation=True)

        self.assertEqual(calls, [(12.0, False), (13.0, True)])
        self.assertFalse(skipped.should_render)
        self.assertTrue(due.should_render)

    def test_scene_suppressed_tick_clears_interaction_before_animation_check(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.hovered_key = "plugin"
        visual.hover_armed_key = "plugin"
        visual.left_stack_at = 1.0
        visual._ensure_fullscreen_plugin_barrage_messages = lambda _now: None
        visual._sync_fullscreen_plugin_alert_overlay = lambda _now: None
        visual._retune_frame_timer = lambda _active: None
        visual._render_frame_due = lambda *_args: False
        visual._active_visual_animation_plan = lambda _now: ActiveVisualAnimationPlan(
            active=(
                getattr(visual, "hovered_key", "") == ""
                and getattr(visual, "hover_armed_key", "") == ""
                and getattr(visual, "left_stack_at", 0.0) == 0.0
            ),
            reason="cleared",
        )
        visual.update = lambda: (_ for _ in ()).throw(AssertionError("Not frame due"))

        visual._handle_scene_suppressed_tick(12.0)

        self.assertEqual(visual.hovered_key, "")
        self.assertEqual(visual.hover_armed_key, "")
        self.assertEqual(visual.left_stack_at, 0.0)

    def test_scene_suppressed_frame_execution_orders_retune_render_and_frame_side_effects(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        actions: list[str] = []
        visual._active_visual_animation_plan = lambda now: actions.append(f"active:{now}") or ActiveVisualAnimationPlan(
            active=True,
            reason="media",
        )
        visual._retune_frame_timer = lambda active: actions.append(f"retune:{active}")

        def render_plan(now: float, active: bool):
            self.assertEqual(actions, ["active:12.0", "retune:True"])
            actions.append(f"render:{now}:{active}")
            return presentation_scene_suppressed_tick_plan(render_frame_due=True)

        visual._scene_suppressed_tick_plan = render_plan
        visual._mark_render_frame = lambda now: actions.append(f"mark:{now}")
        visual.update = lambda: actions.append("update")

        frame_plan = visual._scene_suppressed_frame_execution_plan(12.0)
        if frame_plan.mark_render_frame:
            visual._mark_render_frame(12.0)
        if frame_plan.update_widget:
            visual.update()

        self.assertTrue(frame_plan.should_render)
        self.assertEqual(frame_plan.active_reason, "media")
        self.assertEqual(
            actions,
            ["active:12.0", "retune:True", "render:12.0:True", "mark:12.0", "update"],
        )

    def test_scene_suppressed_frame_execution_stable_frame_skips_frame_side_effects(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        actions: list[str] = []
        visual._active_visual_animation_plan = lambda now: actions.append(f"active:{now}") or ActiveVisualAnimationPlan(
            active=False,
        )
        visual._retune_frame_timer = lambda active: actions.append(f"retune:{active}")
        visual._scene_suppressed_tick_plan = lambda now, active: actions.append(f"render:{now}:{active}") or presentation_scene_suppressed_tick_plan(
            render_frame_due=False,
        )

        frame_plan = visual._scene_suppressed_frame_execution_plan(12.0)

        self.assertFalse(frame_plan.should_render)
        self.assertFalse(frame_plan.mark_render_frame)
        self.assertFalse(frame_plan.update_widget)
        self.assertEqual(actions, ["active:12.0", "retune:False", "render:12.0:False"])

    def test_tick_delta_helpers_preserve_clamped_animation_delta(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.last_tick_at = 10.0
        visual.animation_speed = 2.0

        self.assertAlmostEqual(visual._tick_delta_seconds(11.0), 0.2, delta=0.0001)
        self.assertAlmostEqual(visual._animation_tick_delta_seconds(11.0), 0.4, delta=0.0001)

    def test_hover_auto_activation_due_keeps_allowed_check_lazy(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.hovered_key = "plugin"
        visual.hover_armed_key = "music"
        visual.hover_started_at = 10.0
        visual.expanded = False
        calls: list[str] = []
        visual._hover_auto_activation_allowed = lambda key: calls.append(key) or True

        self.assertFalse(visual._hover_auto_activation_due(11.0))
        self.assertEqual(calls, [])

        visual.hover_armed_key = "plugin"
        self.assertTrue(visual._hover_auto_activation_due(11.0))
        self.assertEqual(calls, ["plugin"])

    def test_tick_hover_due_helpers_preserve_plugin_open_and_left_collapse_delays(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.hovered_key = "plugin"
        visual.plugin_panel_open = False
        visual.hover_started_at = 10.0
        visual.left_stack_at = 10.0

        self.assertFalse(visual._plugin_panel_hover_open_due(10.49))
        self.assertTrue(visual._plugin_panel_hover_open_due(10.5))
        self.assertFalse(visual._left_stack_collapse_due(10.49))
        self.assertTrue(visual._left_stack_collapse_due(10.5))

    def test_tick_interaction_plan_collects_adapter_due_results(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        calls: list[str] = []
        visual._hover_auto_activation_due = lambda now: calls.append(f"hover:{now}") or True
        visual._plugin_panel_hover_open_due = lambda now: calls.append(f"plugin:{now}") or False
        visual._left_stack_collapse_due = lambda now: calls.append(f"left:{now}") or True
        visual._maybe_trigger_alarm_row_long_press = lambda now: calls.append(f"alarm:{now}") or False

        plan = visual._tick_interaction_plan(12.0)

        self.assertEqual(calls, ["hover:12.0", "plugin:12.0", "left:12.0", "alarm:12.0"])
        self.assertTrue(plan.activate_hover_key)
        self.assertFalse(plan.open_plugin_panel)
        self.assertTrue(plan.collapse_left_stack)
        self.assertFalse(plan.stop_after_alarm_long_press)

    def test_tick_executes_interaction_plan_before_normal_render(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        self._prime_tick_interaction_visual(visual)
        visual._wave_values_updated_at = 12.0
        actions: list[str] = []
        visual._tick_interaction_plan = lambda _now: TickInteractionPlan(
            activate_hover_key=True,
            clear_hover_armed_key=True,
            open_plugin_panel=True,
            pulse_after_plugin_open=True,
            collapse_left_stack=True,
            clear_left_stack=True,
            pulse_after_left_collapse=True,
        )
        visual._activate_key = lambda key: actions.append(f"activate:{key}")
        visual._set_expanded = lambda expanded: actions.append(f"expanded:{expanded}") or setattr(visual, "expanded", expanded)
        visual._pulse_stack = lambda: actions.append("pulse")
        visual.update = lambda: actions.append("update")

        with patch("local.island_stack_visual.time.monotonic", return_value=12.0):
            visual._tick()

        self.assertEqual(actions, ["activate:plugin", "pulse", "expanded:False", "pulse"])
        self.assertEqual(visual.hover_armed_key, "")
        self.assertTrue(visual.plugin_panel_open)
        self.assertEqual(visual.left_stack_at, 0.0)

    def test_handle_tick_interaction_applies_actions_in_order_and_reports_stop(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.hovered_key = "plugin"
        visual.hover_armed_key = "plugin"
        visual.plugin_panel_open = False
        visual.left_stack_at = 10.0
        actions: list[str] = []
        visual._tick_interaction_plan = lambda now: actions.append(f"plan:{now}") or TickInteractionPlan(
            activate_hover_key=True,
            clear_hover_armed_key=True,
            open_plugin_panel=True,
            pulse_after_plugin_open=True,
            collapse_left_stack=True,
            clear_left_stack=True,
            pulse_after_left_collapse=True,
            render_after_alarm_long_press=True,
            stop_after_alarm_long_press=True,
        )
        visual._activate_key = lambda key: actions.append(f"activate:{key}")
        visual._pulse_stack = lambda: actions.append("pulse")
        visual._set_expanded = lambda expanded: actions.append(f"expanded:{expanded}")
        visual.update = lambda: actions.append("update")
        visual._mark_render_frame = lambda now: actions.append(f"mark:{now}")

        stop = visual._handle_tick_interaction(12.0)

        self.assertTrue(stop)
        self.assertEqual(
            actions,
            [
                "plan:12.0",
                "activate:plugin",
                "pulse",
                "expanded:False",
                "pulse",
                "update",
                "mark:12.0",
            ],
        )
        self.assertEqual(visual.hover_armed_key, "")
        self.assertTrue(visual.plugin_panel_open)
        self.assertEqual(visual.left_stack_at, 0.0)

    def test_tick_long_press_plan_marks_frame_and_stops_before_render(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        self._prime_tick_interaction_visual(visual)
        actions: list[str] = []
        visual._tick_interaction_plan = lambda _now: TickInteractionPlan(
            render_after_alarm_long_press=True,
            stop_after_alarm_long_press=True,
        )
        visual.update = lambda: actions.append("update")
        visual._mark_render_frame = lambda now: actions.append(f"mark:{now}")
        visual._active_visual_animation_plan = lambda _now: (_ for _ in ()).throw(
            AssertionError("Long-press tick should stop before normal render planning")
        )

        with patch("local.island_stack_visual.time.monotonic", return_value=12.0):
            visual._tick()

        self.assertEqual(actions, ["update", "mark:12.0"])

    def test_animation_fps_clamps_to_display_refresh_rate_and_minimum(self):
        class FakeScreen:
            def __init__(self, refresh_rate: float):
                self._refresh_rate = refresh_rate

            def refreshRate(self):
                return self._refresh_rate

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._notify_settings_changed = lambda: None

        with patch("local.island_stack_visual.QGuiApplication.screens", return_value=[FakeScreen(144.0)]):
            visual.set_animation_fps(240, notify=False)
            self.assertEqual(visual.animation_fps, 144)
            visual.set_animation_fps(12, notify=False)
            self.assertEqual(visual.animation_fps, 30)

    def test_lyric_animation_fps_can_drive_lyric_render_interval(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.animation_fps = 30
        visual.lyric_animation_fps = 120
        visual.media_is_playing = True
        visual.lyric_changed_at = 10.0
        visual.lyrics = ["line"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.lyric_transition_kind = ""
        visual._should_paint_album_island = lambda: False

        with patch.object(IslandStackVisual, "_display_refresh_rate_fps_cap", return_value=144):
            self.assertEqual(visual._effective_animation_fps(True, now=10.1), 120)
            self.assertAlmostEqual(visual._render_interval_seconds(True, now=10.1), 1.0 / 120.0, delta=0.001)

    def test_general_animation_fps_drives_drag_render_interval(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.animation_fps = 96
        visual.lyric_animation_fps = 30
        visual.media_is_playing = False
        visual.dragging = True
        visual.lyrics = ["line"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.lyric_changed_at = 0.0
        visual.lyric_transition_kind = ""
        visual._should_paint_album_island = lambda: False

        with patch.object(IslandStackVisual, "_display_refresh_rate_fps_cap", return_value=144):
            self.assertEqual(visual._effective_animation_fps(True, now=20.0), 96)

    def test_active_visual_animation_plan_short_circuits_early_reasons(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_is_playing = True
        visual.finder_mode = False
        visual.drag_candidate = False
        visual.dragging = False
        visual.barrage_hovered = False
        visual.hover_bounce_until = 0.0
        visual.stack_bounce_until = 0.0
        visual.taskbar_left_handle_visible_until = 0.0
        visual._transition_progress_for_surface = lambda _now: (_ for _ in ()).throw(
            AssertionError("Early active animation should not inspect music surface transition")
        )
        visual._should_paint_album_island = lambda: (_ for _ in ()).throw(
            AssertionError("Early active animation should not inspect album island")
        )

        plan = visual._active_visual_animation_plan(12.0)

        self.assertTrue(plan.active)
        self.assertEqual(plan.reason, "media")

    def test_active_visual_animation_plan_reports_album_transition_reason(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_is_playing = False
        visual.finder_mode = False
        visual.drag_candidate = False
        visual.dragging = False
        visual.barrage_hovered = False
        visual.hover_bounce_until = 0.0
        visual.stack_bounce_until = 0.0
        visual.taskbar_left_handle_visible_until = 0.0
        visual.scene_transition_mode = ""
        visual.surface_transition_pixmap = QPixmap()
        visual.music_surface_transition_mode = ""
        visual.prev_lyric_index = 0
        visual.lyric_index = 0
        visual.lyric_transition_kind = ""
        visual.plugin_time_module_animation = {}
        visual.plugin_alarm_editor_page_animation = {}
        visual.plugin_alarm_editor_reflow_animation = {}
        visual.plugin_alarm_editor_spin_animations = {}
        visual.music_control_press_started_at = {}
        visual._should_paint_album_island = lambda: True
        visual._album_island_transition_progress = lambda _now: 0.42

        plan = visual._active_visual_animation_plan(12.0)

        self.assertTrue(plan.active)
        self.assertEqual(plan.reason, "album_island_transition")

    def test_active_visual_animation_plan_keeps_plugin_animation_checks_lazy(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_is_playing = False
        visual.finder_mode = False
        visual.drag_candidate = False
        visual.dragging = False
        visual.barrage_hovered = False
        visual.hover_bounce_until = 0.0
        visual.stack_bounce_until = 0.0
        visual.taskbar_left_handle_visible_until = 0.0
        visual.scene_transition_mode = ""
        visual.surface_transition_pixmap = QPixmap()
        visual.music_surface_transition_mode = ""
        visual.prev_lyric_index = 0
        visual.lyric_index = 0
        visual.lyric_transition_kind = ""
        visual.plugin_time_module_animation = {"started_at": 10.0}
        visual.plugin_alarm_editor_page_animation = {"started_at": 10.0}
        visual.plugin_alarm_editor_reflow_animation = {"started_at": 10.0}
        visual._should_paint_album_island = lambda: False
        calls: list[str] = []

        def animation_dict_active(animation, _now, _duration):
            calls.append(str(animation))
            return True

        visual._animation_dict_active = animation_dict_active
        visual._alarm_spin_animations_active = lambda _now: (_ for _ in ()).throw(
            AssertionError("Plugin time animation should short-circuit later plugin checks")
        )

        plan = visual._active_visual_animation_plan(12.0)

        self.assertTrue(plan.active)
        self.assertEqual(plan.reason, "plugin_time_module")
        self.assertEqual(calls, ["{'started_at': 10.0}"])

    def test_tasker_motion_response_is_fast_for_taskbar_follow(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = False
        visual.location_mode = "tasker"
        visual.stack_bounce_until = 0.0

        response, damping = visual._visual_motion_params(now=10.0)

        self.assertLessEqual(response, 0.22)
        self.assertGreaterEqual(damping, 0.84)

    def test_tasker_main_initial_motion_starts_at_target_without_vertical_pop(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = False
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.stack_direction = -1
        visual.motions = {}
        visual._u = lambda value: float(value)
        main = StackVisual("music", IslandKind.MUSIC, QRectF(100.0, 40.0, 420.0, 34.0), False, None, 10)

        start = visual._initial_motion_rect_for_visual(main, [main])

        self.assertEqual(start, main.rect)

    def test_ingest_provider_messages_keeps_latest_message_per_app(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.messages = []
        visual.barrage_clock = 12.0
        visual._seen_provider_message_keys = set()
        visual.plugin_samples = []

        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("Line",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
        )
        visual_data = IslandVisualData(
            track=track,
            messages=(
                MessageData("WeChat", "Alice", "first", ColorToken(75, 210, 110), ColorToken(75, 210, 110)),
                MessageData("QQ", "Bob", "qq", ColorToken(80, 170, 255), ColorToken(80, 170, 255)),
                MessageData("WeChat", "Alice", "latest", ColorToken(75, 210, 110), ColorToken(75, 210, 110)),
            ),
            plugins=(),
        )

        visual._ingest_provider_messages(visual_data)

        self.assertEqual([message.app for message in visual.messages], ["WeChat", "QQ"])
        self.assertEqual(visual.messages[0].text, "latest")
        self.assertEqual(len([message for message in visual.messages if message.app == "WeChat"]), 1)

    def test_wechat_summary_notification_expires_after_twenty_seconds(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.messages = []
        visual.barrage_clock = 12.0
        visual._seen_provider_message_keys = set()
        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("Line",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
        )
        visual_data = IslandVisualData(
            track=track,
            messages=(MessageData("微信", "微信", "收到新消息", ColorToken(75, 210, 110), ColorToken(75, 210, 110)),),
            plugins=(),
        )

        with patch("local.island_stack_visual.time.monotonic", return_value=100.0):
            visual._ingest_provider_messages(visual_data)

        self.assertAlmostEqual(visual.messages[0].expires_at, 120.0, delta=0.001)
        visual._prune_expired_messages(119.9)
        self.assertEqual(len(visual.messages), 1)
        visual._prune_expired_messages(120.1)
        self.assertEqual(visual.messages, [])

    def test_detail_messages_do_not_expire_like_summary_notifications(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.messages = []
        visual.barrage_clock = 12.0
        visual._seen_provider_message_keys = set()
        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("Line",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
        )
        visual_data = IslandVisualData(
            track=track,
            messages=(MessageData("微信", "张三", "晚上开会吗？", ColorToken(75, 210, 110), ColorToken(75, 210, 110)),),
            plugins=(),
        )

        with patch("local.island_stack_visual.time.monotonic", return_value=100.0):
            visual._ingest_provider_messages(visual_data)

        self.assertEqual(visual.messages[0].expires_at, 0.0)
        visual._prune_expired_messages(999.0)
        self.assertEqual(len(visual.messages), 1)

    def test_counted_new_message_summary_expires_after_twenty_seconds(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.messages = []
        visual.barrage_clock = 12.0
        visual._seen_provider_message_keys = set()
        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("Line",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
        )
        visual_data = IslandVisualData(
            track=track,
            messages=(MessageData("微信", "项目群", "2条新消息", ColorToken(75, 210, 110), ColorToken(75, 210, 110)),),
            plugins=(),
        )

        with patch("local.island_stack_visual.time.monotonic", return_value=100.0):
            visual._ingest_provider_messages(visual_data)

        self.assertAlmostEqual(visual.messages[0].expires_at, 120.0, delta=0.001)
        visual._prune_expired_messages(120.1)
        self.assertEqual(visual.messages, [])

    def test_clear_messages_for_app_removes_all_matching_messages(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.messages = [
            Message("微信", "张三", "第一条", QColor(75, 210, 110), QColor(75, 210, 110)),
            Message("QQ", "李四", "第二条", QColor(80, 170, 255), QColor(80, 170, 255)),
            Message("微信", "王五", "第三条", QColor(75, 210, 110), QColor(75, 210, 110)),
        ]
        visual.barrage_preview_messages = [
            Message("微信", "预览", "预览消息", QColor(75, 210, 110), QColor(75, 210, 110)),
            Message("钉钉", "预览", "其他预览", QColor(50, 150, 255), QColor(50, 150, 255)),
        ]
        visual.update = lambda: None

        visual._clear_messages_for_app("微信")

        self.assertEqual([message.app for message in visual.messages], ["QQ"])
        self.assertEqual([message.app for message in visual.barrage_preview_messages], ["钉钉"])

    def test_barrage_y_offset_clamps_to_full_screen_height(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.barrage_y_offset = 0
        visual.barrage_font_size = 10
        visual.barrage_speed = 100
        visual.barrage_use_backdrop = True
        visual.barrage_show_icon = True
        visual._barrage_max_y_offset = lambda: 1079
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        visual.set_barrage_settings(y_offset=900, notify=False)
        self.assertEqual(visual.barrage_y_offset, 900)

        visual.set_barrage_settings(y_offset=1300, notify=False)
        self.assertEqual(visual.barrage_y_offset, 1079)

    def test_barrage_motion_uses_full_screen_viewport_width(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual._barrage_viewport_rect = lambda: QRectF(0.0, 0.0, 1920.0, 1080.0)

        travel = visual._barrage_travel_distance(180.0)

        self.assertGreaterEqual(travel, 1920.0 + 180.0)
        self.assertGreater(travel, 940.0 + 180.0 + 140.0)

    def test_fullscreen_plugin_alert_can_be_repeated_as_clickable_barrage(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.sample_index = 0
        visual.barrage_clock = 12.0
        visual.plugin_fullscreen_alert_style = "barrage"
        alert = (
            0,
            "TIME",
            "0:00",
            "Countdown 0:00",
            QColor(255, 184, 0),
            "",
            "time",
            "done",
            "计时结束",
            "点击查看或清除计时器",
            5,
        )
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [alert]

        messages = visual._fullscreen_plugin_alert_barrage_messages(repeat_count=10, now=12.0)

        self.assertEqual(len(messages), 10)
        self.assertTrue(all(getattr(message, "action_key", "") == "plugin-alert:time" for message in messages))
        self.assertTrue(all(message.barrage_started_at >= 12.0 for message in messages))
        self.assertIn("点击查看", messages[0].text)

    def test_fullscreen_plugin_barrage_uses_barrage_clock_not_monotonic_clock(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.sample_index = 0
        visual.barrage_clock = 3.25
        visual.plugin_fullscreen_alert_style = "barrage"
        alert = (
            0,
            "TIME",
            "0:00",
            "Countdown 0:00",
            QColor(255, 184, 0),
            "",
            "time",
            "done",
            "timer done",
            "click to review or clear",
            5,
        )
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [alert]

        messages = visual._fullscreen_plugin_alert_barrage_messages(repeat_count=3, now=123456.0)

        self.assertAlmostEqual(messages[0].barrage_started_at, 3.25, delta=0.001)
        self.assertAlmostEqual(messages[1].barrage_started_at, 3.43, delta=0.001)
        self.assertLess(messages[-1].barrage_started_at, 4.0)

    def test_fullscreen_overlay_configure_reinforces_native_topmost(self):
        source = inspect.getsource(FullscreenPluginAlertOverlay.configure)

        self.assertIn("_force_overlay_topmost", source)

    def test_fullscreen_overlay_native_topmost_uses_set_window_pos(self):
        source = inspect.getsource(FullscreenPluginAlertOverlay)

        self.assertIn("SetWindowPos", source)
        self.assertIn("hwnd_topmost", source)

    def test_plugin_fullscreen_alert_legacy_island_style_maps_to_popup_island(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_fullscreen_alert_style = "island"
        visual.fullscreen_plugin_barrage_alert_key = ""
        visual.fullscreen_plugin_barrage_messages = []
        visual._sync_fullscreen_plugin_alert_overlay = lambda _now: None
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        visual.set_plugin_fullscreen_alert_style("island", notify=False)

        self.assertEqual(visual.plugin_fullscreen_alert_style, "toast")

    def test_plugin_fullscreen_popup_alert_is_top_center_island(self):
        owner = type("Owner", (), {"plugin_fullscreen_alert_x_offset": 0, "plugin_fullscreen_alert_y_offset": 0})()
        overlay = FullscreenPluginAlertOverlay(owner)
        overlay.resize(1000, 700)

        rect = overlay._card_rect("toast")

        self.assertAlmostEqual(rect.center().x(), 500.0, delta=1.0)
        self.assertLessEqual(rect.top(), 30.0)
        self.assertLessEqual(rect.height(), 68.0)

    def test_plugin_fullscreen_alert_style_paints_screen_sized_confirmation(self):
        owner = type("Owner", (), {"plugin_fullscreen_alert_x_offset": 0, "plugin_fullscreen_alert_y_offset": 0})()
        overlay = FullscreenPluginAlertOverlay(owner)
        overlay.resize(800, 450)
        overlay.alert_state = {
            "style": "fullscreen",
            "alert": (
                0,
                "TIME",
                "0:00",
                "Countdown 0:00",
                QColor(255, 184, 0),
                "",
                "time",
                "done",
                "倒计时",
                "时间到",
                5,
            ),
            "action_key": "plugin-alert:time",
            "alert_key": "time|done",
        }
        image = QImage(overlay.size(), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)

        overlay.render(painter, QPoint())
        painter.end()

        self.assertGreater(image.pixelColor(4, 4).alpha(), 70)
        self.assertIn("plugin-alert:time", overlay.active_rects)
        self.assertIn("fullscreen-alert-snooze", overlay.active_rects)
        self.assertIn("fullscreen-alert-dismiss", overlay.active_rects)
        self.assertGreaterEqual(len(overlay.active_rects), 3)
        for action_rect in overlay.active_rects.values():
            self.assertLess(action_rect.width(), overlay.width() * 0.50)
            self.assertLess(action_rect.height(), overlay.height() * 0.25)

    def test_fullscreen_plugin_alert_state_requires_scene_suppression_and_alert(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = True
        visual.plugin_fullscreen_alert_style = "toast"
        alert = (
            0,
            "TIME",
            "0:00",
            "Countdown 0:00",
            QColor(255, 184, 0),
            "",
            "time",
            "done",
            "timer done",
            "click to review or clear",
            5,
        )
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [alert]

        state = visual._fullscreen_plugin_alert_overlay_state()

        self.assertEqual(state["style"], "toast")
        self.assertEqual(state["alert"], alert)
        self.assertEqual(state["action_key"], "plugin-alert:time")

        visual.scene_suppressed = False
        self.assertIsNone(visual._fullscreen_plugin_alert_overlay_state())

    def test_fullscreen_plugin_alert_ignores_running_time_realtime_state(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = True
        visual.plugin_fullscreen_alert_style = "toast"
        running = (
            0,
            "TIME",
            "2:24",
            "倒计时 2:24",
            QColor(255, 184, 0),
            "",
            "time",
            "running",
            "倒计时",
            "2:24",
            30,
        )
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [running]

        self.assertIsNone(visual._fullscreen_plugin_alert_overlay_state())

    def test_fullscreen_plugin_alert_overlay_does_not_pull_alerts_when_context_inactive(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._fullscreen_overlay_context_active = lambda: False
        visual._fullscreen_plugin_alerts = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("alerts should not be pulled when fullscreen overlay context is inactive")
        )

        self.assertIsNone(visual._fullscreen_plugin_alert_overlay_state())

    def test_fullscreen_plugin_alert_accepts_time_done_reminder_state(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = True
        visual.plugin_fullscreen_alert_style = "toast"
        done = (
            0,
            "TIME",
            "0:00",
            "倒计时 0:00",
            QColor(255, 184, 0),
            "",
            "time",
            "done",
            "倒计时",
            "时间到",
            5,
        )
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [done]

        state = visual._fullscreen_plugin_alert_overlay_state()

        self.assertIsNotNone(state)
        self.assertEqual(state["alert"], done)

    def test_barrage_preview_uses_overlay_even_before_scene_is_hidden(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = False
        visual.settings_window_active = True
        visual.barrage_clock = 4.0
        message = Message("Preview", "Barrage", "Position 300px", QColor(96, 186, 255), QColor(52, 118, 240), serial=1)
        message.barrage_started_at = 4.0
        visual.barrage_preview_messages = [message]
        visual.fullscreen_plugin_barrage_messages = []
        visual.messages = []

        state = visual._fullscreen_barrage_overlay_state(preview_only=True)

        self.assertIsNotNone(state)
        self.assertEqual(state["style"], "barrage")
        self.assertEqual(state["alert_key"], "preview-barrage:1")

    def test_barrage_preview_overlay_stops_after_settings_preview_releases(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = False
        visual.settings_window_active = False
        visual.barrage_clock = 4.0
        message = Message("Preview", "Barrage", "Position 300px", QColor(96, 186, 255), QColor(52, 118, 240), serial=1)
        message.barrage_started_at = 4.0
        visual.barrage_preview_messages = [message]

        state = visual._fullscreen_barrage_overlay_state(preview_only=True)

        self.assertIsNone(state)

    def test_barrage_display_mode_anytime_allows_overlay_without_fullscreen(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = False
        visual.scene_reason = ""
        visual.last_scene_probe_reason = ""
        visual.settings_window_active = False
        visual.barrage_display_mode = "always"
        visual.barrage_clock = 4.0
        message = Message("WeChat", "Alice", "hello", QColor(96, 186, 255), QColor(52, 118, 240), serial=1)
        message.barrage_started_at = 4.0
        visual.messages = [message]
        visual.barrage_preview_messages = []
        visual.fullscreen_plugin_barrage_messages = []

        state = visual._barrage_overlay_state()

        self.assertIsNotNone(state)
        self.assertEqual(state["style"], "barrage")

    def test_barrage_overlay_state_carries_message_snapshot(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = False
        visual.scene_reason = ""
        visual.last_scene_probe_reason = ""
        visual.settings_window_active = False
        visual.barrage_display_mode = "always"
        visual.barrage_clock = 4.0
        visual.barrage_preview_messages = []
        visual.fullscreen_plugin_barrage_messages = []
        message = Message("WeChat", "Alice", "hello", QColor(96, 186, 255), QColor(52, 118, 240), serial=1)
        message.barrage_started_at = 4.0
        visual.messages = [message]

        state = visual._barrage_overlay_state()

        self.assertIsNotNone(state)
        self.assertIn("messages", state)
        self.assertEqual(state["messages"], [message])

    def test_barrage_overlay_has_independent_frame_timer(self):
        source = inspect.getsource(BarrageOverlay)

        self.assertIn("frame_timer", source)
        self.assertIn("_advance_barrage_frame", source)
        self.assertIn("active_messages=", source)
        self.assertIn("clock=", source)

    def test_barrage_paint_scene_accepts_snapshot_and_clock(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.barrage_clock = 999.0
        visual.barrage_y_offset = 40
        visual.barrage_font_size = 12
        visual.barrage_speed = 100
        visual.barrage_use_backdrop = True
        visual.barrage_show_icon = True
        visual.barrage_preview_messages = []
        visual.fullscreen_plugin_barrage_messages = []
        visual.messages = []
        visual._u = lambda value: float(value)
        visual._font = lambda size, weight=QFont.Weight.Normal: QFont("Arial", int(size), int(weight))
        visual._barrage_travel_distance = lambda content_width, viewport=None: float((viewport or QRectF(0, 0, 640, 220)).width()) + float(content_width) + 140.0
        painted: list[QRectF] = []
        visual._paint_barrage_text = lambda _painter, rect, *_args: painted.append(QRectF(rect))
        message = Message("Preview", "Barrage", "visible", QColor(96, 186, 255), QColor(52, 118, 240), serial=1)
        message.barrage_started_at = 19.15

        image = QImage(640, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        try:
            rects = visual._paint_barrage_scene(
                painter,
                QRectF(0.0, 0.0, 640.0, 220.0),
                active_messages=[message],
                clock=20.0,
                prune=False,
            )
        finally:
            painter.end()

        self.assertTrue(rects)
        self.assertTrue(painted)
        self.assertEqual(visual.barrage_preview_messages, [])
        self.assertEqual(visual.fullscreen_plugin_barrage_messages, [])

    def test_barrage_display_mode_fullscreen_blocks_normal_overlay_until_preview(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = False
        visual.scene_reason = ""
        visual.last_scene_probe_reason = ""
        visual.settings_window_active = False
        visual.barrage_display_mode = "fullscreen"
        visual.barrage_clock = 4.0
        message = Message("WeChat", "Alice", "hello", QColor(96, 186, 255), QColor(52, 118, 240), serial=1)
        message.barrage_started_at = 4.0
        visual.messages = [message]
        visual.barrage_preview_messages = []
        visual.fullscreen_plugin_barrage_messages = []

        self.assertIsNone(visual._barrage_overlay_state())

        preview = Message("Preview", "Barrage", "Position 300px", QColor(96, 186, 255), QColor(52, 118, 240), serial=2)
        preview.barrage_started_at = 4.0
        visual.barrage_preview_messages = [preview]
        visual.last_barrage_preview_at = 10.0
        with patch("local.island_stack_visual.time.monotonic", return_value=10.5):
            state = visual._barrage_overlay_state()

        self.assertIsNotNone(state)
        self.assertEqual(state["alert_key"], "preview-barrage:1")

    def test_barrage_preview_can_sync_overlay_without_hiding_island_for_settings(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.settings_window_active = False
        visual.last_barrage_preview_at = -100.0
        visual.sample_index = 0
        visual.barrage_clock = 4.0
        visual.barrage_y_offset = 240
        visual.barrage_preview_messages = []
        visual.update = lambda: None
        visual._update_scene_state = lambda _now: (_ for _ in ()).throw(AssertionError("preview should not hide the island"))
        calls: list[float] = []
        visual._sync_barrage_overlay = lambda now: calls.append(float(now))

        visual.show_barrage_preview()

        self.assertEqual(len(visual.barrage_preview_messages), 1)
        self.assertEqual(len(calls), 1)
        self.assertGreater(getattr(visual, "barrage_preview_force_anytime_until", 0.0), visual.last_barrage_preview_at)

    def test_barrage_preview_first_frame_is_already_visible_on_screen(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.settings_window_active = False
        visual.last_barrage_preview_at = -100.0
        visual.sample_index = 0
        visual.barrage_clock = 20.0
        visual.barrage_y_offset = 40
        visual.barrage_font_size = 12
        visual.barrage_speed = 100
        visual.barrage_use_backdrop = True
        visual.barrage_show_icon = True
        visual.fullscreen_plugin_barrage_messages = []
        visual.messages = []
        visual.barrage_preview_messages = []
        visual._u = lambda value: float(value)
        visual._font = lambda size, weight=QFont.Weight.Normal: QFont("Arial", int(size), int(weight))
        visual._barrage_travel_distance = lambda content_width, viewport=None: float((viewport or QRectF(0, 0, 640, 220)).width()) + float(content_width) + 140.0
        visual._sync_barrage_overlay = lambda _now: None
        visual.update = lambda: None
        painted: list[QRectF] = []
        visual._paint_barrage_text = lambda _painter, rect, *_args: painted.append(QRectF(rect))

        visual.show_barrage_preview()
        image = QImage(640, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        try:
            rects = visual._paint_barrage_scene(painter, QRectF(0.0, 0.0, 640.0, 220.0))
        finally:
            painter.end()

        self.assertTrue(rects)
        self.assertTrue(painted)
        self.assertLess(painted[0].left(), 640.0)
        self.assertGreater(painted[0].right(), 0.0)

    def test_fullscreen_plugin_alert_position_offsets_card_rect(self):
        overlay = FullscreenPluginAlertOverlay.__new__(FullscreenPluginAlertOverlay)
        owner = IslandStackVisual.__new__(IslandStackVisual)
        owner.plugin_fullscreen_alert_x_offset = 80
        owner.plugin_fullscreen_alert_y_offset = 120
        owner._u = lambda value: float(value)
        overlay.owner = owner
        overlay.width = lambda: 1000
        overlay.height = lambda: 700

        baseline_owner = IslandStackVisual.__new__(IslandStackVisual)
        baseline_owner.plugin_fullscreen_alert_x_offset = 0
        baseline_owner.plugin_fullscreen_alert_y_offset = 0
        baseline_owner._u = lambda value: float(value)
        baseline = FullscreenPluginAlertOverlay.__new__(FullscreenPluginAlertOverlay)
        baseline.owner = baseline_owner
        baseline.width = lambda: 1000
        baseline.height = lambda: 700

        moved = overlay._card_rect("island")
        original = baseline._card_rect("island")

        self.assertAlmostEqual(moved.left() - original.left(), 80.0, delta=0.01)
        self.assertAlmostEqual(moved.top() - original.top(), 120.0, delta=0.01)

    def test_plugin_fullscreen_alert_preview_works_without_real_due_alert_or_fullscreen(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = False
        visual.scene_reason = ""
        visual.last_scene_probe_reason = ""
        visual.settings_window_active = False
        visual.plugin_fullscreen_alert_style = "toast"
        visual.plugin_fullscreen_alert_x_offset = 0
        visual.plugin_fullscreen_alert_y_offset = 0
        visual.fullscreen_plugin_barrage_messages = []
        visual.fullscreen_plugin_barrage_alert_key = ""
        visual.barrage_preview_messages = []
        visual.messages = []
        visual.sample_index = 0
        visual.barrage_clock = 8.0
        visual._active_plugin_alerts = lambda *_args, **_kwargs: []
        visual.update = lambda: None
        calls: list[float] = []
        visual._sync_fullscreen_plugin_alert_overlay = lambda now: calls.append(float(now))

        visual.show_plugin_fullscreen_alert_preview()
        state = visual._fullscreen_plugin_alert_overlay_state()

        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(state)
        self.assertEqual(state["style"], "toast")
        self.assertEqual(state["action_key"], "plugin-alert:time")

    def test_plugin_fullscreen_alert_barrage_preview_starts_visible(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = False
        visual.scene_reason = ""
        visual.last_scene_probe_reason = ""
        visual.settings_window_active = False
        visual.plugin_fullscreen_alert_style = "barrage"
        visual.fullscreen_plugin_barrage_messages = []
        visual.fullscreen_plugin_barrage_alert_key = ""
        visual.sample_index = 0
        visual.barrage_clock = 8.0
        visual._active_plugin_alerts = lambda *_args, **_kwargs: []
        visual.update = lambda: None
        visual._sync_fullscreen_plugin_alert_overlay = lambda _now: None

        with patch("local.island_stack_visual.time.monotonic", return_value=100.0):
            visual.show_plugin_fullscreen_alert_preview()
            messages = visual._fullscreen_plugin_alert_barrage_messages(repeat_count=3, now=100.0)

        self.assertEqual(len(messages), 3)
        self.assertLess(messages[0].barrage_started_at, visual.barrage_clock)
        self.assertEqual(messages[0].action_key, "plugin-alert:time")

    def test_settings_barrage_preview_does_not_trigger_scene_suppression(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.settings_window_active = True
        visual.last_barrage_preview_at = 10.0
        message = Message("Preview", "Barrage", "Position 240px", QColor(96, 186, 255), QColor(52, 118, 240), serial=1)
        visual.barrage_preview_messages = [message]

        with patch("local.island_stack_visual.time.monotonic", return_value=10.5):
            suppressed, reason = visual._detect_scene_suppression()

        self.assertFalse(suppressed)
        self.assertEqual(reason, "")

    def test_fullscreen_plugin_alert_uses_detected_fullscreen_even_when_scene_stays_visible(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = False
        visual.scene_reason = ""
        visual.last_scene_probe_reason = "fullscreen"
        visual.plugin_fullscreen_alert_style = "toast"
        alert = (
            0,
            "TIME",
            "0:00",
            "Countdown 0:00",
            QColor(255, 184, 0),
            "",
            "time",
            "done",
            "timer done",
            "click to review or clear",
            5,
        )
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [alert]

        state = visual._fullscreen_plugin_alert_overlay_state()

        self.assertEqual(state["style"], "toast")
        self.assertEqual(state["action_key"], "plugin-alert:time")

    def test_tasker_visible_fullscreen_barrage_alert_generates_messages(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = False
        visual.scene_reason = ""
        visual.last_scene_probe_reason = "fullscreen"
        visual.plugin_fullscreen_alert_style = "barrage"
        visual.fullscreen_plugin_barrage_alert_key = ""
        visual.fullscreen_plugin_barrage_messages = []
        visual.sample_index = 0
        visual.barrage_clock = 12.0
        alert = (
            0,
            "TIME",
            "0:00",
            "Countdown 0:00",
            QColor(255, 184, 0),
            "",
            "time",
            "done",
            "timer done",
            "click to review or clear",
            5,
        )
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [alert]

        visual._ensure_fullscreen_plugin_barrage_messages(12.0)

        self.assertEqual(len(visual.fullscreen_plugin_barrage_messages), 10)
        self.assertEqual(visual.fullscreen_plugin_barrage_messages[0].action_key, "plugin-alert:time")

    def test_fullscreen_plugin_barrage_does_not_pull_alerts_when_context_or_style_blocks(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.fullscreen_plugin_barrage_alert_key = "old"
        visual.fullscreen_plugin_barrage_messages = [object()]
        visual._fullscreen_overlay_context_active = lambda: False
        visual._fullscreen_plugin_alert_style = lambda: "barrage"
        visual._fullscreen_plugin_alerts = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("alerts should not be pulled when context is inactive")
        )

        visual._ensure_fullscreen_plugin_barrage_messages(12.0)

        self.assertEqual(visual.fullscreen_plugin_barrage_alert_key, "")
        self.assertEqual(visual.fullscreen_plugin_barrage_messages, [])

        visual.fullscreen_plugin_barrage_alert_key = "old"
        visual.fullscreen_plugin_barrage_messages = [object()]
        visual._fullscreen_overlay_context_active = lambda: True
        visual._fullscreen_plugin_alert_style = lambda: "toast"
        visual._fullscreen_plugin_alerts = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("alerts should not be pulled for non-barrage style")
        )

        visual._ensure_fullscreen_plugin_barrage_messages(12.0)

        self.assertEqual(visual.fullscreen_plugin_barrage_alert_key, "")
        self.assertEqual(visual.fullscreen_plugin_barrage_messages, [])

    def test_sync_barrage_overlay_generates_plugin_barrage_before_state_lookup(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = False
        visual.scene_reason = ""
        visual.last_scene_probe_reason = "fullscreen"
        visual.settings_window_active = False
        visual.plugin_fullscreen_alert_style = "barrage"
        visual.fullscreen_plugin_barrage_alert_key = ""
        visual.fullscreen_plugin_barrage_messages = []
        visual.barrage_preview_messages = []
        visual.messages = []
        visual.sample_index = 0
        visual.barrage_clock = 12.0
        visual._fullscreen_plugin_alert_screen_rect = lambda: QRectF(0.0, 0.0, 1920.0, 1080.0)
        alert = (
            0,
            "TIME",
            "0:00",
            "Countdown 0:00",
            QColor(255, 184, 0),
            "",
            "time",
            "done",
            "timer done",
            "click to review or clear",
            5,
        )
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [alert]

        configured: list[dict[str, object]] = []

        class FakeOverlay:
            def configure(self, state, _screen_rect, _now):
                configured.append(dict(state))

            def request_hide(self, _now):
                configured.append({"hidden": True})

        visual.barrage_overlay = FakeOverlay()

        visual._sync_barrage_overlay(12.0)

        self.assertEqual(len(visual.fullscreen_plugin_barrage_messages), 10)
        self.assertEqual(len(configured), 1)
        self.assertEqual(configured[0]["style"], "barrage")

    def test_barrage_preview_syncs_dedicated_barrage_overlay(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.settings_window_active = True
        visual.last_barrage_preview_at = -100.0
        visual.sample_index = 0
        visual.barrage_clock = 4.0
        visual.barrage_y_offset = 320
        visual.barrage_preview_messages = []
        visual.scene_checked_at = 0.0
        visual.update = lambda: None
        visual._update_scene_state = lambda _now: None
        calls: list[float] = []
        visual._sync_barrage_overlay = lambda now: calls.append(float(now))
        visual._sync_fullscreen_plugin_alert_overlay = lambda _now: (_ for _ in ()).throw(AssertionError("preview should not sync plugin alert overlay"))

        visual.show_barrage_preview()

        self.assertEqual(len(visual.barrage_preview_messages), 1)
        self.assertEqual(len(calls), 1)

    def test_barrage_and_plugin_alert_states_are_independent(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = False
        visual.scene_reason = ""
        visual.last_scene_probe_reason = "fullscreen"
        visual.settings_window_active = True
        visual.barrage_clock = 8.0
        preview = Message("Preview", "Barrage", "Position 300px", QColor(96, 186, 255), QColor(52, 118, 240), serial=1)
        preview.barrage_started_at = 8.0
        visual.barrage_preview_messages = [preview]
        visual.fullscreen_plugin_barrage_messages = []
        visual.messages = []
        visual.plugin_fullscreen_alert_style = "toast"
        alert = (0, "TIME", "0:00", "Countdown 0:00", QColor(255, 184, 0), "", "time", "done", "timer done", "click", 5)
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [alert]

        barrage_state = visual._barrage_overlay_state()
        plugin_state = visual._plugin_alert_overlay_state()

        self.assertEqual(barrage_state["style"], "barrage")
        self.assertEqual(plugin_state["style"], "toast")
        self.assertEqual(plugin_state["action_key"], "plugin-alert:time")

    def test_fullscreen_overlay_state_prioritizes_settings_barrage_preview(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = True
        visual.scene_reason = "settings"
        visual.last_scene_probe_reason = "settings"
        visual.settings_window_active = True
        visual.barrage_clock = 8.0
        message = Message("Preview", "Barrage", "Position 300px", QColor(96, 186, 255), QColor(52, 118, 240), serial=1)
        message.barrage_started_at = 8.0
        visual.barrage_preview_messages = [message]
        visual.fullscreen_plugin_barrage_messages = []
        visual.messages = []
        visual.plugin_fullscreen_alert_style = "toast"
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [
            (0, "TIME", "0:00", "Countdown 0:00", QColor(255, 184, 0), "", "time", "done", "timer done", "click", 5)
        ]

        state = visual._fullscreen_overlay_state()

        self.assertEqual(state["style"], "barrage")
        self.assertEqual(state["alert_key"], "preview-barrage:1")

    def test_scene_suppressed_tick_syncs_fullscreen_plugin_overlay(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = True
        visual.hovered_key = "plugin"
        visual.hover_armed_key = "plugin"
        visual.left_stack_at = 1.0
        calls: list[tuple[str, float | None]] = []
        visual._ensure_fullscreen_plugin_barrage_messages = lambda now: calls.append(("ensure", now))
        visual._sync_fullscreen_plugin_alert_overlay = lambda now: calls.append(("sync", now))
        visual.update = lambda: calls.append(("update", None))

        visual._handle_scene_suppressed_tick(12.0)

        self.assertIn(("ensure", 12.0), calls)
        self.assertIn(("sync", 12.0), calls)
        self.assertIn(("update", None), calls)
        self.assertLess(calls.index(("ensure", 12.0)), calls.index(("sync", 12.0)))
        self.assertLess(calls.index(("sync", 12.0)), calls.index(("update", None)))
        self.assertEqual(visual.hovered_key, "")
        self.assertEqual(visual.hover_armed_key, "")
        self.assertEqual(visual.left_stack_at, 0.0)

    def test_fullscreen_plugin_alert_overlay_paints_on_its_own_screen_sized_viewport(self):
        source = inspect.getsource(FullscreenPluginAlertOverlay.paintEvent)

        self.assertIn("_paint_barrage_scene", source)
        self.assertIn("QRectF(0.0, 0.0, float(self.width()), float(self.height()))", source)

    def test_fullscreen_plugin_alert_overlay_masks_only_clickable_notification_regions(self):
        source = inspect.getsource(FullscreenPluginAlertOverlay._apply_hit_mask)

        self.assertIn("setMask", source)
        self.assertIn("WA_TransparentForMouseEvents", source)

    def test_lyric_display_lines_preserve_explicit_newline(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True

        lines = visual._lyric_display_lines("第一句歌词\n第二句歌词")

        self.assertEqual(lines, ["第一句歌词", "第二句歌词"])

    def test_lyric_display_lines_collapse_dual_line_when_disabled(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = False

        lines = visual._lyric_display_lines("English lyric\n中文翻译")

        self.assertEqual(lines, ["English lyric"])

    def test_tasker_mode_collapses_dual_language_lyric_lines(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True
        visual.location_mode = "tasker"

        wave_lines = visual._lyric_display_lines("English lyric line\n中文翻译歌词")

        self.assertEqual(wave_lines, ["English lyric line"])

        visual.lyric_flow_style = "gelv_songscatter"
        visual.lyrics = ["A prayer for you my friend 为你祈祷我的朋友", "下一句歌词"]
        visual.lyric_index = 0

        self.assertEqual(visual._gelv_display_lines(), ["A prayer for you my friend 为你祈祷我的朋友", "下一句歌词"])

    def test_stick_mode_preserves_dual_language_lyric_lines(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True
        visual.location_mode = "stick"

        lines = visual._lyric_display_lines("English lyric line\n涓枃缈昏瘧姝岃瘝")

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "English lyric line")

    def test_top_mode_preserves_dual_language_lyric_lines(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True
        visual.location_mode = "top"

        lines = visual._lyric_display_lines("English lyric line\nSecond lyric line")

        self.assertEqual(lines, ["English lyric line", "Second lyric line"])

    def test_gelv_edge_notch_height_modes_control_next_line_visibility(self):
        for mode in ("stick", "top"):
            for height_mode, expected in (
                ("compact", ["Current main lyric"]),
                ("standard", ["Current main lyric", "Next lyric should be visible"]),
                ("spacious", ["Current main lyric", "Next lyric should be visible"]),
            ):
                with self.subTest(mode=mode, height_mode=height_mode):
                    visual = IslandStackVisual.__new__(IslandStackVisual)
                    visual.location_mode = mode
                    visual.top_notch_height_mode = height_mode
                    visual.lyric_flow_style = "gelv_songscatter"
                    visual.enable_dual_line_lyrics = False
                    visual.lyrics = ["Current main lyric", "Next lyric should be visible"]
                    visual.lyric_index = 0

                    self.assertEqual(visual._gelv_display_lines(), expected)

    def test_gelv_stick_and_top_dual_line_use_current_explicit_second_line(self):
        for mode in ("stick", "top"):
            with self.subTest(mode=mode):
                visual = IslandStackVisual.__new__(IslandStackVisual)
                visual.location_mode = mode
                visual.lyric_flow_style = "gelv_songscatter"
                visual.enable_dual_line_lyrics = True
                visual.lyrics = ["Current main lyric\nCurrent second lyric", "Next lyric should stay hidden"]
                visual.lyric_index = 0

                self.assertEqual(visual._gelv_display_lines(), ["Current main lyric", "Current second lyric"])

    def test_tasker_gelv_compact_two_line_rects_stay_visible(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.lyric_flow_style = "gelv_songscatter"
        visual.lyrics = ["First visible line", "Second visible line"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.lyric_changed_at = time.monotonic()
        visual.enable_dual_line_lyrics = False
        visual.font_shrink_scale = 1.0
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 13
        visual.gelv_dual_line_font_size = 18
        visual.gelv_initial_word_spacing = 168
        visual.gelv_word_spacing = 248
        visual._lyric_layout_cache = {}

        clip = QRectF(0.0, 0.0, 220.0, 28.0)
        rows = visual._gelv_line_rects(clip, expanded=False, now=time.monotonic())

        self.assertEqual(len(rows), 2)
        self.assertGreaterEqual(rows[0]["rect"].top(), clip.top())
        self.assertLessEqual(rows[1]["rect"].bottom(), clip.bottom())
        self.assertGreater(rows[0]["size"], rows[1]["size"] + 6.0)
        self.assertGreater(rows[0]["opacity"], rows[1]["opacity"] + 0.20)

    def test_gelv_edge_notch_line_switch_keeps_next_line_motion(self):
        for mode in ("stick", "top"):
            with self.subTest(mode=mode):
                visual = IslandStackVisual.__new__(IslandStackVisual)
                visual.location_mode = mode
                visual.top_notch_height_mode = "standard"
                visual.lyric_flow_style = "gelv_songscatter"
                visual.enable_dual_line_lyrics = False
                visual.lyric_changed_at = 10.0
                visual.prev_lyric_index = 0
                visual.lyric_index = 1
                visual.lyrics = ["first line", "second line", "third line"]
                visual.gelv_primary_font_size = 24
                visual.gelv_secondary_font_size = 12
                rect = QRectF(0.0, 0.0, 300.0, 58.0)

                final_rows = visual._gelv_line_rects(rect, expanded=False, now=10.7)
                moving_rows = visual._gelv_line_rects(rect, expanded=False, now=10.08)
                final_texts = [row["text"] for row in final_rows]
                moving_texts = [row["text"] for row in moving_rows]
                self.assertIn("third line", final_texts)
                self.assertIn("third line", moving_texts)
                final_current = next(row for row in final_rows if row["text"] == "second line" and not row.get("transition_out"))
                final_next = next(row for row in final_rows if row["text"] == "third line")
                moving_current = next(row for row in moving_rows if row["text"] == "second line" and not row.get("transition_out"))

                self.assertGreater(moving_current["rect"].top(), final_current["rect"].top() + 1.0)
                self.assertLess(moving_current["rect"].top(), final_next["rect"].top())

    def test_lyric_display_lines_avoid_short_slash_split(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True

        lines = visual._lyric_display_lines("A/B test")

        self.assertEqual(lines, ["A/B test"])

    def test_dual_line_secondary_settings_are_saved_in_snapshot(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = True
        visual.music_auto_detect = False
        visual.persistent_plugin = True
        visual.finder_mode = False
        visual.manual_finder_available = False
        visual.expanded = False
        visual.show_seconds = True
        visual.enabled_plugin_count = 3
        visual.ambient_intensity = 0.0
        visual.music_ambience_style = "sea_level"
        visual.music_flow_intensity = 0.74
        visual.compact_duotone_balance = 50
        visual.expanded_duotone_balance = 50
        visual.ui_font_family = "PingFang SC"
        visual.high_quality_font_rendering = False
        visual.animation_fps = 60
        visual.animation_speed = 1.0
        visual.empty_bar_palette = "aurora"
        visual.empty_bar_width = 92
        visual.empty_bar_height = 14
        visual.empty_bar_y_offset = 0
        visual.empty_bar_opacity = 100
        visual.empty_bar_custom_colors = ("#46D6FF", "#73FFA6", "#FF5A86")
        visual.stack_center_ratio = 0.5
        visual.saved_window_pos = None
        visual.stack_direction = 1
        visual.barrage_y_offset = 0
        visual.barrage_font_size = 10
        visual.barrage_speed = 100
        visual.barrage_use_backdrop = True
        visual.barrage_show_icon = True
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.lyric_offset_seconds = 0.0
        visual.lyric_font_size = 18
        visual.lyric_elastic_strength = 100
        visual.lyric_elastic_mode = "both"
        visual.lyric_word_spacing = 100
        visual.enable_dual_line_lyrics = True
        visual.dual_line_secondary_weight = 88
        visual.dual_line_secondary_spacing = 124
        visual.no_lyrics_text = "暂无歌词"
        visual.super_topmost_enabled = True

        snapshot = visual.settings_snapshot()

        self.assertEqual(snapshot["v2_dual_line_secondary_weight"], 88)
        self.assertEqual(snapshot["v2_dual_line_secondary_spacing"], 124)
        self.assertTrue(snapshot["v2_super_topmost"])

    def test_lyric_flow_settings_are_saved_in_snapshot(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = True
        visual.music_auto_detect = False
        visual.persistent_plugin = True
        visual.finder_mode = False
        visual.manual_finder_available = False
        visual.expanded = False
        visual.show_seconds = True
        visual.enabled_plugin_count = 3
        visual.ambient_intensity = 0.0
        visual.music_ambience_style = "sea_level"
        visual.music_flow_intensity = 0.74
        visual.compact_duotone_balance = 50
        visual.expanded_duotone_balance = 50
        visual.ui_font_family = "PingFang SC"
        visual.high_quality_font_rendering = False
        visual.animation_fps = 60
        visual.animation_speed = 1.0
        visual.empty_bar_palette = "aurora"
        visual.empty_bar_width = 92
        visual.empty_bar_height = 14
        visual.empty_bar_y_offset = 0
        visual.empty_bar_opacity = 100
        visual.empty_bar_custom_colors = ("#46D6FF", "#73FFA6", "#FF5A86")
        visual.stack_center_ratio = 0.5
        visual.saved_window_pos = None
        visual.stack_direction = 1
        visual.barrage_y_offset = 0
        visual.barrage_font_size = 10
        visual.barrage_speed = 100
        visual.barrage_use_backdrop = True
        visual.barrage_show_icon = True
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.lyric_offset_seconds = 0.0
        visual.lyric_font_size = 18
        visual.lyric_elastic_strength = 100
        visual.lyric_elastic_mode = "both"
        visual.lyric_word_spacing = 100
        visual.wave_word_fill_enabled = True
        visual.wave_color_fill_enabled = True
        visual.enable_dual_line_lyrics = False
        visual.dual_line_secondary_weight = 72
        visual.dual_line_secondary_spacing = 100
        visual.no_lyrics_text = "暂无歌词"
        visual.super_topmost_enabled = True
        visual.lyric_flow_style = "gelv_songscatter"
        visual.gelv_font_family = "PingFang SC"
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 13
        visual.gelv_dual_line_font_size = 18
        visual.gelv_initial_word_spacing = 168
        visual.gelv_word_spacing = 248
        visual.gelv_color_fill_enabled = True
        visual.gelv_word_fill_enabled = True
        visual.wave_font_weight = "heavy"
        visual.ember_word_fill_enabled = True
        visual.ember_color_fill_enabled = True
        visual.ember_font_weight = "bold"
        visual.ember_font_size = 19
        visual.ember_back_gray_mix = 64
        visual.ember_color_back_gray_mix = 27
        visual.ember_back_opacity = 42
        visual.ember_blur_panel_strength = 37
        visual.ember_lyric_offset_seconds = 0.35
        visual.no_lyrics_display_mode = "album_island"
        visual.album_island_cover_count = 7
        visual.album_island_cover_size = 34
        visual.album_island_compact_cover_size = 28
        visual.album_island_expanded_cover_size = 72
        visual.album_island_spacing = 140
        visual.album_island_secondary_style = "perspective_dim"
        visual.album_island_perspective_amount = 135
        visual.listened_seconds_total = 7260.0
        visual.album_history = [{"track_id": "a", "title": "A", "artist": "B"}]
        visual.compact_lyric_show_cover = False

        snapshot = visual.settings_snapshot()

        self.assertEqual(snapshot["v2_lyric_flow_style"], "gelv_songscatter")
        self.assertEqual(snapshot["v2_gelv_font_family"], "Noto Serif SC ExtraBold")
        self.assertEqual(snapshot["v2_gelv_primary_font_size"], 24)
        self.assertEqual(snapshot["v2_gelv_secondary_font_size"], 13)
        self.assertEqual(snapshot["v2_gelv_dual_line_font_size"], 18)
        self.assertEqual(snapshot["v2_gelv_initial_word_spacing"], 168)
        self.assertEqual(snapshot["v2_gelv_word_spacing"], 248)
        self.assertNotIn("v2_gelv_size_shift_enabled", snapshot)
        self.assertTrue(snapshot["v2_gelv_color_fill_enabled"])
        self.assertTrue(snapshot["v2_gelv_word_fill_enabled"])
        self.assertNotIn("v2_gelv_color_fill_fallback", snapshot)
        self.assertEqual(snapshot["v2_wave_font_weight"], "heavy")
        self.assertTrue(snapshot["v2_ember_word_fill_enabled"])
        self.assertTrue(snapshot["v2_ember_color_fill_enabled"])
        self.assertNotIn("v2_ember_color_fill_fallback", snapshot)
        self.assertEqual(snapshot["v2_ember_font_weight"], "bold")
        self.assertEqual(snapshot["v2_ember_font_size"], 19)
        self.assertEqual(snapshot["v2_ember_back_gray_mix"], 64)
        self.assertEqual(snapshot["v2_ember_color_back_gray_mix"], 27)
        self.assertEqual(snapshot["v2_ember_back_opacity"], 42)
        self.assertEqual(snapshot["v2_ember_blur_panel_strength"], 37)
        self.assertAlmostEqual(snapshot["v2_ember_lyric_offset_seconds"], 0.35, delta=0.001)
        self.assertNotIn("v2_wave_word_fill_enabled", snapshot)
        self.assertNotIn("v2_wave_color_fill_enabled", snapshot)
        self.assertNotIn("v2_lyric_elastic_strength", snapshot)
        self.assertNotIn("v2_lyric_elastic_mode", snapshot)
        self.assertNotIn("v2_lyric_word_spacing", snapshot)
        self.assertNotIn("v2_wave_color_fill_fallback", snapshot)
        self.assertEqual(snapshot["v2_no_lyrics_display_mode"], "album_island")
        self.assertEqual(snapshot["v2_album_island_cover_count"], 7)
        self.assertEqual(snapshot["v2_album_island_cover_size"], 34)
        self.assertEqual(snapshot["v2_album_island_compact_cover_size"], 28)
        self.assertEqual(snapshot["v2_album_island_expanded_cover_size"], 72)
        self.assertEqual(snapshot["v2_album_island_spacing"], 140)
        self.assertEqual(snapshot["v2_album_island_secondary_style"], "perspective_dim")
        self.assertEqual(snapshot["v2_album_island_perspective_amount"], 135)
        self.assertEqual(snapshot["v2_album_island_listened_seconds_total"], 7260.0)
        self.assertEqual(snapshot["v2_album_island_history"][0]["track_id"], "a")
        self.assertFalse(snapshot["v2_compact_lyric_show_cover"])

    def test_location_tasker_settings_are_saved_in_snapshot(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = True
        visual.music_auto_detect = False
        visual.persistent_plugin = True
        visual.finder_mode = False
        visual.manual_finder_available = False
        visual.expanded = False
        visual.show_seconds = True
        visual.enabled_plugin_count = 3
        visual.ambient_intensity = 0.0
        visual.music_ambience_style = "sea_level"
        visual.music_flow_intensity = 0.74
        visual.compact_duotone_balance = 50
        visual.expanded_duotone_balance = 50
        visual.ui_font_family = "PingFang SC"
        visual.high_quality_font_rendering = False
        visual.animation_fps = 60
        visual.animation_speed = 1.0
        visual.empty_bar_palette = "aurora"
        visual.empty_bar_width = 92
        visual.empty_bar_height = 14
        visual.empty_bar_y_offset = 0
        visual.empty_bar_opacity = 100
        visual.empty_bar_custom_colors = ("#46D6FF", "#73FFA6", "#FF5A86")
        visual.stack_center_ratio = 0.5
        visual.saved_window_pos = None
        visual.stack_direction = 1
        visual.barrage_y_offset = 0
        visual.barrage_font_size = 10
        visual.barrage_speed = 100
        visual.barrage_use_backdrop = True
        visual.barrage_show_icon = True
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.lyric_offset_seconds = 0.0
        visual.lyric_font_size = 18
        visual.lyric_elastic_strength = 100
        visual.lyric_elastic_mode = "both"
        visual.lyric_word_spacing = 100
        visual.wave_color_fill_enabled = False
        visual.enable_dual_line_lyrics = False
        visual.dual_line_secondary_weight = 72
        visual.dual_line_secondary_spacing = 100
        visual.no_lyrics_text = "暂无歌词"
        visual.super_topmost_enabled = True
        visual.lyric_flow_style = "wave_stream"
        visual.gelv_font_family = "Source Han Serif SC"
        visual.gelv_primary_font_size = 22
        visual.gelv_secondary_font_size = 12
        visual.gelv_dual_line_font_size = 17
        visual.gelv_initial_word_spacing = 220
        visual.gelv_word_spacing = 320
        visual.gelv_color_fill_enabled = False
        visual.gelv_word_fill_enabled = False
        visual.no_lyrics_display_mode = "text"
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_island_secondary_style = "perspective_dim"
        visual.album_island_perspective_amount = 100
        visual.compact_lyric_show_cover = True
        visual.location_mode = "tasker"
        visual.taskbar_fusion_mode = "compact"
        visual.taskbar_recognition_threshold = 0.66
        visual.taskbar_corner_style = "win11"
        visual.taskbar_plugin_compact_style = "block"
        visual.taskbar_preview_visible = True
        visual.dual_line_font_size = 13

        snapshot = visual.settings_snapshot()

        self.assertEqual(snapshot["v2_location_mode"], "tasker")
        self.assertEqual(snapshot["v2_taskbar_corner_style"], "win11")
        self.assertEqual(snapshot["v2_dual_line_font_size"], 13)
        self.assertNotIn("v2_taskbar_recognition_threshold", snapshot)
        self.assertNotIn("v2_taskbar_plugin_compact_style", snapshot)
        self.assertNotIn("v2_taskbar_message_compact_style", snapshot)
        self.assertNotIn("v2_taskbar_fusion_mode", snapshot)
        self.assertNotIn("v2_taskbar_hint_text", snapshot)
        self.assertEqual(snapshot["v2_taskbar_auto_hide_mode"], "auto")

    def test_taskbar_attach_hot_zone_is_centered_on_screen(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)

        zone = visual._taskbar_attach_hot_zone_for_screen(FakeScreen())

        self.assertFalse(zone.isNull())
        self.assertTrue(zone.contains(QPointF(960, 540)))
        self.assertLess(zone.width(), 720.0)
        self.assertLess(zone.height(), 220.0)

    def test_taskbar_attach_hot_zone_accepts_main_island_rect_not_pointer(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        zone = visual._taskbar_attach_hot_zone_for_screen(FakeScreen())
        outside_main = QRectF(100.0, 100.0, 420.0, 48.0)
        inside_main = QRectF(750.0, 516.0, 420.0, 48.0)

        self.assertFalse(visual._taskbar_attach_hot_zone_accepts_rect(zone, outside_main))
        self.assertTrue(visual._taskbar_attach_hot_zone_accepts_rect(zone, inside_main))

    def test_taskbar_preview_shows_autohide_prompt_but_arms_only_when_main_island_enters_box(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = False
        visual.manual_finder_available = False
        visual.location_mode = "free"
        visual.taskbar_preview_visible = False
        visual.taskbar_selected_drop_zone = QRectF()
        visual.taskbar_drop_zones = []
        visual.taskbar_drop_overlay = None
        visual.taskbar_attach_hot_zone_active = False
        visual._u = lambda value: float(value)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: True
        visual._taskbar_task_area_rect_for_screen = lambda _screen, _taskbar: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_occupied_rects_for_screen = lambda _screen, _taskbar: []
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._hide_taskbar_drop_overlay = lambda: None
        visual._sync_taskbar_drop_overlay = lambda: None
        visual._current_main_target_global_rect = lambda: QRectF(100.0, 100.0, 420.0, 48.0)

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            visual._update_taskbar_preview_for_drag(QPoint(960, 540))

        self.assertTrue(visual.taskbar_preview_visible)
        self.assertFalse(visual.taskbar_attach_hot_zone.isNull())
        self.assertTrue(visual.taskbar_attach_hot_zone.contains(QPointF(960, 540)))
        self.assertFalse(visual.taskbar_attach_hot_zone_active)
        self.assertEqual(visual.taskbar_drop_zones, [])

        visual._current_main_target_global_rect = lambda: QRectF(750.0, 516.0, 420.0, 48.0)
        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            visual._update_taskbar_preview_for_drag(QPoint(960, 1072))

        self.assertTrue(visual.taskbar_preview_visible)
        self.assertTrue(visual.taskbar_attach_hot_zone_active)
        self.assertTrue(visual.taskbar_drop_zones)

    def test_taskbar_preview_hides_center_prompt_when_taskbar_autohide_disabled(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = False
        visual.manual_finder_available = False
        visual.location_mode = "free"
        visual.taskbar_preview_visible = True
        visual.taskbar_selected_drop_zone = QRectF(100.0, 1044.0, 320.0, 32.0)
        visual.taskbar_drop_zones = [QRectF(100.0, 1044.0, 320.0, 32.0)]
        visual.taskbar_drop_overlay = None
        visual.taskbar_attach_hot_zone_active = True
        visual._u = lambda value: float(value)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: False
        visual._hide_taskbar_drop_overlay = lambda: None
        visual._sync_taskbar_drop_overlay = lambda: None
        visual._current_main_target_global_rect = lambda: QRectF(750.0, 516.0, 420.0, 48.0)

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            visual._update_taskbar_preview_for_drag(QPoint(960, 540))

        self.assertFalse(visual.taskbar_preview_visible)
        self.assertTrue(visual.taskbar_attach_hot_zone.isNull())
        self.assertFalse(visual.taskbar_attach_hot_zone_active)
        self.assertEqual(visual.taskbar_drop_zones, [])

    def test_taskbar_preview_keeps_center_prompt_when_autohide_taskbar_rect_is_hidden(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_preview_visible = False
        visual.taskbar_selected_drop_zone = QRectF()
        visual.taskbar_drop_zones = []
        visual.taskbar_drop_overlay = None
        visual.taskbar_attach_hot_zone_active = False
        visual.last_taskbar_rect = QRectF()
        visual._u = lambda value: float(value)
        visual._taskbar_allowed = lambda: True
        visual._taskbar_rect_for_screen = lambda _screen: None
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: True
        visual._taskbar_hidden_fallback_rect_for_screen = lambda _screen: QRectF(0.0, 1032.0, 1920.0, 48.0)
        visual._taskbar_task_area_rect_for_screen = lambda _screen, _taskbar: QRectF()
        visual._taskbar_occupied_rects_for_screen = lambda _screen, _taskbar: []
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._hide_taskbar_drop_overlay = lambda: None
        visual._sync_taskbar_drop_overlay = lambda: None
        visual._current_main_target_global_rect = lambda: QRectF(750.0, 516.0, 420.0, 48.0)

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            visual._update_taskbar_preview_for_drag(QPoint(960, 540))

        self.assertTrue(visual.taskbar_preview_visible)
        self.assertFalse(visual.taskbar_attach_hot_zone.isNull())
        self.assertEqual(visual.taskbar_attach_hot_zone_host_rect, QRectF(0.0, 0.0, 1920.0, 1080.0))
        self.assertTrue(visual.taskbar_attach_hot_zone_active)
        self.assertTrue(visual.taskbar_drop_zones)
        self.assertEqual(visual.last_taskbar_rect, QRectF(0.0, 1032.0, 1920.0, 48.0))

    def test_taskbar_auto_hide_setting_overrides_detection(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: {"auto_hide": False, "rect": QRectF(), "edge": ""}

        visual.taskbar_auto_hide_mode = "enabled"
        self.assertTrue(visual._taskbar_auto_hide_enabled_for_screen(object()))

        visual.taskbar_auto_hide_mode = "disabled"
        self.assertFalse(visual._taskbar_auto_hide_enabled_for_screen(object()))

    def test_mydockfinder_availability_suppresses_taskbar_autohide_recognition(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_auto_hide_mode = "enabled"
        visual.mydockfinder_running = True
        visual.manual_finder_available = False

        self.assertFalse(visual._taskbar_auto_hide_enabled_for_screen(object()))

        visual.mydockfinder_running = False
        visual.manual_finder_available = True

        self.assertFalse(visual._taskbar_auto_hide_enabled_for_screen(object()))

    def test_taskbar_preview_hides_when_finder_or_manual_mydockfinder_is_enabled(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = True
        visual.manual_finder_available = False
        visual.location_mode = "finder"
        visual.taskbar_fusion_mode = "off"
        visual.taskbar_preview_visible = False
        visual.taskbar_recognition_threshold = 0.66

        self.assertFalse(visual._taskbar_allowed())
        visual.finder_mode = False
        visual.location_mode = "free"
        visual.manual_finder_available = True

        self.assertFalse(visual._taskbar_allowed())

    def test_accepting_taskbar_drop_sets_tasker_location_mode(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = False
        visual.manual_finder_available = False
        visual.location_mode = "free"
        visual.taskbar_fusion_mode = "off"
        visual.taskbar_preview_visible = True
        visual.taskbar_attach_hot_zone_active = True
        visual.expanded = False
        visual.plugin_panel_open = False
        dock_calls: list[str] = []
        visual._dock_to_taskbar_task_area = lambda: dock_calls.append("dock") or True
        visual._pulse_stack = lambda: None
        visual.update = lambda: None

        self.assertTrue(visual._accept_taskbar_preview_drop())
        self.assertEqual(visual.location_mode, "tasker")
        self.assertEqual(visual.taskbar_fusion_mode, "compact")
        self.assertEqual(dock_calls, ["dock"])

    def test_accepting_taskbar_drop_requires_main_island_inside_prompt_box(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = False
        visual.manual_finder_available = False
        visual.location_mode = "free"
        visual.taskbar_fusion_mode = "off"
        visual.taskbar_preview_visible = True
        visual.taskbar_attach_hot_zone_active = False
        visual.expanded = False
        visual.plugin_panel_open = False
        dock_calls: list[str] = []
        visual._dock_to_taskbar_task_area = lambda: dock_calls.append("dock") or True

        self.assertFalse(visual._accept_taskbar_preview_drop())

        self.assertEqual(visual.location_mode, "free")
        self.assertEqual(dock_calls, [])

    def test_accepting_taskbar_drop_requests_animated_dock_and_clears_center_hint(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = False
        visual.manual_finder_available = False
        visual.location_mode = "free"
        visual.taskbar_fusion_mode = "off"
        visual.taskbar_preview_visible = True
        visual.taskbar_attach_hot_zone_active = True
        visual.taskbar_attach_hot_zone = QRectF(760.0, 450.0, 400.0, 160.0)
        visual.expanded = False
        visual.plugin_panel_open = False
        dock_flags: list[bool] = []
        visual._dock_to_taskbar_task_area = lambda: dock_flags.append(bool(getattr(visual, "taskbar_dock_animation_requested", False))) or True
        visual._pulse_stack = lambda: None
        visual.update = lambda: None
        hide_calls: list[str] = []
        visual._hide_taskbar_drop_overlay = lambda: hide_calls.append("hide")

        self.assertTrue(visual._accept_taskbar_preview_drop())

        self.assertEqual(dock_flags, [True])
        self.assertFalse(getattr(visual, "taskbar_dock_animation_requested", True))
        self.assertTrue(visual.taskbar_attach_hot_zone.isNull())
        self.assertIn("hide", hide_calls)

    def test_tasker_location_reverts_to_free_when_leaving_taskbar_zone(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = False
        visual.manual_finder_available = False
        visual.location_mode = "tasker"
        visual.taskbar_fusion_mode = "compact"
        visual.taskbar_preview_visible = True
        visual._pulse_stack = lambda: None
        visual.update = lambda: None

        visual._set_taskbar_presence(False, notify=False)

        self.assertEqual(visual.location_mode, "free")
        self.assertEqual(visual.taskbar_fusion_mode, "off")
        self.assertFalse(visual.taskbar_preview_visible)

    def test_taskbar_drop_zone_rect_uses_taskbar_bounds(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)

        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        zone = visual._taskbar_drop_zone_rect(taskbar, desired_width=360.0)

        self.assertGreaterEqual(zone.top(), taskbar.top())
        self.assertLessEqual(zone.bottom(), taskbar.bottom())
        self.assertAlmostEqual(zone.center().x(), taskbar.center().x(), delta=0.1)

    def test_taskbar_drop_zone_prefers_detected_task_area(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        screen = object()
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        task_area = QRectF(180.0, 1040.0, 1320.0, 40.0)
        visual._taskbar_rect_for_screen = lambda _screen: taskbar
        visual._taskbar_task_area_rect_for_screen = lambda _screen, _taskbar: task_area

        zones = visual._taskbar_drop_zones_for_screen(screen, desired_width=360.0)

        self.assertEqual(len(zones), 2)
        self.assertTrue(all(zone.left() >= task_area.left() and zone.right() <= task_area.right() for zone in zones))
        self.assertLess(zones[0].center().x(), task_area.center().x())
        self.assertGreater(zones[1].center().x(), task_area.center().x())

    def test_taskbar_drop_zones_use_left_and_right_available_slots(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        screen = object()
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_rect_for_screen = lambda _screen: taskbar
        visual._taskbar_task_area_rect_for_screen = lambda _screen, _taskbar: taskbar
        visual._taskbar_occupied_rects_for_screen = lambda _screen, _taskbar: [
            QRectF(0.0, 1040.0, 170.0, 40.0),
            QRectF(640.0, 1040.0, 710.0, 40.0),
            QRectF(1810.0, 1040.0, 110.0, 40.0),
        ]

        zones = visual._taskbar_drop_zones_for_screen(screen, desired_width=360.0)

        self.assertEqual(len(zones), 2)
        self.assertLess(zones[0].center().x(), taskbar.center().x())
        self.assertGreater(zones[1].center().x(), taskbar.center().x())

    def test_taskbar_drop_zones_split_empty_taskbar_into_left_and_right_not_center(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        screen = object()
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_rect_for_screen = lambda _screen: taskbar
        visual._taskbar_task_area_rect_for_screen = lambda _screen, _taskbar: taskbar
        visual._taskbar_occupied_rects_for_screen = lambda _screen, _taskbar: []

        zones = visual._taskbar_drop_zones_for_screen(screen, desired_width=360.0)

        self.assertEqual(len(zones), 2)
        self.assertLess(zones[0].center().x(), taskbar.center().x() - 120.0)
        self.assertGreater(zones[1].center().x(), taskbar.center().x() + 120.0)
        self.assertFalse(any(abs(zone.center().x() - taskbar.center().x()) < 80.0 for zone in zones))

    def test_taskbar_drop_zone_selection_uses_drag_position(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        zones = [QRectF(190.0, 1044.0, 360.0, 32.0), QRectF(1460.0, 1044.0, 320.0, 32.0)]

        selected = visual._select_taskbar_drop_zone(zones, QPoint(1580, 1060))

        self.assertEqual(selected, zones[1])

    def test_tasker_track_drag_updates_position_ratio_within_taskbar_bounds(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        track = QRectF(180.0, 1040.0, 1500.0, 40.0)

        left_ratio = visual._taskbar_position_ratio_for_center(track, 280.0, 100.0)
        right_ratio = visual._taskbar_position_ratio_for_center(track, 280.0, 2000.0)
        middle_ratio = visual._taskbar_position_ratio_for_center(track, 280.0, 930.0)

        self.assertEqual(left_ratio, 0.0)
        self.assertEqual(right_ratio, 1.0)
        self.assertAlmostEqual(middle_ratio, 0.5, delta=0.02)

    def test_tasker_hit_test_uses_selected_drop_zone_as_drag_grab_area(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.taskbar_selected_drop_zone = QRectF(100.0, 1040.0, 360.0, 34.0)
        visual.taskbar_left_handle_rect = QRectF()
        visual.music_button_rects = {}
        visual.motions = {}
        visual._u = lambda value: float(value)
        visual._taskbar_left_handle_visible = lambda: False
        visual.mapToGlobal = lambda _point: QPoint(40, 1000)
        visual._build_visuals = lambda: []

        self.assertEqual(visual._hit_test(QPointF(220.0, 56.0)), "music")

    def test_tasker_left_resize_handle_is_small_rounded_vertical_rail(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        main_rect = QRectF(520.0, 1044.0, 360.0, 34.0)

        handle = visual._taskbar_left_handle_rect(main_rect)

        self.assertLess(handle.width(), handle.height())
        self.assertLessEqual(handle.right(), main_rect.left() - 3.0)
        self.assertLessEqual(handle.width(), 4.0)
        self.assertGreater(handle.height(), main_rect.height() * 0.45)

    def test_tasker_left_resize_handle_is_visible_only_on_tasker_hover_or_drag(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.hovered_key = ""
        visual.drag_press_key = ""
        visual.dragging = False

        self.assertFalse(visual._taskbar_left_handle_visible())

        visual.hovered_key = "music"
        self.assertTrue(visual._taskbar_left_handle_visible())

        visual.hovered_key = ""
        visual.dragging = True
        visual.drag_press_key = "taskbar:left-handle"
        self.assertTrue(visual._taskbar_left_handle_visible())

    def test_tasker_left_resize_handle_is_hidden_when_expanded(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = True
        visual.hovered_key = "music"
        visual.dragging = False
        visual.drag_press_key = ""
        visual.taskbar_left_handle_visible_until = time.monotonic() + 3.0

        self.assertFalse(visual._taskbar_left_handle_visible())

    def test_tasker_left_resize_handle_frame_matches_island_height(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        main_rect = QRectF(520.0, 1044.0, 360.0, 34.0)

        handle = visual._taskbar_left_handle_rect(main_rect)
        frame = visual._taskbar_left_handle_frame_rect(main_rect)

        self.assertAlmostEqual(frame.height(), main_rect.height(), delta=0.01)
        self.assertLessEqual(frame.left(), handle.left())
        self.assertGreaterEqual(frame.right(), handle.right())

    def test_tasker_left_resize_handle_frame_grows_from_island_edge(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        main_rect = QRectF(520.0, 1044.0, 360.0, 34.0)

        frame = visual._taskbar_left_handle_frame_rect(main_rect)

        self.assertLess(frame.left(), main_rect.left())
        self.assertGreater(frame.right(), main_rect.left())

    def test_tasker_left_resize_handle_is_painted_before_main_body(self):
        source = inspect.getsource(IslandStackVisual._paint_island_frame)

        handle_index = source.index("self._paint_taskbar_left_handle(painter, rect)")
        music_index = source.index("self._paint_music(painter, rect, self.expanded)")

        self.assertLess(handle_index, music_index)

    def test_tasker_left_resize_handle_visibility_progress_fades_near_expiry(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.hovered_key = ""
        visual.dragging = False
        visual.drag_press_key = ""
        visual.taskbar_left_handle_visible_until = 10.0

        full = visual._taskbar_left_handle_visibility_progress(now=9.2)
        fading = visual._taskbar_left_handle_visibility_progress(now=9.92)
        gone = visual._taskbar_left_handle_visibility_progress(now=10.3)

        self.assertGreater(full, fading)
        self.assertGreater(fading, 0.0)
        self.assertEqual(gone, 0.0)

    def test_tasker_left_resize_handle_slide_progress_collapses_toward_island(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.hovered_key = ""
        visual.dragging = False
        visual.drag_press_key = ""
        visual.taskbar_left_handle_visible_until = 10.0

        full = visual._taskbar_left_handle_slide_progress(now=9.2)
        sliding = visual._taskbar_left_handle_slide_progress(now=9.92)
        gone = visual._taskbar_left_handle_slide_progress(now=10.3)

        self.assertGreater(full, sliding)
        self.assertGreater(sliding, 0.0)
        self.assertEqual(gone, 0.0)

    def test_tasker_window_position_clamps_to_bottom_screen_boundary(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        screen_geo = QRect(0, 0, 1920, 1080)
        new_pos = QPoint(500, 1100)

        clamped = visual._clamp_taskbar_window_pos(new_pos, main_top=940.0, main_w=420.0, main_h=176.0, screen_geo=screen_geo)

        self.assertLessEqual(clamped.y() + 940.0 + 176.0, screen_geo.bottom() + 1.0)

    def test_tasker_window_position_can_reach_left_and_right_screen_edges(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        screen_geo = QRect(0, 0, 1920, 1080)
        main_top = 940.0
        main_w = 420.0
        main_h = 44.0
        main_left = visual.width() / 2.0 - main_w / 2.0

        left = visual._clamp_taskbar_window_pos(QPoint(-999, 900), main_top, main_w, main_h, screen_geo)
        right = visual._clamp_taskbar_window_pos(QPoint(9999, 900), main_top, main_w, main_h, screen_geo)

        self.assertEqual(left.x() + main_left, screen_geo.left())
        self.assertEqual(right.x() + main_left + main_w, screen_geo.right() + 1.0)

    def test_tasker_compact_music_width_is_stable_across_content_width_changes(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_manual_width = 0.0
        visual.taskbar_selected_drop_zone = QRectF()
        visual._u = lambda value: float(value)

        narrow = visual._taskbar_compact_music_size(300.0, 44.0)
        wide = visual._taskbar_compact_music_size(760.0, 44.0)

        self.assertEqual(narrow[0], wide[0])
        self.assertGreaterEqual(narrow[0], 360.0)

    def test_tasker_compact_music_height_uses_full_taskbar_height(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_manual_width = 0.0
        visual.taskbar_selected_drop_zone = QRectF()
        visual._u = lambda value: float(value)

        _width, height = visual._taskbar_compact_music_size(420.0, 44.0)

        self.assertAlmostEqual(height, 44.0)

    def test_tasker_compact_music_height_is_not_scaled_twice(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_manual_width = 0.0
        visual.taskbar_selected_drop_zone = QRectF()

        _width, height = visual._taskbar_compact_music_size(420.0, 52.0)

        self.assertAlmostEqual(height, 52.0)

    def test_tasker_anchor_music_height_uses_unscaled_taskbar_rect_height(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.taskbar_manual_width = 0.0
        visual.taskbar_selected_drop_zone = QRectF()

        _width, height = visual._main_size_for_taskbar_anchor(
            IslandKind.MUSIC,
            420.0,
            QRectF(0.0, 1388.0, 2560.0, 52.0),
        )

        self.assertAlmostEqual(height, 52.0)

    def test_stick_music_height_uses_edge_notch_height_not_taskbar_height(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "stick"
        visual.expanded = False
        visual.top_notch_height_mode = "standard"
        visual.lyric_flow_style = "wave_stream"
        visual.lyrics = []
        visual._u = lambda value: float(value)
        visual._should_paint_album_island = lambda: False
        visual._gelv_enabled = lambda: False
        visual._taskbar_height = lambda: 52.0

        _width, height = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)

        self.assertAlmostEqual(height, 40.0)

    def test_tasker_compact_non_music_main_height_uses_full_taskbar_height(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.taskbar_manual_width = 0.0
        visual._u = lambda value: float(value)
        visual._taskbar_height = lambda: 44.0
        visual._enabled_plugin_slot_count = lambda: 1

        _plugin_w, plugin_h = visual._main_size_for_kind(IslandKind.PLUGIN, 420.0)
        _message_w, message_h = visual._main_size_for_kind(IslandKind.MESSAGE, 420.0)
        _empty_w, empty_h = visual._main_size_for_kind(IslandKind.EMPTY_TEXT, 420.0)
        _time_w, time_h = visual._main_size_for_kind(IslandKind.TIME, 420.0)

        self.assertAlmostEqual(plugin_h, 44.0)
        self.assertAlmostEqual(message_h, 44.0)
        self.assertAlmostEqual(empty_h, 44.0)
        self.assertAlmostEqual(time_h, 44.0)

    def test_tasker_compact_music_height_ignores_stale_selected_slot_height(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_manual_width = 0.0
        visual.taskbar_selected_drop_zone = QRectF(100.0, 1044.0, 420.0, 34.0)
        visual._u = lambda value: float(value)

        _width, height = visual._taskbar_compact_music_size(420.0, 40.0)

        self.assertAlmostEqual(height, 40.0)

    def test_tasker_build_visuals_uses_full_height_when_shell_rect_is_hidden_sliver(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.finder_mode = False
        visual.taskbar_manual_width = 0.0
        visual.last_taskbar_rect = QRectF(0.0, 1078.0, 1920.0, 2.0)
        visual.taskbar_follow_base_rect = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual.taskbar_selected_drop_zone = QRectF(100.0, 1040.0, 420.0, 40.0)
        visual.messages = []
        visual._u = lambda value: float(value)
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._current_screen_geometry = lambda: QRect(0, 0, 1920, 1080)
        visual._stack_target_width = lambda _kind: 420.0
        visual._stack_center_x = lambda _width: 470.0
        visual._vertical_stack_direction = lambda *_args: -1
        visual._drag_hides_auxiliary_visuals = lambda: False
        visual._main_top_for_direction = lambda *_args: 72.0
        visual._taskbar_main_overlay_alert = lambda: None
        visual._active_plugin_alerts = lambda *_args, **_kwargs: []
        visual._plugin_enabled = lambda: False
        visual._plugin_panel_visible = lambda: False
        visual._folded_message_limit = lambda _kind: 3

        main = next(item for item in visual._build_visuals() if item.z_index == 10)

        self.assertEqual(main.key, "music")
        self.assertAlmostEqual(main.rect.height(), 40.0)

    def test_tasker_height_prefers_live_taskbar_height_over_stale_drop_zone(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_follow_base_rect = QRectF()
        visual.last_taskbar_rect = QRectF()
        visual.taskbar_selected_drop_zone = QRectF(100.0, 1047.0, 420.0, 26.0)
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(960, 1060)
        visual._taskbar_drag_reference_rect_for_screen = lambda _screen: QRectF(0.0, 1032.0, 1920.0, 48.0)

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            height = visual._taskbar_height()

        self.assertAlmostEqual(height, 48.0)

    def test_tasker_build_visuals_does_not_cap_tall_taskbar_height(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.finder_mode = False
        visual.taskbar_manual_width = 0.0
        visual.last_taskbar_rect = QRectF(0.0, 1000.0, 1920.0, 80.0)
        visual.taskbar_follow_base_rect = QRectF()
        visual.taskbar_selected_drop_zone = QRectF(100.0, 1000.0, 420.0, 80.0)
        visual.messages = []
        visual._u = lambda value: float(value)
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._current_screen_geometry = lambda: QRect(0, 0, 1920, 1080)
        visual._stack_target_width = lambda _kind: 420.0
        visual._stack_center_x = lambda _width: 470.0
        visual._vertical_stack_direction = lambda *_args: -1
        visual._drag_hides_auxiliary_visuals = lambda: False
        visual._main_top_for_direction = lambda *_args: 72.0
        visual._taskbar_main_overlay_alert = lambda: None
        visual._active_plugin_alerts = lambda *_args, **_kwargs: []
        visual._plugin_enabled = lambda: False
        visual._plugin_panel_visible = lambda: False
        visual._folded_message_limit = lambda _kind: 3

        main = next(item for item in visual._build_visuals() if item.z_index == 10)

        self.assertAlmostEqual(main.rect.height(), 80.0)

    def test_stick_music_height_grows_for_dual_line_lyrics(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "stick"
        visual.expanded = False
        visual.enable_dual_line_lyrics = True
        visual.lyric_flow_style = "wave_stream"
        visual.lyrics = ["And for the record baby\n涓枃缈昏瘧姝岃瘝"]
        visual.lyric_index = 0
        visual.taskbar_follow_base_rect = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual.last_taskbar_rect = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual.taskbar_selected_drop_zone = QRectF(100.0, 1040.0, 420.0, 40.0)
        visual.taskbar_manual_width = 0.0
        visual._u = lambda value: float(value)
        visual._should_paint_album_island = lambda: False
        visual._gelv_enabled = lambda: False

        _width, height = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)

        self.assertGreater(height, 40.0)

    def test_stick_music_single_line_height_stays_taskbar_edge_height(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "stick"
        visual.expanded = False
        visual.enable_dual_line_lyrics = False
        visual.lyric_flow_style = "wave_stream"
        visual.lyrics = ["Single lyric line"]
        visual.lyric_index = 0
        visual.taskbar_follow_base_rect = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual.last_taskbar_rect = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual.taskbar_selected_drop_zone = QRectF(100.0, 1040.0, 420.0, 40.0)
        visual.taskbar_manual_width = 0.0
        visual._u = lambda value: float(value)
        visual._should_paint_album_island = lambda: False
        visual._gelv_enabled = lambda: False

        _width, height = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)

        self.assertAlmostEqual(height, 40.0)

    def test_top_notch_height_modes_use_distinct_compact_standard_spacious_semantics(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "top"
        visual.expanded = False
        visual.enable_dual_line_lyrics = False
        visual.lyric_flow_style = "wave_stream"
        visual.lyrics = ["Single lyric line"]
        visual.lyric_index = 0
        visual._u = lambda value: float(value)
        visual._should_paint_album_island = lambda: False
        visual._gelv_enabled = lambda: False

        heights: dict[str, float] = {}
        for mode in ("compact", "standard", "spacious"):
            visual.top_notch_height_mode = mode
            _width, heights[mode] = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)

        self.assertLess(heights["compact"], heights["standard"])
        self.assertLess(heights["standard"], heights["spacious"])

        visual.enable_dual_line_lyrics = True
        visual.dual_line_font_size = 18
        visual.lyrics = ["First lyric line\nSecond lyric line"]
        visual.top_notch_height_mode = "compact"
        _width, compact_dual = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)
        self.assertFalse(visual._dual_line_lyrics_allowed())
        self.assertEqual(visual._lyric_display_lines("First lyric line\nSecond lyric line"), ["First lyric line"])
        visual.top_notch_height_mode = "standard"
        _width, standard_dual = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)
        self.assertTrue(visual._dual_line_lyrics_allowed())
        self.assertEqual(visual._lyric_display_lines("First lyric line\nSecond lyric line"), ["First lyric line", "Second lyric line"])
        self.assertLess(visual._dual_line_secondary_font_size(), 18)

        self.assertLess(compact_dual, standard_dual)

    def test_edge_notch_music_height_modes_are_shared_by_top_and_stick(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = False
        visual.enable_dual_line_lyrics = False
        visual.lyric_flow_style = "gelv_songscatter"
        visual.lyrics = ["Current lyric", "Next lyric"]
        visual.lyric_index = 0
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 14
        visual._u = lambda value: float(value)
        visual._should_paint_album_island = lambda: False
        visual._taskbar_height = lambda: 40.0
        visual._taskbar_compact_main_height = lambda fallback=None: float(fallback if fallback is not None else 40.0)

        heights: dict[str, dict[str, float]] = {"top": {}, "stick": {}}
        for height_mode in ("compact", "standard", "spacious"):
            visual.top_notch_height_mode = height_mode
            for location_mode in ("top", "stick"):
                visual.location_mode = location_mode
                _width, height = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)
                heights[location_mode][height_mode] = height

        for height_mode in ("compact", "standard", "spacious"):
            self.assertAlmostEqual(heights["top"][height_mode], heights["stick"][height_mode], delta=0.001)
        self.assertLess(heights["top"]["compact"], heights["top"]["standard"])
        self.assertLess(heights["top"]["standard"], heights["top"]["spacious"])

    def test_tasker_compact_lyrics_use_equal_vertical_padding(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual._u = lambda value: float(value)

        top, bottom = visual._compact_lyric_vertical_padding(two_line=False, rect=QRectF(10.0, 10.0, 360.0, 34.0))

        self.assertAlmostEqual(top, bottom)

    def test_tasker_cover_only_music_paints_album_cover_and_play_button(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.lyric_flow_style = "album_island"
        visual.no_lyrics_display_mode = "text"
        visual.media_is_playing = False
        visual.music_button_rects = {}
        visual._u = lambda value: float(value)
        visual._paint_cover = lambda *_args, **_kwargs: None
        image = QImage(120, 60, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music_compact(painter, QRectF(10.0, 10.0, 58.0, 34.0))
        painter.end()

        self.assertEqual(set(visual.music_button_rects), {"music:toggle_play_pause"})
        button = visual.music_button_rects["music:toggle_play_pause"]
        self.assertGreater(button.center().x(), 36.0)

    def test_tasker_cover_only_detects_crowded_medium_width(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.taskbar_selected_drop_zone = QRectF()
        visual._u = lambda value: float(value)

        self.assertTrue(visual._taskbar_cover_only_music_active(QRectF(10.0, 10.0, 118.0, 34.0)))

    def test_tasker_hover_overlay_registers_compact_playback_controls(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.lyric_flow_style = "wave_stream"
        visual.no_lyrics_display_mode = "text"
        visual.lyrics = ["line"]
        visual.media_is_playing = True
        visual.hovered_key = "music"
        visual.music_button_rects = {}
        visual._u = lambda value: float(value)
        visual._paint_cover = lambda *_args, **_kwargs: None
        visual._paint_compact_lyric_viewport = lambda *_args, **_kwargs: None
        visual.compact_lyric_show_cover = True
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music_compact(painter, QRectF(10.0, 10.0, 220.0, 34.0))
        painter.end()

        self.assertEqual(
            set(visual.music_button_rects),
            {"music:previous", "music:toggle_play_pause", "music:next"},
        )

    def test_tasker_hover_music_controls_are_large_opaque_right_side_buttons(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.lyric_flow_style = "wave_stream"
        visual.no_lyrics_display_mode = "text"
        visual.lyrics = ["line"]
        visual.media_is_playing = True
        visual.hovered_key = "music"
        visual.music_button_rects = {}
        visual._u = lambda value: float(value)
        visual._paint_cover = lambda *_args, **_kwargs: None
        visual._paint_compact_lyric_viewport = lambda *_args, **_kwargs: None
        visual.compact_lyric_show_cover = True
        painted: list[tuple[str, QRectF, int]] = []
        visual._paint_music_control_button = lambda _painter, rect, command, fill_alpha, radius=None: painted.append(
            (str(command), QRectF(rect), int(fill_alpha))
        )
        image = QImage(280, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music_compact(painter, QRectF(10.0, 10.0, 240.0, 34.0))
        painter.end()

        self.assertEqual([item[0] for item in painted], ["music:previous", "music:toggle_play_pause", "music:next"])
        buttons = [item[1] for item in painted]
        group = QRectF(buttons[0])
        for button in buttons[1:]:
            group = group.united(button)
        self.assertGreater(group.center().x(), 10.0 + 240.0 * 0.68)
        self.assertGreaterEqual(min(button.width() for button in buttons), 32.0)
        self.assertGreaterEqual(min(item[2] for item in painted), 176)
        self.assertGreater(visual.music_button_rects["music:next"].right(), 10.0 + 240.0 - 12.0)

    def test_tasker_hover_music_controls_do_not_scrim_whole_lyric_area(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.hovered_key = "music"
        visual._u = lambda value: float(value)
        visual._taskbar_compact_active = lambda: True
        visual._music_shell_radius = lambda _rect, _expanded=False: 8.0
        visual._paint_music_control_button = lambda *_args, **_kwargs: None
        image = QImage(280, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_taskbar_music_hover_overlay(painter, QRectF(10.0, 10.0, 240.0, 34.0))
        painter.end()

        self.assertEqual(image.pixelColor(28, 27).alpha(), 0)

    def test_music_control_icon_specs_use_svg_assets_and_theme_tone(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_is_playing = False
        visual.music_ambience_style = "system"
        with patch("local.island_stack_visual.system_dark_mode", return_value=False):
            visual._refresh_music_system_theme(force=True)

        play = visual._music_control_icon_spec("music:toggle_play_pause")
        previous = visual._music_control_icon_spec("music:previous")
        next_icon = visual._music_control_icon_spec("music:next")

        self.assertTrue(str(play["path"]).endswith("播放2.svg"))
        self.assertTrue(play["path"].exists())
        self.assertFalse(bool(play["mirrored"]))
        self.assertTrue(str(previous["path"]).endswith("上一首.svg"))
        self.assertTrue(previous["path"].exists())
        self.assertFalse(bool(previous["mirrored"]))
        self.assertTrue(str(next_icon["path"]).endswith("上一首.svg"))
        self.assertTrue(bool(next_icon["mirrored"]))

        visual.media_is_playing = True
        pause = visual._music_control_icon_spec("music:toggle_play_pause")
        self.assertTrue(str(pause["path"]).endswith("暂停.svg"))
        self.assertTrue(pause["path"].exists())

        with patch("local.island_stack_visual.system_dark_mode", return_value=True):
            visual._refresh_music_system_theme(force=True)
        dark_background_play = visual._music_control_icon_spec("music:toggle_play_pause")
        self.assertTrue(str(dark_background_play["path"]).endswith("暂停 (1).svg"))
        self.assertTrue(dark_background_play["path"].exists())

    def test_tasker_hover_music_controls_do_not_auto_activate(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertFalse(visual._hover_auto_activation_allowed("music:toggle_play_pause"))
        self.assertFalse(visual._hover_auto_activation_allowed("music:previous"))
        self.assertTrue(visual._hover_auto_activation_allowed("plugin"))

    def test_music_control_click_suppresses_current_hover_auto_expand_until_leave(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_control_press_started_at = {}
        visual.media_is_playing = False
        visual.last_media_refresh_at = 1.0
        visual.hovered_key = "music"
        visual.hover_armed_key = "music"
        visual.tasker_music_hover_auto_expand_suppressed = False
        visual._run_background = lambda _name, _callback: None
        visual._pulse_stack = lambda: None
        visual.update = lambda: None

        visual._activate_key("music:toggle_play_pause")

        self.assertTrue(visual.tasker_music_hover_auto_expand_suppressed)
        self.assertEqual(visual.hover_armed_key, "")
        self.assertFalse(visual._hover_auto_activation_allowed("music"))
        self.assertTrue(visual._hover_auto_activation_allowed("plugin"))

    def test_toggle_play_pause_records_elastic_animation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_control_press_started_at = {}
        visual.media_is_playing = False
        visual.last_media_refresh_at = 1.0
        visual._run_background = lambda _name, _callback: None
        visual._pulse_stack = lambda: None
        visual.update = lambda: None

        with patch("local.island_stack_visual.time.monotonic", return_value=12.0):
            visual._activate_key("music:toggle_play_pause")

        self.assertEqual(visual.music_control_press_started_at["music:toggle_play_pause"], 12.0)
        self.assertTrue(visual.media_is_playing)
        self.assertEqual(visual.last_media_refresh_at, 0.0)

    def test_music_control_elastic_scale_peaks_after_click(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_control_press_started_at = {"music:toggle_play_pause": 10.0}
        visual._animation_duration = lambda seconds, minimum=0.05: seconds

        self.assertGreater(visual._music_control_elastic_scale("music:toggle_play_pause", now=10.08), 1.0)
        self.assertEqual(visual._music_control_elastic_scale("music:toggle_play_pause", now=10.7), 1.0)

    def test_island_ripple_targets_controls_without_card_drag_or_fields(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._animation_duration = lambda seconds, minimum=0.05: seconds
        visual._music_control_icon_color = lambda _alpha: QColor(255, 255, 255)
        visual.update = lambda: None
        visual.music_button_rects = {"music:next": QRectF(80.0, 25.0, 34.0, 34.0)}
        visual.plugin_action_rects = {
            "timer-pause": QRectF(30.0, 30.0, 24.0, 24.0),
            "alarm-editor-focus-hour": QRectF(60.0, 30.0, 24.0, 24.0),
            "alarm-list": QRectF(20.0, 20.0, 100.0, 50.0),
        }
        shell = SpringRect(QRectF(10.0, 10.0, 130.0, 70.0))
        shell.opacity = 1.0
        visual.motions = {"music": shell, "plugin-panel": shell}

        self.assertTrue(visual._start_island_button_ripple("music:next", QPointF(94.0, 42.0), now=10.0))
        self.assertTrue(visual._start_island_button_ripple("plugin-action:timer-pause", QPointF(42.0, 42.0), now=10.1))
        self.assertFalse(visual._start_island_button_ripple("music", QPointF(50.0, 50.0), now=10.2))
        self.assertFalse(visual._start_island_button_ripple("plugin-panel", QPointF(50.0, 50.0), now=10.2))
        self.assertFalse(visual._start_island_button_ripple("plugin", QPointF(50.0, 50.0), now=10.2))
        self.assertFalse(visual._start_island_button_ripple("music:open_surface", QPointF(50.0, 50.0), now=10.2))
        self.assertFalse(visual._start_island_button_ripple("taskbar:left-handle", QPointF(50.0, 50.0), now=10.2))
        self.assertFalse(visual._start_island_button_ripple("plugin-action:alarm-editor-focus-hour", QPointF(70.0, 40.0), now=10.2))
        self.assertFalse(visual._start_island_button_ripple("plugin-action:alarm-list", QPointF(70.0, 40.0), now=10.2))
        self.assertFalse(visual._start_island_button_ripple("", QPointF(150.0, 90.0), now=10.2))
        self.assertEqual(len(visual._island_button_ripples), 2)
        self.assertEqual(visual._island_button_ripples[0]["clip"], QRectF(10.0, 10.0, 130.0, 70.0))

    def test_island_ripple_starts_only_on_activated_click_and_expires_on_existing_clock(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._animation_duration = lambda seconds, minimum=0.05: seconds
        visual._music_control_icon_color = lambda _alpha: QColor(255, 255, 255)
        visual._island_button_ripples = []
        visual.music_button_rects = {"music:previous": QRectF(40.0, 20.0, 36.0, 36.0)}
        visual.motions = {}
        visual.drag_press_key = "music:previous"
        visual.drag_press_pos = QPointF(50.0, 30.0)
        actions: list[str] = []
        visual.update = lambda: actions.append("update")
        visual._activate_key = lambda key: actions.append(key)
        event = SimpleNamespace(position=lambda: QPointF(50.0, 30.0))
        activated = SimpleNamespace(activate_pressed_key=True, update_after_activation=True, capture_release_anchor=False)
        dragged = SimpleNamespace(activate_pressed_key=False, update_after_activation=False, capture_release_anchor=False)

        with patch("local.island_stack_visual.time.monotonic", return_value=10.0):
            visual._apply_mouse_release_action_plan(dragged, event, QPoint())
            self.assertEqual(visual._island_button_ripples, [])
            visual._apply_mouse_release_action_plan(activated, event, QPoint())

        self.assertEqual(actions, ["update", "music:previous", "update"])
        self.assertEqual(len(visual._island_button_ripples), 1)
        self.assertTrue(visual._active_visual_animation_plan(10.1).active)
        self.assertFalse(visual._active_visual_animation_plan(10.5).active)

        image = QImage(140, 80, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        visual._paint_island_button_ripples(painter, now=10.15, owner_key="music")
        painter.end()
        self.assertTrue(any(image.pixelColor(x, y).alpha() > 0 for y in range(10, 70) for x in range(10, 130)))
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)

        visual._island_button_ripples = []
        visual.drag_press_key = "music:previous"
        outside = SimpleNamespace(position=lambda: QPointF(90.0, 30.0))
        with patch("local.island_stack_visual.time.monotonic", return_value=10.2):
            visual._apply_mouse_release_action_plan(activated, outside, QPoint())
        self.assertEqual(visual._island_button_ripples, [])

        visual._paint_island_button_ripples(QPainter(), now=10.5)
        self.assertEqual(visual._island_button_ripples, [])

    def test_island_ripple_does_not_start_on_alert_or_message_cards(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._animation_duration = lambda seconds, minimum=0.05: seconds
        visual.update = lambda: None
        alert = SpringRect(QRectF(10.0, 10.0, 90.0, 38.0))
        alert.opacity = 1.0
        message = SpringRect(QRectF(10.0, 55.0, 90.0, 38.0))
        message.opacity = 1.0
        hidden = SpringRect(QRectF(10.0, 100.0, 90.0, 38.0))
        hidden.opacity = 0.0
        visual.motions = {"plugin-alert:time": alert, "message:one": message, "message:hidden": hidden}

        self.assertFalse(visual._start_island_button_ripple("plugin-alert:time", QPointF(30.0, 25.0), now=20.0))
        self.assertFalse(visual._start_island_button_ripple("message:one", QPointF(30.0, 70.0), now=20.1))
        self.assertFalse(visual._start_island_button_ripple("message:hidden", QPointF(30.0, 115.0), now=20.1))
        self.assertEqual(getattr(visual, "_island_button_ripples", []), [])

    def test_island_track_navigation_glow_uses_cover_but_other_buttons_keep_their_origin(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._animation_duration = lambda seconds, minimum=0.05: seconds
        visual._music_control_icon_color = lambda _alpha: QColor(255, 255, 255)
        visual.update = lambda: None
        visual.music_cover_rect = QRectF(24.0, 24.0, 60.0, 60.0)
        visual.music_button_rects = {
            "music:previous": QRectF(120.0, 80.0, 32.0, 32.0),
            "music:next": QRectF(200.0, 80.0, 32.0, 32.0),
            "music:toggle_play_pause": QRectF(160.0, 80.0, 32.0, 32.0),
        }
        music = SpringRect(QRectF(10.0, 10.0, 260.0, 120.0))
        music.opacity = 1.0
        visual.motions = {"music": music}

        for index, key in enumerate(visual.music_button_rects):
            visual._start_island_button_ripple(key, visual.music_button_rects[key].center(), now=10.0 + index * 0.01)

        previous, following, toggle = visual._island_button_ripples
        self.assertEqual(previous["center"], visual.music_cover_rect.center())
        self.assertEqual(following["center"], visual.music_cover_rect.center())
        self.assertEqual(toggle["center"], visual.music_button_rects["music:toggle_play_pause"].center())
        self.assertEqual(previous["duration"], 1.72)
        self.assertEqual(following["duration"], 1.72)
        self.assertEqual(previous["travel_duration"], 1.5)
        self.assertEqual(previous["source_radius_fraction"], 0.0)
        self.assertEqual(toggle["duration"], 0.38)
        self.assertTrue(all(ripple["owner_key"] == "music" for ripple in visual._island_button_ripples))
        self.assertTrue(all(ripple["clip"] == music.rect for ripple in visual._island_button_ripples))

        visual.music_cover_rect = QRectF()
        visual._start_island_button_ripple("music:next", QPointF(216.0, 96.0), now=10.04)
        self.assertEqual(visual._island_button_ripples[-1]["center"], QPointF(216.0, 96.0))
        self.assertEqual(visual._island_button_ripples[-1]["duration"], 0.38)

    def test_island_glow_is_a_circular_ring_clipped_to_its_own_shell(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._animation_duration = lambda seconds, minimum=0.05: seconds
        visual._music_control_icon_color = lambda _alpha: QColor(255, 255, 255)
        visual.update = lambda: None
        visual.music_button_rects = {"music:toggle_play_pause": QRectF(96.0, 66.0, 32.0, 32.0)}
        music = SpringRect(QRectF(10.0, 10.0, 220.0, 110.0))
        music.opacity = 1.0
        visual.motions = {"music": music}
        visual._start_island_button_ripple("music:toggle_play_pause", QPointF(112.0, 82.0), now=10.0)
        image = QImage(250, 140, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        # The ring is filled by a radial light falloff, not a painter outline.
        painter.setPen(QColor(0, 0, 0))
        visual._paint_island_button_ripples(painter, 10.12, owner_key="music", shell_rect=music.rect)
        painter.end()

        self.assertLess(image.pixelColor(112, 82).alpha(), 5)
        radial_peaks = [image.pixelColor(x, y).alpha() for x, y in ((81, 82), (143, 82), (112, 51))]
        self.assertGreater(min(radial_peaks), 80)
        self.assertLess(max(radial_peaks) - min(radial_peaks), 12)
        self.assertLess(image.pixelColor(122, 82).alpha(), image.pixelColor(143, 82).alpha())
        self.assertEqual(image.pixelColor(9, 65).alpha(), 0)
        self.assertEqual(image.pixelColor(10, 10).alpha(), 0)
        self.assertEqual(image.pixelColor(240, 125).alpha(), 0)

        other = QImage(250, 140, QImage.Format.Format_ARGB32_Premultiplied)
        other.fill(QColor(0, 0, 0, 0))
        painter = QPainter(other)
        visual._paint_island_button_ripples(painter, 10.12, owner_key="plugin-panel", shell_rect=music.rect)
        painter.end()
        self.assertEqual(other.pixelColor(112, 82).alpha(), 0)

        # Calling without a card owner only prunes expired animation state;
        # it must never draw above text, cover art, or the host surface.
        no_owner = QImage(250, 140, QImage.Format.Format_ARGB32_Premultiplied)
        no_owner.fill(QColor(0, 0, 0, 0))
        painter = QPainter(no_owner)
        visual._paint_island_button_ripples(painter, 10.12)
        painter.end()
        self.assertEqual(no_owner.pixelColor(112, 82).alpha(), 0)

    def test_island_music_glow_stays_behind_cover_and_content_in_both_layouts(self):
        for expanded, layout in ((False, "standard"), (True, "dual_panel")):
            with self.subTest(expanded=expanded, layout=layout):
                visual = IslandStackVisual.__new__(IslandStackVisual)
                visual._u = lambda value: float(value)
                visual._animation_duration = lambda seconds, minimum=0.05: seconds
                visual._music_control_icon_color = lambda _alpha: QColor(255, 255, 255)
                visual.update = lambda: None
                visual.music_button_rects = {"music:toggle_play_pause": QRectF(100.0, 65.0, 32.0, 32.0)}
                shell = QRectF(10.0, 10.0, 240.0, 110.0)
                music = SpringRect(shell)
                music.opacity = 1.0
                visual.motions = {"music": music}
                visual._start_island_button_ripple("music:toggle_play_pause", QPointF(116.0, 81.0), now=10.0)
                visual._island_glow_paint_now = 10.12
                visual._effective_music_ambience_style = lambda: "pure_black"
                visual._normalize_music_expanded_layout_style = lambda style: str(style)
                visual.music_expanded_layout_style = layout
                visual._active_lyric_color_fill_paints_background = lambda _style=None: False
                visual._music_shell_radius = lambda _rect, _expanded: 22.0
                visual._music_shell_path = lambda rect, radius, _expanded=False: visual._rounded_path(rect, radius)
                visual._record_visual_paint_section = lambda *_args: None
                visual._paint_pure_black_music_background = lambda painter, rect, radius: painter.fillPath(visual._rounded_path(rect, radius), QColor(0, 0, 0))
                visual._paint_dual_panel_music_background = lambda painter, rect, radius, _style: painter.fillPath(visual._rounded_path(rect, radius), QColor(0, 0, 0))
                visual._paint_music_reactive_background_overlay = lambda *_args: None
                visual._paint_music_surface_transition_wash = lambda *_args: None
                visual._paint_music_border = lambda *_args: None
                cover = QRectF(30.0, 30.0, 46.0, 46.0)
                glyph = QRectF(108.0, 75.0, 16.0, 16.0)

                def content(painter, _rect):
                    painter.fillRect(cover, QColor(15, 80, 140))
                    painter.fillRect(glyph, QColor(255, 255, 255))

                visual._paint_music_compact = content
                visual._paint_music_expanded = content
                image = QImage(270, 140, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(QColor(0, 0, 0, 0))
                painter = QPainter(image)
                visual._paint_music(painter, shell, expanded)
                painter.end()

                self.assertEqual(image.pixelColor(53, 53), QColor(15, 80, 140))
                self.assertEqual(image.pixelColor(116, 83), QColor(255, 255, 255))
                self.assertGreater(image.pixelColor(148, 81).red(), 10)
                self.assertEqual(image.pixelColor(8, 81).alpha(), 0)

    def test_island_circular_glow_keeps_visible_curvature_in_early_frames(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._animation_duration = lambda seconds, minimum=0.05: seconds
        visual._music_control_icon_color = lambda _alpha: QColor(255, 255, 255)
        visual.update = lambda: None
        visual.music_button_rects = {"music:toggle_play_pause": QRectF(96.0, 66.0, 32.0, 32.0)}
        shell = SpringRect(QRectF(10.0, 10.0, 220.0, 110.0))
        shell.opacity = 1.0
        visual.motions = {"music": shell}
        visual._start_island_button_ripple("music:toggle_play_pause", QPointF(112.0, 82.0), now=10.0)
        peaks = []
        for elapsed in (0.016, 0.032, 0.064, 0.128):
            image = QImage(250, 140, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor(0, 0, 0, 0))
            painter = QPainter(image)
            visual._paint_island_button_ripples(painter, 10.0 + elapsed, owner_key="music", shell_rect=shell.rect)
            painter.end()
            peak = max(range(113, 220), key=lambda x: image.pixelColor(x, 82).alpha())
            self.assertGreater(image.pixelColor(peak, 82).alpha(), 50)
            self.assertGreater(image.pixelColor(peak, 82).alpha(), image.pixelColor(112, 82).alpha() + 30)
            peaks.append(peak)
        self.assertEqual(peaks, sorted(set(peaks)))
        self.assertLess(peaks[0] - 112, 25)

    def test_island_album_ring_accelerates_to_right_edge_with_a_thick_soft_band(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._animation_duration = lambda seconds, minimum=0.05: seconds
        visual._music_control_icon_color = lambda _alpha: QColor(255, 255, 255)
        visual.update = lambda: None
        visual.music_cover_rect = QRectF(24.0, 24.0, 60.0, 60.0)
        visual.music_button_rects = {"music:next": QRectF(550.0, 43.0, 32.0, 32.0)}
        shell = SpringRect(QRectF(10.0, 10.0, 600.0, 100.0))
        shell.opacity = 1.0
        visual.motions = {"music": shell}
        visual._start_island_button_ripple("music:next", QPointF(566.0, 59.0), now=10.0)
        self.assertTrue(visual._active_visual_animation_plan(11.6).active)
        self.assertFalse(visual._active_visual_animation_plan(11.72).active)
        at_start = QImage(630, 130, QImage.Format.Format_ARGB32_Premultiplied)
        at_start.fill(QColor(0, 0, 0, 0))
        start_painter = QPainter(at_start)
        visual._paint_island_button_ripples(start_painter, 10.0, owner_key="music", shell_rect=shell.rect)
        start_painter.end()
        self.assertEqual(at_start.pixelColor(54, 54).alpha(), 0)
        self.assertEqual(at_start.pixelColor(83, 54).alpha(), 0)
        peak_positions = {}
        for elapsed in (0.1, 0.25, 0.5, 0.75, 1.0, 1.25, 1.45, 1.5):
            with self.subTest(elapsed=elapsed):
                image = QImage(630, 130, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(QColor(0, 0, 0, 0))
                painter = QPainter(image)
                visual._paint_island_button_ripples(painter, 10.0 + elapsed, owner_key="music", shell_rect=shell.rect)
                painter.end()
                travel = min(1.0, elapsed / 1.5)
                expected_peak = 54 + round((shell.rect.right() - 54) * (0.4 * travel + 0.6 * travel * travel))
                peak = max(range(55, 609), key=lambda x: image.pixelColor(x, 54).alpha())
                peak_positions[elapsed] = peak
                self.assertLessEqual(abs(peak - expected_peak), 8)
                self.assertGreater(image.pixelColor(peak, 54).alpha(), 40)
                self.assertLessEqual(image.pixelColor(peak, 54).alpha(), 145)
                if elapsed in (0.5, 1.0):
                    self.assertGreater(image.pixelColor(peak - 20, 54).alpha(), 45)
                    self.assertGreater(image.pixelColor(peak + 20, 54).alpha(), 45)
                if elapsed == 0.1:
                    diagonal = round((peak - 54) / math.sqrt(2.0))
                    diagonal_peak = max(image.pixelColor(54 + diagonal + dx, 54 - diagonal + dy).alpha()
                        for dx in (-1, 0, 1) for dy in (-1, 0, 1))
                    self.assertLess(abs(image.pixelColor(peak, 54).alpha()
                        - diagonal_peak), 20)
                self.assertEqual(image.pixelColor(610, 54).alpha(), 0)

        # Equal time slices cover successively more ground: the ring accelerates
        # without an abrupt jump and still starts directly behind the cover.
        advances = [peak_positions[b] - peak_positions[a] for a, b in (
            (0.25, 0.5), (0.5, 0.75), (0.75, 1.0), (1.0, 1.25),
        )]
        self.assertEqual(advances, sorted(advances))
        self.assertLess(peak_positions[0.1] - 54, 25)
        self.assertGreaterEqual(peak_positions[1.5], 602)

        edge_alphas = []
        for elapsed in (1.5, 1.6, 1.7, 1.72):
            image = QImage(630, 130, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor(0, 0, 0, 0))
            painter = QPainter(image)
            visual._paint_island_button_ripples(painter, 10.0 + elapsed, owner_key="music", shell_rect=shell.rect)
            painter.end()
            edge_alphas.append(image.pixelColor(608, 54).alpha())
        self.assertGreater(edge_alphas[0], edge_alphas[1])
        self.assertGreater(edge_alphas[1], edge_alphas[2])
        self.assertEqual(edge_alphas[3], 0)

    def test_island_glow_rapid_clicks_remain_bounded_and_track_shell_geometry(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._animation_duration = lambda seconds, minimum=0.05: seconds
        visual._music_control_icon_color = lambda _alpha: QColor(255, 255, 255)
        visual.update = lambda: None
        visual.music_button_rects = {"music:next": QRectF(80.0, 45.0, 32.0, 32.0)}
        visual.music_cover_rect = QRectF(25.0, 25.0, 40.0, 40.0)
        music = SpringRect(QRectF(10.0, 10.0, 130.0, 80.0))
        music.opacity = 1.0
        visual.motions = {"music": music}
        for index in range(20):
            visual._start_island_button_ripple("music:next", QPointF(96.0, 61.0), now=10.0 + index * 0.004)
        self.assertEqual(len(visual._island_button_ripples), 4)
        shifted = QRectF(60.0, 20.0, 260.0, 160.0)
        image = QImage(340, 200, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        visual._paint_island_button_ripples(painter, 11.076, owner_key="music", shell_rect=shifted)
        painter.end()
        self.assertGreater(image.pixelColor(225, 90).alpha(), 25)
        self.assertEqual(image.pixelColor(45, 45).alpha(), 0)
        visual._paint_island_button_ripples(QPainter(), 12.3)
        self.assertEqual(visual._island_button_ripples, [])

    def test_island_glow_keeps_joined_plugin_shell_and_message_edges_clean(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._animation_duration = lambda seconds, minimum=0.05: seconds
        visual._plugin_theme_colors = lambda: {
            "shell": QColor(30, 30, 32),
            "shell_expanded": QColor(30, 30, 32),
            "stroke": QColor(255, 255, 255, 40),
            "inner_stroke": QColor(0, 0, 0, 30),
            "muted": QColor(185, 185, 190),
        }
        visual._system_dark_mode = lambda: True
        visual.update = lambda: None
        visual.plugin_action_rects = {"timer-pause": QRectF(130.0, 55.0, 30.0, 30.0)}
        visual._plugin_panel_visible = lambda: True
        visual._plugin_panel_shell_rect = lambda _rect: QRectF(30.0, 25.0, 190.0, 100.0)
        visual._plugin_panel_shell_radius = lambda _rect: 22.0
        visual._enabled_plugin_samples = lambda: []
        joined = SpringRect(QRectF(30.0, 25.0, 190.0, 100.0))
        joined.opacity = 1.0
        visual.motions = {"plugin-panel": joined}
        visual._start_island_button_ripple("plugin-action:timer-pause", QPointF(145.0, 70.0), now=10.0)
        visual._island_glow_paint_now = 10.12
        visual._island_glow_current_visual_key = "plugin-panel"
        image = QImage(250, 150, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        visual._paint_plugin_panel(painter, QRectF(30.0, 25.0, 190.0, 100.0))
        painter.end()
        self.assertGreater(image.pixelColor(176, 70).red(), 30)
        self.assertEqual(image.pixelColor(29, 75).alpha(), 0)
        self.assertEqual(image.pixelColor(30, 25).alpha(), 0)

        # Tiny message cards are truly elliptical, so the glow must not leave
        # rectangular corner pixels even when the painter's own clip is broad.
        visual._island_button_ripples = [{
            "started_at": 10.0,
            "duration": 0.38,
            "center": QPointF(50.0, 50.0),
            "clip": QRectF(30.0, 30.0, 40.0, 40.0),
            "origin_fraction": QPointF(0.5, 0.5),
            "owner_key": "message:one",
            "light_surface": False,
        }]
        visual._island_glow_current_visual_key = "message:one"
        visual._message_card_progress = lambda _rect: 0.0
        tiny = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
        tiny.fill(QColor(0, 0, 0, 0))
        painter = QPainter(tiny)
        visual._paint_message(painter, QRectF(30.0, 30.0, 40.0, 40.0), SimpleNamespace(name_color=QColor(30, 80, 140)), False)
        painter.end()
        self.assertEqual(tiny.pixelColor(30, 30).alpha(), 0)
        self.assertGreater(tiny.pixelColor(66, 50).alpha(), 0)

    def test_island_solid_album_glow_stays_inside_music_shell(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._animation_duration = lambda seconds, minimum=0.05: seconds
        visual._music_control_icon_color = lambda _alpha: QColor(255, 255, 255)
        visual.update = lambda: None
        visual.music_button_rects = {"music:next": QRectF(180.0, 55.0, 32.0, 32.0)}
        visual.music_cover_rect = QRectF(30.0, 30.0, 40.0, 40.0)
        shell = QRectF(10.0, 10.0, 240.0, 110.0)
        motion = SpringRect(shell)
        motion.opacity = 1.0
        visual.motions = {"music": motion}
        visual._start_island_button_ripple("music:next", QPointF(196.0, 71.0), now=10.0)
        visual._island_glow_paint_now = 10.12
        visual._effective_music_ambience_style = lambda: "solid_album"
        visual._normalize_music_expanded_layout_style = lambda style: str(style)
        visual.music_expanded_layout_style = "standard"
        visual._active_lyric_color_fill_paints_background = lambda _style=None: False
        visual._music_shell_radius = lambda _rect, _expanded: 20.0
        visual._music_shell_path = lambda rect, radius, _expanded=False: visual._rounded_path(rect, radius)
        visual._record_visual_paint_section = lambda *_args: None
        visual._paint_solid_album_music_background = lambda painter, rect, radius: painter.fillPath(visual._rounded_path(rect, radius), QColor(0, 0, 0))
        visual._paint_music_reactive_background_overlay = lambda *_args: None
        visual._paint_music_surface_transition_wash = lambda *_args: None
        visual._paint_music_compact = lambda *_args: None
        visual._paint_music_border = lambda *_args: None
        image = QImage(270, 140, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        visual._paint_music(painter, shell, False)
        painter.end()

        self.assertEqual(image.pixelColor(5, 68).alpha(), 0)
        self.assertLess(image.pixelColor(17, 68).red(), 24)
        self.assertGreater(image.pixelColor(85, 50).red(), 3)

    def test_mydockfinder_listener_switches_allowed_modes(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.mydockfinder_running = False
        visual.manual_finder_available = False
        visual._reset_location_runtime_state = lambda *args, **kwargs: None
        visual._notify_settings_changed = lambda: None
        visual._begin_surface_transition = lambda _reason: False
        visual._hide_taskbar_drop_overlay = lambda: None
        visual._force_super_topmost = lambda: None
        visual.update = lambda: None

        visual.set_mydockfinder_running(True, notify=False)

        self.assertEqual(visual.location_mode, "free")
        self.assertEqual(visual.available_location_modes(), ["free", "finder"])

        visual.set_mydockfinder_running(False, notify=False)

        self.assertEqual(visual.available_location_modes(), ["top", "free", "tasker"])

    def test_edge_location_modes_are_canonical_only_without_mydockfinder(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.mydockfinder_running = False
        visual.manual_finder_available = False
        visual.taskbar_auto_hide_mode = "auto"

        self.assertEqual(visual._canonical_location_mode("top"), "top")
        self.assertEqual(visual._canonical_location_mode("stick"), "stick")

        visual.mydockfinder_running = True

        self.assertEqual(visual._canonical_location_mode("top"), "free")
        self.assertEqual(visual._canonical_location_mode("tasker"), "free")
        self.assertEqual(visual._canonical_location_mode("stick"), "free")

    def test_set_location_mode_stick_refreshes_above_taskbar_position_immediately(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "free"
        visual.finder_mode = False
        visual.mydockfinder_running = False
        visual.manual_finder_available = False
        visual.taskbar_auto_hide_mode = "auto"
        visual.drag_candidate = False
        visual._reset_location_runtime_state = lambda *args, **kwargs: None
        visual._begin_surface_transition = lambda _reason: False
        visual.clearMask = lambda: None
        visual._sync_taskbar_drop_overlay = lambda: None
        visual._pulse_stack = lambda: None
        visual._notify_settings_changed = lambda: None
        visual.update = lambda: None
        refresh_calls: list[str] = []
        visual._refresh_taskbar_above_follow_position = lambda: refresh_calls.append("refresh") or True

        visual.set_location_mode("stick", notify=False)

        self.assertEqual(visual.location_mode, "stick")
        self.assertTrue(visual.taskbar_above_follow_active)
        self.assertEqual(refresh_calls, ["refresh"])

    def test_mydockfinder_listener_result_does_not_clear_manual_finder_availability(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "finder"
        visual.finder_mode = True
        visual.attach_to_real_finder = True
        visual.mydockfinder_running = True
        visual.manual_finder_available = True
        visual._reset_location_runtime_state = lambda *args, **kwargs: None
        visual._notify_settings_changed = lambda: None
        visual._begin_surface_transition = lambda _reason: False
        visual._hide_taskbar_drop_overlay = lambda: None
        visual._force_super_topmost = lambda: None
        visual.update = lambda: None

        visual.set_mydockfinder_running(False, notify=False)

        self.assertTrue(visual.manual_finder_available)
        self.assertFalse(visual.mydockfinder_running)
        self.assertEqual(visual.location_mode, "finder")
        self.assertEqual(visual.available_location_modes(), ["free", "finder"])

    def test_last_position_snapshot_does_not_store_mode(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.stack_center_ratio = 0.5
        visual.saved_window_pos = (11, 22)
        visual.last_window_position = None
        visual.stack_direction = 1
        visual.taskbar_corner_style = "win11"
        visual.taskbar_left_handle_color = "#8E8E93"
        visual.taskbar_manual_width = 0.0
        visual.taskbar_position_ratio = 0.5
        visual.expanded = False
        visual.show_seconds = True
        visual.persistent_plugin = True
        visual.enabled_plugin_ids = {"time"}
        visual.enabled_plugin_count = 1
        visual.side_island_style = "standard"
        visual.plugin_island_style = "standard"
        visual.music_island_style = "standard"
        visual.time_alarm_side_display = "note"
        visual.music_playing = True
        visual.music_auto_detect = False
        visual.super_topmost_enabled = True
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.no_lyrics_text = "暂无歌词"
        visual.lyric_offset_seconds = 0.0
        visual.lyric_font_size = 18
        visual.lyric_elastic_strength = 100
        visual.lyric_elastic_mode = "both"
        visual.lyric_word_spacing = 100
        visual.enable_dual_line_lyrics = False
        visual.dual_line_font_size = 12
        visual.dual_line_secondary_weight = 72
        visual.dual_line_secondary_spacing = 100
        visual.compact_lyric_show_cover = True
        visual.lyric_flow_style = "wave_stream"
        visual.gelv_font_family = "Source Han Serif SC"
        visual.gelv_primary_font_size = 22
        visual.gelv_secondary_font_size = 12
        visual.gelv_dual_line_font_size = 17
        visual.gelv_initial_word_spacing = 220
        visual.gelv_word_spacing = 320
        visual.gelv_color_fill_enabled = False
        visual.wave_color_fill_enabled = False
        visual.no_lyrics_display_mode = "text"
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_compact_cover_size = 30
        visual.album_island_expanded_cover_size = 72
        visual.album_island_spacing = 112
        visual.album_island_secondary_style = "perspective_dim"
        visual.album_island_perspective_amount = 100
        visual.listened_seconds_total = 0.0
        visual.album_history = []
        visual.music_ambience_style = "sea_level"
        visual.ambient_intensity = 0.0
        visual.music_flow_intensity = 0.74
        visual.compact_duotone_balance = 50
        visual.expanded_duotone_balance = 50
        visual.ui_font_family = "PingFang SC"
        visual.high_quality_font_rendering = False
        visual.animation_fps = 60
        visual.animation_speed = 1.0
        visual.empty_bar_palette = "aurora"
        visual.empty_bar_width = 92
        visual.empty_bar_height = 14
        visual.empty_bar_y_offset = 0
        visual.empty_bar_opacity = 100
        visual.empty_bar_custom_colors = ("#46D6FF", "#73FFA6", "#FF5A86")
        visual.barrage_y_offset = 0
        visual.barrage_font_size = 10
        visual.barrage_speed = 100
        visual.barrage_use_backdrop = True
        visual.barrage_show_icon = True
        visual.mydockfinder_path = ""
        visual.mydockfinder_running = False
        visual._serializable_album_history = lambda: []

        visual.save_last_position(QPoint(100, 200))
        snapshot = visual.settings_snapshot()

        self.assertEqual(snapshot["v2_last_window_position"], {"x": 100, "y": 200})
        self.assertNotIn("v2_last_location_mode", snapshot)

    def test_free_drag_no_longer_auto_snaps_to_top_center(self):
        source = inspect.getsource(IslandStackVisual._move_standalone_drag)

        self.assertNotIn("_snap_top_center_window_pos", source)

    def test_position_preset_top_center_moves_standalone_window(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.location_mode = "free"
        visual.expanded = False
        visual.stack_direction = 1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_kind = lambda _kind, _stack_w: (300.0, 62.0)
        visual._main_top_for_direction = lambda _kind, _main_h, _direction: 20.0
        visual._notify_settings_changed = lambda: None
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        with patch("local.island_stack_visual.QGuiApplication.primaryScreen", return_value=FakeScreen()):
            self.assertTrue(visual.apply_position_preset("top_center", notify=False))

        self.assertTrue(moves)
        main_left = visual.width() / 2.0 - 300.0 / 2.0
        self.assertEqual(moves[-1].x() + main_left + 150.0, 960.0)
        self.assertEqual(moves[-1].y() + 20.0, 8.0)

    def test_position_preset_last_restores_last_window_position(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "free"
        visual.saved_window_pos = None
        visual.last_window_position = (321, 123)
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        self.assertTrue(visual.apply_position_preset("last", notify=False))

        self.assertEqual(moves[-1], QPoint(321, 123))

    def test_standalone_move_remembers_last_window_position(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "free"
        visual.finder_mode = False
        visual._initializing_position = False
        visual._restoring_position = False
        visual.pos = lambda: QPoint(432, 210)

        remembered = getattr(visual, "_remember_current_window_position", lambda: False)()

        self.assertTrue(remembered)
        self.assertEqual(visual.last_window_position, (432, 210))
        self.assertEqual(visual.saved_window_pos, (432, 210))

    def test_tasker_drag_track_allows_bottom_corners(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_task_area_rect_for_screen = lambda _screen, _taskbar: QRectF(120.0, 1040.0, 1520.0, 40.0)

        drag_track = visual._taskbar_drag_track_rect_for_screen(None, taskbar)

        self.assertLessEqual(drag_track.left(), 0.0)
        self.assertGreaterEqual(drag_track.right(), 1919.0)

    def test_tasker_follow_position_preserves_intended_width_when_track_detection_is_narrow(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_position_ratio = 0.5
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(960, 1040)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, _taskbar: QRectF(910.0, 1040.0, 96.0, 40.0)
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 760.0
        visual._main_size_for_kind = lambda _kind, _stack_w: (420.0, 34.0)
        visual._main_top_for_direction = lambda *_args: 10.0
        visual.move = lambda _pos: None

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertAlmostEqual(visual.taskbar_selected_drop_zone.width(), 420.0, delta=0.1)

    def test_set_location_mode_tasker_refreshes_position_immediately(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.manual_finder_available = False
        visual.location_mode = "free"
        visual.finder_mode = False
        visual.attach_to_real_finder = False
        visual.taskbar_preview_visible = False
        visual.scene_suppressed = False
        visual.scene_reason = ""
        visual.scene_transition_mode = ""
        visual.group_frame_mask_path = None
        visual._reset_location_runtime_state = lambda **_kwargs: None
        visual._begin_surface_transition = lambda _reason="": False
        visual._sync_taskbar_drop_overlay = lambda: None
        visual.clearMask = lambda: None
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None
        refresh_calls: list[str] = []
        visual._refresh_taskbar_follow_position = lambda: refresh_calls.append("refresh") or True

        visual.set_location_mode("tasker", notify=False)

        self.assertEqual(visual.location_mode, "tasker")
        self.assertEqual(refresh_calls, ["refresh"])

    def test_set_taskbar_fusion_mode_expanded_opens_panel_and_enters_tasker(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = False
        visual.plugin_panel_open = False
        location_calls: list[tuple[str, bool]] = []
        visual.set_location_mode = lambda mode, notify=True: location_calls.append((str(mode), bool(notify)))

        visual.set_taskbar_fusion_mode("expanded", notify=False)

        self.assertTrue(visual.expanded)
        self.assertTrue(visual.plugin_panel_open)
        self.assertEqual(location_calls, [("tasker", False)])

    def test_set_taskbar_fusion_mode_preview_sets_preview_before_free_location(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_preview_visible = False
        preview_at_location_call: list[bool] = []

        def record_location(mode, notify=True):
            preview_at_location_call.append(bool(visual.taskbar_preview_visible))
            visual.location_mode = str(mode)

        visual.set_location_mode = record_location

        visual.set_taskbar_fusion_mode("preview", notify=False)

        self.assertEqual(visual.location_mode, "free")
        self.assertEqual(preview_at_location_call, [True])

    def test_tasker_follow_recovers_from_stale_cover_only_selected_zone(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.expanded = False
        visual.taskbar_position_ratio = 0.5
        visual.taskbar_manual_width = 0.0
        visual.taskbar_selected_drop_zone = QRectF(930.0, 1044.0, 42.0, 32.0)
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 650
        visual.mapToGlobal = lambda _point: QPoint(960, 1080)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, _taskbar: QRectF(180.0, 1040.0, 1500.0, 40.0)
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: False
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: False
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 760.0
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertGreaterEqual(visual.taskbar_selected_drop_zone.width(), 360.0)
        self.assertTrue(moves)
        main_global = QRectF(
            moves[-1].x() + visual.width() / 2.0 - visual.taskbar_selected_drop_zone.width() / 2.0,
            visual.taskbar_selected_drop_zone.top(),
            visual.taskbar_selected_drop_zone.width(),
            visual.taskbar_selected_drop_zone.height(),
        )
        self.assertGreaterEqual(main_global.left(), 0.0)
        self.assertLessEqual(main_global.right(), 1920.0)
        self.assertLessEqual(main_global.bottom(), 1080.0)

    def test_tasker_follow_skips_redundant_window_move(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.expanded = True
        visual.taskbar_position_ratio = 0.5
        visual.taskbar_manual_width = 0.0
        visual.taskbar_selected_drop_zone = QRectF()
        visual.saved_window_pos = None
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 650
        visual.mapToGlobal = lambda _point: QPoint(960, 1080)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, _taskbar: QRectF(180.0, 1040.0, 1500.0, 40.0)
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 760.0
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._refresh_taskbar_follow_position())
            self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertEqual(len(moves), 1)

    def test_tasker_tick_skips_same_frame_follow_refresh(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.last_tick_at = 100.0
        visual.animation_speed = 1.0
        visual.location_mode = "tasker"
        visual.drag_candidate = False
        visual.taskbar_follow_last_at = 100.0
        visual.scene_suppressed = False
        visual.pointer_pos = QPointF(-1.0, -1.0)
        visual.barrage_clock = 0.0
        visual.super_topmost_enabled = False
        visual.media_is_playing = False
        visual.timed_lyrics = []
        visual.lyric_changed_at = 100.0
        visual.lyrics = ["line"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.hovered_key = ""
        visual.hover_armed_key = ""
        visual.hover_started_at = 0.0
        visual.expanded = False
        visual.plugin_panel_open = False
        visual.left_stack_at = 0.0
        visual.scroll_phase = 0.0
        visual.ambient_phase = 0.0
        visual._refresh_media_data = lambda _now: None
        visual._update_scene_state = lambda _now: None
        visual._point_in_barrage = lambda _point: False
        visual._maybe_trigger_alarm_row_long_press = lambda _now: False
        visual._update_wave_values = lambda: None
        visual.update = lambda: None
        refresh_calls: list[str] = []
        visual._refresh_taskbar_follow_position = lambda: refresh_calls.append("refresh") or True

        with patch("local.island_stack_visual.time.monotonic", return_value=100.005):
            visual._tick()

        self.assertEqual(refresh_calls, [])

    def test_stable_music_tick_keeps_playback_scroll_on_configured_frame_interval(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.last_tick_at = 10.0
        visual.last_render_update_at = 10.0
        visual.animation_fps = 60
        visual.animation_speed = 1.0
        visual.location_mode = "free"
        visual.scene_suppressed = False
        visual.pointer_pos = QPointF(-1.0, -1.0)
        visual.barrage_clock = 0.0
        visual.barrage_overlay = None
        visual.super_topmost_enabled = False
        visual.media_is_playing = True
        visual.timed_lyrics = []
        visual.lyric_changed_at = 10.0
        visual.lyrics = ["line"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.hovered_key = ""
        visual.hover_armed_key = ""
        visual.hover_started_at = 0.0
        visual.hover_bounce_until = 0.0
        visual.stack_bounce_until = 0.0
        visual.expanded = False
        visual.plugin_panel_open = False
        visual.left_stack_at = 0.0
        visual.scroll_phase = 0.0
        visual.ambient_phase = 0.0
        visual.plugin_time_module_animation = {}
        visual.plugin_alarm_editor_spin_animations = {}
        visual.plugin_alarm_editor_page_animation = {}
        visual.plugin_alarm_editor_reflow_animation = {}
        visual.scene_transition_mode = ""
        visual.surface_transition_started_at = 0.0
        visual.album_island_transition_started_at = 0.0
        visual._sync_music_background_to_theme = lambda: False
        visual._refresh_media_data = lambda _now: None
        visual._update_scene_state = lambda _now: None
        visual._prune_expired_messages = lambda _now: None
        visual._ensure_fullscreen_plugin_barrage_messages = lambda _now: None
        visual._sync_fullscreen_plugin_alert_overlay = lambda _now: None
        visual._point_in_barrage = lambda _point: False
        visual._maybe_trigger_alarm_row_long_press = lambda _now: False
        wave_calls: list[str] = []
        update_calls: list[str] = []
        visual._update_wave_values = lambda: wave_calls.append("wave")
        visual.update = lambda: update_calls.append("update")

        with patch("local.island_stack_visual.time.monotonic", return_value=10.016):
            visual._tick()

        self.assertEqual(wave_calls, ["wave"])
        self.assertEqual(update_calls, ["update"])
        self.assertGreater(visual.scroll_phase, 0.0)
        self.assertGreater(visual.ambient_phase, 0.0)

    def test_active_hover_animation_keeps_configured_render_frame(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.last_tick_at = 10.0
        visual.last_render_update_at = 10.0
        visual.animation_fps = 60
        visual.animation_speed = 1.0
        visual.location_mode = "free"
        visual.scene_suppressed = False
        visual.pointer_pos = QPointF(-1.0, -1.0)
        visual.barrage_clock = 0.0
        visual.barrage_overlay = None
        visual.super_topmost_enabled = False
        visual.media_is_playing = True
        visual.timed_lyrics = []
        visual.lyric_changed_at = 10.0
        visual.lyrics = ["line"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.hovered_key = "music"
        visual.hover_armed_key = ""
        visual.hover_started_at = 10.0
        visual.hover_bounce_until = 10.4
        visual.stack_bounce_until = 0.0
        visual.expanded = False
        visual.plugin_panel_open = False
        visual.left_stack_at = 0.0
        visual.scroll_phase = 0.0
        visual.ambient_phase = 0.0
        visual.plugin_time_module_animation = {}
        visual.plugin_alarm_editor_spin_animations = {}
        visual.plugin_alarm_editor_page_animation = {}
        visual.plugin_alarm_editor_reflow_animation = {}
        visual.scene_transition_mode = ""
        visual.surface_transition_started_at = 0.0
        visual.album_island_transition_started_at = 0.0
        visual._sync_music_background_to_theme = lambda: False
        visual._refresh_media_data = lambda _now: None
        visual._update_scene_state = lambda _now: None
        visual._prune_expired_messages = lambda _now: None
        visual._ensure_fullscreen_plugin_barrage_messages = lambda _now: None
        visual._sync_fullscreen_plugin_alert_overlay = lambda _now: None
        visual._point_in_barrage = lambda _point: False
        visual._hover_auto_activation_allowed = lambda _key: False
        visual._maybe_trigger_alarm_row_long_press = lambda _now: False
        wave_calls: list[str] = []
        update_calls: list[str] = []
        visual._update_wave_values = lambda: wave_calls.append("wave")
        visual.update = lambda: update_calls.append("update")

        with patch("local.island_stack_visual.time.monotonic", return_value=10.016):
            visual._tick()

        self.assertEqual(wave_calls, ["wave"])
        self.assertEqual(update_calls, ["update"])

    def test_media_refresh_forces_visible_frame_even_when_stable(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.last_tick_at = 10.0
        visual.last_render_update_at = 10.0
        visual.animation_fps = 60
        visual.animation_speed = 1.0
        visual.location_mode = "free"
        visual.scene_suppressed = False
        visual.pointer_pos = QPointF(-1.0, -1.0)
        visual.barrage_clock = 0.0
        visual.barrage_overlay = None
        visual.super_topmost_enabled = False
        visual.media_is_playing = True
        visual.timed_lyrics = []
        visual.lyric_changed_at = 10.0
        visual.lyrics = ["line"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.hovered_key = ""
        visual.hover_armed_key = ""
        visual.hover_started_at = 0.0
        visual.hover_bounce_until = 0.0
        visual.stack_bounce_until = 0.0
        visual.expanded = False
        visual.plugin_panel_open = False
        visual.left_stack_at = 0.0
        visual.scroll_phase = 0.0
        visual.ambient_phase = 0.0
        visual.plugin_time_module_animation = {}
        visual.plugin_alarm_editor_spin_animations = {}
        visual.plugin_alarm_editor_page_animation = {}
        visual.plugin_alarm_editor_reflow_animation = {}
        visual.scene_transition_mode = ""
        visual.surface_transition_started_at = 0.0
        visual.album_island_transition_started_at = 0.0
        visual._sync_music_background_to_theme = lambda: False
        visual._refresh_media_data = lambda _now: True
        visual._update_scene_state = lambda _now: None
        visual._prune_expired_messages = lambda _now: None
        visual._ensure_fullscreen_plugin_barrage_messages = lambda _now: None
        visual._sync_fullscreen_plugin_alert_overlay = lambda _now: None
        visual._point_in_barrage = lambda _point: False
        visual._maybe_trigger_alarm_row_long_press = lambda _now: False
        wave_calls: list[str] = []
        update_calls: list[str] = []
        visual._update_wave_values = lambda: wave_calls.append("wave")
        visual.update = lambda: update_calls.append("update")

        with patch("local.island_stack_visual.time.monotonic", return_value=10.016):
            visual._tick()

        self.assertEqual(wave_calls, ["wave"])
        self.assertEqual(update_calls, ["update"])

    def test_tasker_follow_skips_stable_material_refresh(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.expanded = True
        visual.taskbar_position_ratio = 0.5
        visual.taskbar_manual_width = 0.0
        visual.taskbar_selected_drop_zone = QRectF()
        visual.saved_window_pos = None
        visual.taskbar_follow_last_key = ""
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 650
        visual.mapToGlobal = lambda _point: QPoint(960, 1080)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, _taskbar: QRectF(180.0, 1040.0, 1500.0, 40.0)
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: False
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: False
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 760.0
        visual.move = lambda _pos: None
        material_calls: list[str] = []
        visual._update_taskbar_material_for_screen = lambda *_args: material_calls.append("material")

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            with patch("local.island_stack_visual.time.monotonic", return_value=200.0):
                self.assertTrue(visual._refresh_taskbar_follow_position())
            with patch("local.island_stack_visual.time.monotonic", return_value=200.05):
                self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertEqual(material_calls, ["material"])

    def test_taskbar_material_exclusion_uses_tasker_slot_without_rebuilding_layout(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_selected_drop_zone = QRectF(500.0, 1042.0, 420.0, 34.0)
        visual.motions = {}
        visual._u = lambda value: float(value)
        visual.mapToGlobal = lambda _point: QPoint(0, 0)
        rebuild_calls: list[str] = []

        def rebuild_layout():
            rebuild_calls.append("layout")
            return QRectF(500.0, 1042.0, 420.0, 34.0)

        visual._current_main_global_rect = rebuild_layout

        rects = visual._taskbar_material_exclusion_rects()

        self.assertEqual(rebuild_calls, [])
        self.assertTrue(rects)
        self.assertTrue(any(rect.contains(visual.taskbar_selected_drop_zone.center()) for rect in rects))

    def test_tasker_vertical_direction_uses_taskbar_position_not_standalone_auto(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.expanded = True
        visual.persistent_plugin = False
        visual.messages = []
        visual.stack_direction = 1
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.last_taskbar_rect = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._u = lambda value: float(value)
        visual.mapToGlobal = lambda _point: QPoint(0, 0)

        direction = visual._vertical_stack_direction(IslandKind.MUSIC, 176.0, QRect(0, 0, 1920, 1080))

        self.assertEqual(direction, -1)

    def test_tasker_window_move_treats_one_pixel_jitter_as_same_position(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.saved_window_pos = (100, 200)
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        moved = visual._move_taskbar_window_if_needed(QPoint(101, 199))

        self.assertFalse(moved)
        self.assertEqual(moves, [])

    def test_scene_state_probe_keeps_refresh_interval_lazy(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_checked_at = 10.0
        visual._detect_scene_suppression = lambda: (_ for _ in ()).throw(
            AssertionError("Scene suppression probe should stay throttled")
        )

        visual._update_scene_state(10.39)

        self.assertEqual(visual.scene_checked_at, 10.0)

    def test_tasker_scene_suppression_ignores_fullscreen_foreground(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.scene_checked_at = -10.0
        visual.scene_suppressed = False
        visual.scene_transition_mode = ""
        visual.hovered_key = "music"
        visual.hover_armed_key = "music"
        visual.left_stack_at = 1.0
        visual.group_frame_mask_path = None
        visual._detect_scene_suppression = lambda: (True, "fullscreen")
        visual._set_input_passthrough = lambda _enabled: (_ for _ in ()).throw(AssertionError("Tasker should not passthrough on fullscreen"))
        visual.clearMask = lambda: None
        visual.update = lambda: None

        visual._update_scene_state(1.0)

        self.assertFalse(visual.scene_suppressed)
        self.assertEqual(visual.scene_reason, "")

    def test_tasker_follow_refresh_repositions_when_taskbar_moves(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_position_ratio = 0.8
        visual.taskbar_manual_width = 0.0
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(840, 1040)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1000.0, 1920.0, 80.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, _taskbar: QRectF(180.0, 1000.0, 1500.0, 80.0)
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: False
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: False
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_kind = lambda _kind, _stack_w: (300.0, 62.0)
        visual._main_top_for_direction = lambda *_args: 10.0
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertTrue(moves)
        self.assertAlmostEqual(visual.taskbar_selected_drop_zone.height(), 80.0)
        self.assertLessEqual(moves[-1].y() + 10.0 + visual.taskbar_selected_drop_zone.height(), 1080.0)

    def test_tasker_follow_uses_hidden_taskbar_fallback_without_hiding_window(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_position_ratio = 0.5
        visual.taskbar_manual_width = 0.0
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(840, 1040)
        visual._taskbar_rect_for_screen = lambda _screen: None
        visual._taskbar_hidden_fallback_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_taskbar_anchor = lambda _kind, _stack_w, _taskbar: (300.0, 62.0)
        visual._main_top_for_direction = lambda *_args: 10.0
        moves: list[QPoint] = []
        visual._move_taskbar_window_if_needed = lambda pos: moves.append(QPoint(pos)) or True
        visual.save_last_position = lambda _pos: None
        calls: list[str] = []
        visual.hide = lambda: calls.append("hide")
        visual.show = lambda: calls.append("show")

        screen = FakeScreen()
        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=screen):
            self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertEqual(calls, [])
        self.assertTrue(moves)
        self.assertFalse(getattr(visual, "taskbar_hidden_by_follow", False))

        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=screen):
            self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertNotIn("hide", calls)
        self.assertFalse(getattr(visual, "taskbar_hidden_by_follow", True))

    def test_tasker_follow_moves_inside_hidden_autohide_taskbar_below_screen(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_position_ratio = 0.5
        visual.taskbar_manual_width = 0.0
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(960, 1060)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, _taskbar: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: {
            "auto_hide": True,
            "rect": QRectF(0.0, 1040.0, 1920.0, 40.0),
            "edge": "bottom",
        }
        visual._windows_taskbar_rect_for_screen = lambda _screen: None
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: True
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: True
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_taskbar_anchor = lambda _kind, _stack_w, _taskbar: (300.0, 34.0)
        visual._main_top_for_direction = lambda *_args: 10.0
        visual.save_last_position = lambda _pos: None
        moves: list[QPoint] = []
        visual._move_taskbar_window_if_needed = lambda pos: moves.append(QPoint(pos)) or True

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertTrue(moves)
        self.assertGreater(moves[-1].y() + 10.0 + 34.0, 1080.0)

    def test_tasker_follow_reinforces_topmost_after_refresh_move(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_position_ratio = 0.5
        visual.taskbar_manual_width = 0.0
        visual.taskbar_follow_last_key = ""
        visual.taskbar_follow_material_last_at = 0.0
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(960, 1060)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_drag_track_rect_for_screen = lambda _screen, _taskbar: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: False
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: False
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_taskbar_anchor = lambda _kind, _stack_w, _taskbar: (300.0, 34.0)
        visual._main_top_for_direction = lambda *_args: 10.0
        visual.save_last_position = lambda _pos: None
        visual._move_taskbar_window_if_needed = lambda _pos: True
        visual._set_taskbar_follow_hidden = lambda _hidden: None
        topmost_calls: list[str] = []
        visual._force_super_topmost = lambda: topmost_calls.append("topmost")

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertEqual(topmost_calls, ["topmost"])

    def test_tasker_follow_refresh_moves_immediately_without_lagging_animation(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_position_ratio = 0.5
        visual.taskbar_manual_width = 0.0
        visual.taskbar_follow_last_key = ""
        visual.taskbar_follow_material_last_at = 0.0
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(960, 1060)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_drag_track_rect_for_screen = lambda _screen, _taskbar: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: False
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: False
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_taskbar_anchor = lambda _kind, _stack_w, _taskbar: (300.0, 34.0)
        visual._main_top_for_direction = lambda *_args: 10.0
        visual.save_last_position = lambda _pos: None
        visual._set_taskbar_follow_hidden = lambda _hidden: None
        visual._force_super_topmost = lambda: None
        immediate_moves: list[QPoint] = []
        visual._move_taskbar_window_if_needed = lambda pos: immediate_moves.append(QPoint(pos)) or True
        animations: list[QPoint] = []
        visual._animate_taskbar_follow_to_pos = lambda pos: animations.append(QPoint(pos)) or True

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertTrue(immediate_moves)
        self.assertEqual(animations, [])

    def test_tasker_follow_target_builder_unifies_visible_and_hidden_autohide_layout(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_position_ratio = 0.5
        visual.taskbar_manual_width = 0.0
        visual.taskbar_follow_base_rect = QRectF()
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual._taskbar_drag_track_rect_for_screen = lambda _screen, taskbar: QRectF(taskbar)
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: True
        visual._taskbar_autohide_visible_follow_lift = lambda _taskbar, _geo: 4.0
        visual._taskbar_bottom_boundary_extension_for_screen = lambda _screen, _taskbar: 40.0
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_taskbar_anchor = lambda _kind, _stack_w, _taskbar: (300.0, 40.0)
        visual._main_top_for_direction = lambda *_args: 10.0

        appbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_follow_rect_for_current_visibility = lambda _screen, _taskbar, force_visible=False: (QRectF(appbar), False)
        visible = visual._taskbar_follow_target_for_screen(FakeScreen(), appbar)
        visual._taskbar_follow_rect_for_current_visibility = lambda _screen, _taskbar, force_visible=False: (QRectF(0.0, 1080.0, 1920.0, 40.0), True)
        hidden = visual._taskbar_follow_target_for_screen(FakeScreen(), appbar)

        self.assertEqual(visible.main_height, hidden.main_height)
        self.assertEqual(visible.zone_rect.width(), hidden.zone_rect.width())
        self.assertNotEqual(visible.window_pos.y(), hidden.window_pos.y())
        self.assertTrue(hidden.hidden_autohide)

    def test_tasker_autohide_binary_follow_uses_follow_animation(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_position_ratio = 0.5
        visual.taskbar_manual_width = 0.0
        visual.taskbar_follow_last_key = ""
        visual.taskbar_follow_material_last_at = 0.0
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(960, 1060)
        appbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(appbar)
        visual._taskbar_drag_track_rect_for_screen = lambda _screen, _taskbar: QRectF(appbar)
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: True
        visual._taskbar_follow_rect_for_current_visibility = lambda _screen, _taskbar, force_visible=False: (QRectF(0.0, 1080.0, 1920.0, 40.0), True)
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_taskbar_anchor = lambda _kind, _stack_w, _taskbar: (300.0, 34.0)
        visual._main_top_for_direction = lambda *_args: 10.0
        visual.save_last_position = lambda _pos: None
        visual._set_taskbar_follow_hidden = lambda _hidden: None
        visual._force_super_topmost = lambda: None
        immediate_moves: list[QPoint] = []
        visual._move_taskbar_window_if_needed = lambda pos: immediate_moves.append(QPoint(pos)) or True
        animations: list[QPoint] = []
        visual._animate_taskbar_follow_to_pos = lambda pos: animations.append(QPoint(pos)) or True

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertEqual(immediate_moves, [])
        self.assertTrue(animations)

    def test_taskbar_follow_animation_duration_stays_responsive(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.animation_speed = 1.0

        self.assertGreaterEqual(visual._taskbar_follow_animation_duration_ms(QPoint(0, 0), QPoint(0, 40)), 45)
        self.assertLessEqual(visual._taskbar_follow_animation_duration_ms(QPoint(0, 0), QPoint(0, 40)), 72)
        self.assertLessEqual(visual._taskbar_follow_animation_duration_ms(QPoint(0, 0), QPoint(420, 40)), 86)

    def test_taskbar_dock_animation_duration_stays_responsive(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.animation_speed = 1.0

        self.assertLessEqual(visual._taskbar_dock_animation_duration_ms(), 80)

    def test_tasker_tick_refreshes_follow_on_frame_for_responsive_tracking(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.last_tick_at = 100.0
        visual.animation_speed = 1.0
        visual.location_mode = "tasker"
        visual.drag_candidate = False
        visual.taskbar_follow_last_at = 100.0
        visual.scene_suppressed = False
        visual.pointer_pos = QPointF(-1.0, -1.0)
        visual.barrage_clock = 0.0
        visual.super_topmost_enabled = False
        visual.media_is_playing = False
        visual.timed_lyrics = []
        visual.lyric_changed_at = 100.0
        visual.lyrics = ["line"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.hovered_key = ""
        visual.hover_armed_key = ""
        visual.hover_started_at = 0.0
        visual.expanded = False
        visual.plugin_panel_open = False
        visual.left_stack_at = 0.0
        visual.scroll_phase = 0.0
        visual.ambient_phase = 0.0
        visual._sync_music_background_to_theme = lambda: False
        visual._refresh_media_data = lambda _now: None
        visual._update_scene_state = lambda _now: None
        visual._prune_expired_messages = lambda _now: None
        visual._ensure_fullscreen_plugin_barrage_messages = lambda _now: None
        visual._sync_fullscreen_plugin_alert_overlay = lambda _now: None
        visual._point_in_barrage = lambda _point: False
        visual._maybe_trigger_alarm_row_long_press = lambda _now: False
        visual._update_wave_values = lambda: None
        visual.update = lambda: None
        refresh_calls: list[str] = []
        visual._refresh_taskbar_follow_position = lambda: refresh_calls.append("refresh") or True

        with patch("local.island_stack_visual.time.monotonic", return_value=100.02):
            visual._tick()

        self.assertEqual(refresh_calls, ["refresh"])

    def test_tasker_topmost_reinforce_interval_is_short_for_taskbar_input(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"

        self.assertLessEqual(visual._topmost_reinforce_interval(), 0.55)

    def test_taskbar_rect_uses_appbar_rect_when_autohide_shell_is_tiny(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

            def availableGeometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._windows_taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1078.0, 1920.0, 2.0)
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: {
            "auto_hide": True,
            "rect": QRectF(0.0, 1040.0, 1920.0, 40.0),
        }

        rect = visual._taskbar_rect_for_screen(FakeScreen())

        self.assertIsNotNone(rect)
        self.assertAlmostEqual(rect.top(), 1040.0)
        self.assertAlmostEqual(rect.height(), 40.0)

    def test_taskbar_rect_uses_appbar_rect_when_autohide_shell_is_missing(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

            def availableGeometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._windows_taskbar_rect_for_screen = lambda _screen: None
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: {
            "auto_hide": True,
            "rect": QRectF(0.0, 1040.0, 1920.0, 40.0),
            "edge": "bottom",
        }

        rect = visual._taskbar_rect_for_screen(FakeScreen())

        self.assertIsNotNone(rect)
        self.assertAlmostEqual(rect.top(), 1040.0)
        self.assertAlmostEqual(rect.height(), 40.0)

    def test_taskbar_hidden_fallback_preserves_vertical_taskbar_edge(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.last_taskbar_rect = QRectF(0.0, 0.0, 48.0, 1080.0)

        rect = visual._taskbar_hidden_fallback_rect_for_screen(FakeScreen())

        self.assertAlmostEqual(rect.left(), 0.0)
        self.assertAlmostEqual(rect.top(), 0.0)
        self.assertAlmostEqual(rect.width(), 48.0)
        self.assertAlmostEqual(rect.height(), 1080.0)

    def test_standalone_drag_direction_stays_locked_while_dragging(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.stack_direction = 1
        visual.drag_candidate = True
        visual.dragging = True
        visual._u = lambda value: float(value)

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            first = visual._standalone_drag_direction(QPoint(100, 715))
            second = visual._standalone_drag_direction(QPoint(100, 725))
            third = visual._standalone_drag_direction(QPoint(100, 1040))

        self.assertEqual(first, 1)
        self.assertEqual(second, 1)
        self.assertEqual(third, 1)

    def test_dragging_hides_auxiliary_visuals_but_keeps_current_main_island(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = False
        visual.expanded = True
        visual.dragging = True
        visual.drag_press_key = "plugin"
        visual.stack_direction = 1
        visual.drag_auto_stack_direction = 1
        visual.messages = [Message("WeChat", "Alice", "hello", QColor(1, 2, 3), QColor(4, 5, 6))]
        visual._u = lambda value: float(value)
        visual._main_kind = lambda: IslandKind.PLUGIN
        visual._current_screen_geometry = lambda: QRect(0, 0, 1920, 1080)
        visual._stack_target_width = lambda _kind: 420.0
        visual._stack_center_x = lambda _width: 470.0
        visual._taskbar_compact_active = lambda: False
        visual._standalone_width_limit = lambda _center_x, _screen_geo: 420.0
        visual._vertical_stack_direction = lambda *_args: -1
        visual._main_size_for_kind = lambda _kind, _stack_w: (420.0, 48.0)
        visual._main_top_for_direction = lambda _kind, _height, direction: 72.0 if direction >= 0 else 180.0
        visual._plugin_enabled = lambda: True
        visual._plugin_panel_visible = lambda: True
        visual._plugin_panel_height = lambda _width: 156.0
        visual._active_plugin_alerts = lambda _limit, include_expanded=False: []
        visual._taskbar_main_overlay_alert = lambda: None
        visual._folded_message_limit = lambda _kind: 2

        visuals = visual._build_visuals()

        self.assertEqual([(item.key, item.kind, item.z_index) for item in visuals], [("plugin", IslandKind.PLUGIN, 10)])
        self.assertAlmostEqual(visuals[0].rect.top(), 72.0)

    def test_drag_start_collapses_expanded_music_and_rescales_drag_offset(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.finder_mode = False
        visual.plugin_panel_open = True
        visual.drag_press_key = "music"
        visual.drag_press_main_offset = QPointF(210.0, 88.0)
        visual.drag_press_main_global_rect = QRectF(710.0, 72.0, 420.0, 176.0)
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_kind = lambda _kind, _stack_w: (420.0, 176.0) if visual.expanded else (420.0, 46.0)

        visual._collapse_expanded_for_drag()

        self.assertFalse(visual.expanded)
        self.assertFalse(visual.plugin_panel_open)
        self.assertAlmostEqual(visual.drag_press_main_offset.x(), 210.0)
        self.assertAlmostEqual(visual.drag_press_main_offset.y(), 23.0)

    def test_taskbar_expanded_material_tokens_follow_sampled_taskbar_color(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_material_color = QColor(238, 240, 244)
        visual.taskbar_material_is_light = True

        tokens = visual._taskbar_material_frame_tokens()

        self.assertGreater(tokens["fill_top"].red(), 210)
        self.assertLess(tokens["stroke"].alpha(), 120)

    def test_taskbar_message_block_uses_app_color_without_sender_initial(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        msg = Message("微信", "张三", "hello", QColor(75, 210, 110), QColor(75, 210, 110))

        model = visual._taskbar_message_block_model(msg, QRectF(0.0, 0.0, 34.0, 34.0))

        self.assertEqual(model["fill"], QColor(75, 210, 110))
        self.assertNotEqual(model["glyph"], "张")
        self.assertNotEqual(model["outer"], QColor(0, 0, 0, 238))

    def test_tasker_compact_album_island_uses_available_side_slots(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_island_secondary_style = "perspective_dim"
        visual.album_island_perspective_amount = 100
        visual.album_island_transition_started_at = 1.0
        visual.album_island_transition_direction = 1
        visual._u = lambda value: float(value)
        art = QImage(16, 16, QImage.Format.Format_ARGB32)
        art.fill(QColor(120, 80, 50))
        previous = QImage(16, 16, QImage.Format.Format_ARGB32)
        previous.fill(QColor(50, 90, 140))
        next_art = QImage(16, 16, QImage.Format.Format_ARGB32)
        next_art.fill(QColor(180, 90, 120))
        visual.album_art = art
        visual.album_history = [{"track_id": "old", "album_art": previous}]
        visual.album_queue = [{"track_id": "next", "album_art": next_art}]

        with patch("local.island_stack_visual.time.monotonic", return_value=1.08):
            slots = visual._album_island_slots(QRectF(0.0, 0.0, 260.0, 34.0), reserve_text=True)

        self.assertEqual([int(slot["side"]) for slot in slots], [-1, 0, 1])

    def test_taskbar_message_rails_cap_at_three_and_stay_outside_main_shell(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_message_compact_style = "strip"
        visual.music_playing = False
        visual.persistent_plugin = False
        visual.messages = [
            Message("微信", str(index), "hello", QColor(75, 210, 110), QColor(75, 210, 110))
            for index in range(5)
        ]
        visual._u = lambda value: float(value)
        main_rect = QRectF(500.0, 1044.0, 280.0, 34.0)

        rail = visual._taskbar_message_rect(main_rect, 0, 3, 1)
        second = visual._taskbar_message_rect(main_rect, 1, 3, 1)

        self.assertEqual(visual._folded_message_limit(IslandKind.MESSAGE), 3)
        self.assertGreaterEqual(rail.left(), main_rect.right())
        self.assertAlmostEqual(rail.left() - main_rect.right(), 9.0, delta=0.1)
        self.assertGreaterEqual(second.left(), rail.right())
        self.assertGreaterEqual(second.left(), main_rect.right())

    def test_taskbar_material_sampling_skips_own_island_region(self):
        class FakeScreen:
            def __init__(self, exclusion: QRectF):
                self.exclusion = QRectF(exclusion)
                self.samples: list[QRectF] = []

            def grabWindow(self, _win_id, x, y, width, height):
                sample = QRectF(float(x), float(y), float(width), float(height))
                self.samples.append(sample)
                image = QImage(max(1, int(width)), max(1, int(height)), QImage.Format.Format_ARGB32)
                center = sample.center()
                if self.exclusion.contains(center):
                    image.fill(QColor(75, 210, 110))
                else:
                    image.fill(QColor(36, 38, 42))
                return QPixmap.fromImage(image)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        exclusion = QRectF(920.0, 1000.0, 160.0, 80.0)
        visual._taskbar_material_exclusion_rects = lambda: [QRectF(exclusion)]
        screen = FakeScreen(exclusion)

        color = visual._sample_taskbar_material_color(screen, QRectF(0.0, 1000.0, 2000.0, 80.0))

        self.assertEqual(color, QColor(36, 38, 42))
        self.assertTrue(screen.samples)
        self.assertFalse(exclusion.contains(screen.samples[-1].center()))

    def test_taskbar_material_sampling_is_throttled_between_animation_ticks(self):
        class FakeScreen:
            def __init__(self):
                self.calls = 0

            def grabWindow(self, _win_id, _x, _y, width, height):
                self.calls += 1
                image = QImage(max(1, int(width)), max(1, int(height)), QImage.Format.Format_ARGB32)
                image.fill(QColor(36, 38, 42))
                return QPixmap.fromImage(image)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.taskbar_material_color = QColor()
        visual.taskbar_material_is_light = False
        visual.taskbar_material_sampled_at = 0.0
        visual.taskbar_material_sample_key = ""
        visual._taskbar_material_exclusion_rects = lambda: []
        screen = FakeScreen()
        taskbar = QRectF(0.0, 1000.0, 2000.0, 80.0)

        with patch("local.island_stack_visual.time.monotonic", return_value=100.0):
            visual._update_taskbar_material_for_screen(screen, taskbar)
        first_refresh_calls = screen.calls
        with patch("local.island_stack_visual.time.monotonic", return_value=100.05):
            visual._update_taskbar_material_for_screen(screen, taskbar)

        self.assertGreater(first_refresh_calls, 0)
        self.assertEqual(screen.calls, first_refresh_calls)

    def test_taskbar_expanded_frame_targets_visual_rects_not_child_spring_rects(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.finder_mode = False
        visual.location_mode = "tasker"
        visual.taskbar_corner_style = "win11"
        visual.taskbar_material_color = QColor(36, 38, 42)
        visual.taskbar_material_is_light = False
        visual.group_frame_motion = None
        visual.group_frame_mask_path = None
        visual.finder_hover_rect = QRectF()
        visual._u = lambda value: float(value)
        image = QImage(900, 260, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        target_rect = QRectF(320.0, 120.0, 420.0, 52.0)
        old_motion = SpringRect(QRectF(40.0, 20.0, 18.0, 18.0))
        old_motion.opacity = 1.0
        items = [(StackVisual("music", IslandKind.MUSIC, target_rect, False, None, 10), old_motion)]

        visual._paint_taskbar_group_frame(painter, items, 1.0)
        painter.end()

        self.assertTrue(visual.finder_hover_rect.contains(target_rect.center()))

    def test_taskbar_expanded_frame_ignores_transient_plugin_alerts(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.finder_mode = False
        visual.location_mode = "tasker"
        visual.taskbar_corner_style = "win11"
        visual.taskbar_material_color = QColor(36, 38, 42)
        visual.taskbar_material_is_light = False
        visual.group_frame_motion = None
        visual.group_frame_mask_path = None
        visual.finder_hover_rect = QRectF()
        visual._u = lambda value: float(value)
        image = QImage(1200, 320, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        main_rect = QRectF(420.0, 140.0, 360.0, 48.0)
        alert_rect = QRectF(980.0, 140.0, 120.0, 42.0)
        main_motion = SpringRect(main_rect)
        main_motion.opacity = 1.0
        alert_motion = SpringRect(alert_rect)
        alert_motion.opacity = 1.0
        items = [
            (StackVisual("music", IslandKind.MUSIC, main_rect, False, None, 10), main_motion),
            (StackVisual("plugin-alert:time", IslandKind.PLUGIN_ALERT, alert_rect, False, None, 8), alert_motion),
        ]

        visual._paint_taskbar_group_frame(painter, items, 1.0)
        painter.end()

        self.assertTrue(visual.finder_hover_rect.contains(main_rect.center()))
        self.assertLess(visual.finder_hover_rect.right(), alert_rect.left())

    def test_finder_group_frame_cradles_compact_music_from_lower_half_when_bar_sample_is_tall(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = False
        visual.finder_mode = True
        visual.location_mode = "finder"
        visual.group_frame_motion = None
        visual.group_frame_mask_path = None
        visual.finder_hover_rect = QRectF()
        visual.VISUAL_SCALE = 1.0
        visual._u = lambda value: float(value)
        visual.width = lambda: 780
        visual.finder_material = FinderGlassMaterial(
            top_color=QColor(244, 245, 245, 120),
            mid_color=QColor(176, 181, 181, 132),
            bottom_color=QColor(158, 164, 164, 140),
            highlight_alpha=88,
            border_alpha=166,
            shadow_alpha=50,
            bar_height=54.0,
            blur_value=11,
            source="test",
        )
        visual._has_current_two_line_lyric = lambda: False
        image = QImage(780, 170, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        main_rect = QRectF(48.0, 30.0, 500.0, 40.0)
        alert_rect = QRectF(558.0, 31.0, 144.0, 38.0)
        main_motion = SpringRect(main_rect)
        main_motion.opacity = 1.0
        alert_motion = SpringRect(alert_rect)
        alert_motion.opacity = 1.0
        items = [
            (StackVisual("music", IslandKind.MUSIC, main_rect, False, None, 10), main_motion),
            (StackVisual("plugin-alert:weather", IslandKind.PLUGIN_ALERT, alert_rect, False, None, 8), alert_motion),
        ]

        visual._paint_myfinder_group_frame(painter, items, 1.0)
        painter.end()

        frame_top = visual.group_frame_motion.rect.top()
        self.assertGreaterEqual(frame_top, main_rect.top() + main_rect.height() * 0.36)
        self.assertLess(frame_top, main_rect.bottom())

    def test_finder_compact_music_sits_near_top_while_frame_remains_split(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = False
        visual.finder_mode = True
        visual.location_mode = "finder"
        visual.group_frame_motion = None
        visual.group_frame_mask_path = None
        visual.finder_hover_rect = QRectF()
        visual.VISUAL_SCALE = 1.0
        visual._u = lambda value: float(value)
        visual.width = lambda: 760
        visual.finder_material = FinderGlassMaterial(
            top_color=QColor(244, 245, 245, 120),
            mid_color=QColor(176, 181, 181, 132),
            bottom_color=QColor(158, 164, 164, 140),
            highlight_alpha=88,
            border_alpha=166,
            shadow_alpha=50,
            bar_height=54.0,
            blur_value=11,
            source="test",
        )
        visual._has_current_two_line_lyric = lambda: False
        image = QImage(760, 150, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        main_top = visual._finder_stack_top_y()
        main_rect = QRectF(76.0, main_top, 560.0, 62.0)
        motion = SpringRect(main_rect)
        motion.opacity = 1.0

        visual._paint_myfinder_group_frame(
            painter,
            [(StackVisual("music", IslandKind.MUSIC, main_rect, False, None, 10), motion)],
            1.0,
        )
        painter.end()

        frame_top = visual.group_frame_target_rect.top()
        self.assertLessEqual(main_top, 8.0)
        self.assertGreaterEqual(frame_top, main_rect.top() + main_rect.height() * 0.38)
        self.assertLessEqual(frame_top, main_rect.top() + main_rect.height() * 0.44)

    def test_tasker_compact_drops_stale_side_motion_without_exit_ghost(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"

        drop = getattr(visual, "_taskbar_should_drop_stale_motion_key", lambda _key: False)

        self.assertTrue(drop("plugin-alert:time"))
        self.assertTrue(drop("taskbar-overlay:time"))
        self.assertTrue(drop("message-dot:wechat"))
        self.assertFalse(drop("music"))

    def test_tasker_compact_skips_floating_message_dot_backing(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual._u = lambda value: float(value)
        main_rect = QRectF(40.0, 20.0, 220.0, 34.0)
        dot_rect = QRectF(252.0, 26.0, 5.0, 24.0)
        main_motion = SpringRect(main_rect)
        main_motion.opacity = 1.0
        dot_motion = SpringRect(dot_rect)
        dot_motion.opacity = 1.0
        message = Message("WeChat", "Alice", "new", QColor(75, 210, 110), QColor(75, 210, 110))
        items = [
            (StackVisual("music", IslandKind.MUSIC, main_rect, False, None, 10), main_motion),
            (StackVisual("message-dot:wechat", IslandKind.MESSAGE, dot_rect, True, message, 14), dot_motion),
        ]
        image = QImage(320, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_dot_group_backing(painter, items)
        painter.end()

        self.assertEqual(image.pixelColor(254, 60).alpha(), 0)

    def test_tasker_notification_frame_wraps_side_rails_with_taskbar_material(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.expanded = False
        visual.taskbar_corner_style = "win11"
        visual.taskbar_material_color = QColor(36, 38, 42)
        visual.taskbar_material_is_light = False
        visual.taskbar_notification_frame_motion = None
        visual.taskbar_notification_frame_rect = QRectF()
        visual._u = lambda value: float(value)
        main_rect = QRectF(40.0, 20.0, 220.0, 34.0)
        message = Message("WeChat", "Alice", "new", QColor(75, 210, 110), QColor(75, 210, 110))
        rails = [
            StackVisual("plugin-alert:time", IslandKind.PLUGIN_ALERT, QRectF(269.0, 25.0, 5.0, 24.0), False, None, 8),
            StackVisual("message-dot:wechat", IslandKind.MESSAGE, QRectF(278.0, 25.0, 5.0, 24.0), True, message, 13),
        ]
        main_motion = SpringRect(main_rect)
        main_motion.opacity = 1.0
        items = [(StackVisual("music", IslandKind.MUSIC, main_rect, False, None, 10), main_motion)]
        for rail in rails:
            motion = SpringRect(rail.rect)
            motion.opacity = 1.0
            items.append((rail, motion))
        image = QImage(340, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_taskbar_notification_frame(painter, items, 1.0)
        painter.end()

        frame = visual.taskbar_notification_frame_rect
        self.assertFalse(frame.isNull())
        self.assertLessEqual(frame.left(), main_rect.right() - 12.0)
        self.assertLess(frame.left(), rails[0].rect.left())
        self.assertGreaterEqual(frame.right(), rails[-1].rect.right())
        self.assertAlmostEqual(frame.height(), main_rect.height(), delta=0.1)
        self.assertIn("_taskbar_material_frame_tokens", inspect.getsource(IslandStackVisual._paint_taskbar_material_frame))
        self.assertIn("_taskbar_right_extension_path", inspect.getsource(IslandStackVisual._paint_taskbar_notification_frame))

    def test_tasker_motion_uses_fast_stable_response(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.stack_bounce_until = 0.0

        params = getattr(visual, "_visual_motion_params", lambda _now: (0.34, 0.76))(10.0)

        self.assertLessEqual(params[0], 0.22)
        self.assertGreaterEqual(params[1], 0.84)

    def test_playback_and_finder_keep_full_animation_interval(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.animation_fps = 90
        visual.media_is_playing = True
        visual.finder_mode = False
        visual.drag_candidate = False
        visual.dragging = False
        visual.barrage_hovered = False
        visual.hover_bounce_until = 0.0
        visual.stack_bounce_until = 0.0
        visual.taskbar_left_handle_visible_until = 0.0
        visual.scene_transition_mode = ""
        visual.surface_transition_pixmap = QPixmap()
        visual.prev_lyric_index = 0
        visual.lyric_index = 0
        visual.lyric_transition_kind = ""
        visual._should_paint_album_island = lambda: False
        visual.plugin_time_module_animation = {}
        visual.plugin_alarm_editor_page_animation = {}
        visual.plugin_alarm_editor_reflow_animation = {}
        visual.plugin_alarm_editor_spin_animations = {}
        visual.music_control_press_started_at = {}

        self.assertTrue(visual._has_active_visual_animation(10.0))
        self.assertEqual(visual._stable_render_interval_ms(), visual._timer_interval_ms())
        self.assertEqual(visual._render_interval_seconds(False), visual._render_interval_seconds(True))

        visual.media_is_playing = False
        visual.finder_mode = True

        self.assertTrue(visual._has_active_visual_animation(10.0))

    def test_group_frame_target_reuses_tiny_tasker_jitter(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        previous = QRectF(100.0, 50.0, 320.0, 46.0)
        visual.group_frame_target_rect = QRectF(previous)
        visual.group_frame_target_mode_key = "tasker"

        coalesce = getattr(visual, "_coalesced_group_frame_target", lambda target, _mode: QRectF(target))
        tiny = coalesce(QRectF(101.0, 50.8, 319.4, 46.6), "tasker")
        large = coalesce(QRectF(116.0, 52.0, 320.0, 46.0), "tasker")

        self.assertEqual(tiny, previous)
        self.assertNotEqual(large, previous)

    def test_finder_group_frame_target_reuses_small_follow_jitter(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        previous = QRectF(100.0, 50.0, 320.0, 46.0)
        visual.group_frame_target_rect = QRectF(previous)
        visual.group_frame_target_mode_key = "finder"

        coalesce = getattr(visual, "_coalesced_group_frame_target", lambda target, _mode: QRectF(target))
        small = coalesce(QRectF(104.5, 50.0, 319.2, 47.0), "finder")
        large = coalesce(QRectF(109.0, 50.0, 320.0, 46.0), "finder")

        self.assertEqual(small, previous)
        self.assertNotEqual(large, previous)

    def test_finder_group_frame_does_not_double_main_top_as_bottom_padding(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = True
        visual.expanded = False
        visual.group_frame_motion = None
        visual.group_frame_mode_key = ""
        visual.group_frame_target_rect = QRectF()
        visual.group_frame_target_mode_key = ""
        visual.group_frame_mask_path = None
        visual.finder_hover_rect = QRectF()
        visual._u = lambda value: float(value)
        visual.width = lambda: 800
        visual._has_current_two_line_lyric = lambda: False
        visual.finder_material = type(
            "FakeFinderMaterial",
            (),
            {
                "bar_height": 34.0,
                "corner_radius": 16.0,
                "texture_image": QImage(),
                "bottom_color": QColor(94, 98, 102, 210),
                "mid_color": QColor(116, 120, 124, 210),
                "shadow_alpha": 88,
            },
        )()
        main_rect = QRectF(180.0, 32.0, 360.0, 46.0)
        motion = SpringRect(main_rect)
        motion.opacity = 1.0
        image = QImage(800, 160, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_myfinder_group_frame(
            painter,
            [(StackVisual("music", IslandKind.MUSIC, main_rect, False, None, 10), motion)],
            0.016,
        )
        painter.end()

        self.assertLessEqual(visual.group_frame_target_rect.bottom(), main_rect.bottom() + 9.0)

    def test_album_history_entry_downscales_large_art_for_memory(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "track-1"
        visual.track_title = "Song"
        visual.artist = "Artist"
        art = QImage(1200, 900, QImage.Format.Format_ARGB32)
        art.fill(QColor(80, 120, 180))
        visual.album_art = art
        visual.listened_seconds_total = 0.0

        entry = visual._current_album_history_entry()
        image = entry["album_art"]

        self.assertLessEqual(max(image.width(), image.height()), 256)

    def test_scaled_album_art_cache_keeps_only_small_recent_working_set(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._scaled_album_art_cache = {}
        art = QImage(320, 320, QImage.Format.Format_ARGB32)
        art.fill(QColor(80, 120, 180))
        image = QImage(260, 80, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)

        for index in range(31):
            visual._paint_cover(
                painter,
                QRectF(8.0 + index, 8.0, 42.0 + index, 42.0),
                6.0,
                art,
                cache_key=f"stress-{index}",
            )
        painter.end()

        self.assertEqual(len(visual._scaled_album_art_cache), visual.SCALED_ALBUM_ART_CACHE_LIMIT)
        self.assertLessEqual(sum(cover.sizeInBytes() for cover in visual._scaled_album_art_cache.values()), visual.SCALED_ALBUM_ART_CACHE_PIXEL_BUDGET)

    def test_lyric_layout_cache_keeps_small_recent_working_set(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._lyric_layout_cache = {}
        visual.ui_font_family = "PingFang SC"
        visual.high_quality_font_rendering = False
        visual.font_shrink_scale = 1.0
        visual.lyric_elastic_strength = 100
        visual.lyric_word_spacing = 100

        for index in range(72):
            visual._lyric_layout(f"line {index}", 16, True)

        self.assertLessEqual(len(visual._lyric_layout_cache), 48)

    def test_taskbar_group_frame_resets_when_target_jumps_from_stale_mode(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.finder_mode = False
        visual.location_mode = "tasker"
        visual.taskbar_corner_style = "win11"
        visual.taskbar_material_color = QColor(36, 38, 42)
        visual.taskbar_material_is_light = False
        visual.group_frame_motion = SpringRect(QRectF(0.0, 0.0, 1150.0, 220.0))
        visual.group_frame_motion.opacity = 1.0
        visual.group_frame_mode_key = "finder"
        visual.group_frame_mask_path = None
        visual.finder_hover_rect = QRectF()
        visual._u = lambda value: float(value)
        image = QImage(900, 260, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        main_rect = QRectF(360.0, 118.0, 300.0, 44.0)
        motion = SpringRect(main_rect)
        motion.opacity = 1.0

        visual._paint_taskbar_group_frame(painter, [(StackVisual("music", IslandKind.MUSIC, main_rect, False, None, 10), motion)], 1.0)
        painter.end()

        self.assertLess(visual.finder_hover_rect.width(), main_rect.width() + 60.0)
        self.assertAlmostEqual(visual.finder_hover_rect.center().x(), main_rect.center().x(), delta=20.0)

    def test_finder_frame_targets_visual_rects_not_child_spring_rects(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.finder_mode = True
        visual.location_mode = "finder"
        visual.group_frame_motion = None
        visual.group_frame_mask_path = None
        visual.finder_hover_rect = QRectF()
        visual.stack_direction = 1
        visual._u = lambda value: float(value)
        visual.width = lambda: 900
        visual.finder_material = FinderGlassMaterial(
            top_color=QColor(31, 32, 36, 235),
            mid_color=QColor(37, 38, 42, 235),
            bottom_color=QColor(44, 45, 50, 238),
            highlight_alpha=88,
            border_alpha=100,
            shadow_alpha=50,
            bar_height=44.0,
            blur_value=11,
            source="test",
            corner_radius=12.0,
            available=True,
        )
        visual._has_current_two_line_lyric = lambda: False
        image = QImage(900, 360, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        target_rect = QRectF(340.0, 118.0, 360.0, 64.0)
        old_motion = SpringRect(QRectF(32.0, 18.0, 20.0, 18.0))
        old_motion.opacity = 1.0
        items = [(StackVisual("music", IslandKind.MUSIC, target_rect, False, None, 10), old_motion)]

        visual._paint_myfinder_group_frame(painter, items, 1.0)
        painter.end()

        self.assertGreater(visual.finder_hover_rect.width(), target_rect.width())
        self.assertAlmostEqual(visual.finder_hover_rect.center().x(), target_rect.center().x(), delta=28.0)

    def test_manual_finder_toggle_resets_stale_tasker_frame_and_transition(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.attach_to_real_finder = False
        visual.manual_finder_available = False
        visual.taskbar_fusion_mode = "compact"
        visual.taskbar_preview_visible = True
        visual.taskbar_selected_drop_zone = QRectF(100.0, 100.0, 300.0, 40.0)
        visual.taskbar_drop_zones = [QRectF(100.0, 100.0, 300.0, 40.0)]
        visual.group_frame_motion = SpringRect(QRectF(0.0, 0.0, 1100.0, 240.0))
        visual.group_frame_mask_path = object()
        visual.group_frame_mode_key = "tasker"
        visual.finder_hover_rect = QRectF(20.0, 20.0, 400.0, 60.0)
        visual.interaction_mask_active = True
        visual.last_mask_rect = QRectF(20.0, 20.0, 400.0, 60.0)
        visual.surface_transition_pixmap = QPixmap(8, 8)
        visual.surface_transition_global_rect = QRectF(10.0, 10.0, 8.0, 8.0)
        visual.surface_transition_reason = "tasker"
        visual.hovered_key = "plugin"
        visual.hover_armed_key = "plugin"
        visual.left_stack_at = 10.0
        visual.drag_detach_global_rects = {"music": QRectF(10.0, 10.0, 30.0, 30.0)}
        visual._sync_taskbar_drop_overlay = lambda: None
        visual._pulse_stack = lambda: None
        visual._notify_settings_changed = lambda: None
        visual.update = lambda: None
        visual.clearMask = lambda: None

        visual.set_manual_finder_available(True, notify=False)

        self.assertEqual(visual.location_mode, "free")
        self.assertFalse(visual.finder_mode)
        self.assertEqual(visual.taskbar_fusion_mode, "off")
        self.assertFalse(visual.taskbar_preview_visible)
        self.assertIsNone(visual.group_frame_motion)
        self.assertIsNone(visual.group_frame_mask_path)
        self.assertTrue(visual.finder_hover_rect.isNull())
        self.assertFalse(visual.interaction_mask_active)
        self.assertTrue(visual.last_mask_rect.isNull())
        self.assertTrue(visual.surface_transition_pixmap.isNull())
        self.assertTrue(visual.surface_transition_global_rect.isNull())
        self.assertEqual(visual.surface_transition_reason, "")
        self.assertEqual(visual.hovered_key, "")
        self.assertEqual(visual.hover_armed_key, "")
        self.assertEqual(visual.drag_detach_global_rects, {})

    def test_finder_attach_disallowed_when_manual_finder_is_off(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.manual_finder_available = False
        visual.finder_material = FinderGlassMaterial(
            top_color=QColor(31, 32, 36, 235),
            mid_color=QColor(37, 38, 42, 235),
            bottom_color=QColor(44, 45, 50, 238),
            highlight_alpha=88,
            border_alpha=100,
            shadow_alpha=50,
            bar_height=44.0,
            blur_value=11,
            source="test",
            corner_radius=12.0,
            available=True,
        )

        self.assertFalse(visual._finder_attach_allowed())

        visual.manual_finder_available = True
        self.assertTrue(visual._finder_attach_allowed())

    def test_taskbar_left_handle_stays_visible_for_three_seconds_after_hover(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.hovered_key = ""
        visual.dragging = False
        visual.drag_press_key = ""
        visual.taskbar_left_handle_visible_until = 12.0

        with patch("local.island_stack_visual.time.monotonic", return_value=10.5):
            self.assertTrue(visual._taskbar_left_handle_visible())
        with patch("local.island_stack_visual.time.monotonic", return_value=13.2):
            self.assertFalse(visual._taskbar_left_handle_visible())

    def test_taskbar_left_handle_default_color_is_gray_and_configurable(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_left_handle_color = "#8E8E93"
        visual._effective_music_ambience_style = lambda: "pure_black"

        default_color = visual._taskbar_left_handle_color()
        visual.taskbar_left_handle_color = "#FF3366"
        custom_color = visual._taskbar_left_handle_color()

        self.assertEqual(default_color, QColor("#8E8E93"))
        self.assertEqual(custom_color, QColor("#FF3366"))

    def test_tasker_color_fill_does_not_restrict_music_background_style(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.lyric_flow_style = "wave_stream"
        visual.wave_color_fill_enabled = True
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        visual.set_music_ambience_style("sea_level", notify=False)

        self.assertEqual(visual.music_ambience_style, "sea_level")
        self.assertEqual(visual._effective_music_ambience_style(), "sea_level")

    def test_gelv_color_fill_does_not_restrict_music_background_style(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "free"
        visual.music_ambience_style = "pure_black"
        visual.lyric_flow_style = "gelv_songscatter"
        visual.gelv_color_fill_enabled = True
        visual._pulse_stack = lambda: None
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        visual.set_music_ambience_style("sea_level", notify=False)

        self.assertEqual(visual.music_ambience_style, "sea_level")
        self.assertEqual(visual._effective_music_ambience_style(), "sea_level")

    def test_enabling_gelv_color_fill_keeps_selected_music_background_style(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "free"
        visual.music_ambience_style = "duotone_full"
        visual.lyric_flow_style = "gelv_songscatter"
        visual.gelv_color_fill_enabled = False
        visual._pulse_stack = lambda: None
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        visual.set_gelv_color_fill_enabled(True, notify=False)

        self.assertEqual(visual.music_ambience_style, "duotone_full")

    def test_music_auto_detect_keeps_external_playback_visible_when_metadata_is_advancing(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = True
        visual.music_auto_detect = True
        visual.live_audio_available = False
        visual.media_is_playing = True
        visual.media_source_app = "NetEase"
        visual.track_title = "Song"
        visual.artist = "Artist"
        visual.media_duration_seconds = 180.0
        visual._estimated_media_position = lambda _now: 42.0

        self.assertTrue(visual._music_enabled_for_stack())

    def test_music_auto_detect_keeps_paused_media_visible_without_live_audio(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = True
        visual.music_auto_detect = True
        visual.live_audio_available = False
        visual.media_is_playing = False
        visual.track_title = "Paused song"
        visual.artist = "Artist"
        visual.lyrics = []
        visual.persistent_plugin = False
        visual.enabled_plugin_ids = set()
        visual.messages = []

        self.assertTrue(visual._music_enabled_for_stack())
        self.assertEqual(visual._main_kind(), IslandKind.MUSIC)

    def test_empty_display_defaults_to_visible_bar_when_audio_is_absent(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = True
        visual.music_auto_detect = True
        visual.live_audio_available = False
        visual.media_is_playing = True
        visual.track_title = "Song"
        visual.persistent_plugin = False
        visual.enabled_plugin_ids = set()
        visual.messages = []

        self.assertFalse(visual._music_enabled_for_stack())
        self.assertEqual(visual._empty_island_display_mode(), "visible_bar")
        self.assertEqual(visual._main_kind(), IslandKind.THIN_BAR)

    def test_empty_hidden_bar_setting_migrates_to_visible_bar(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.empty_island_display_mode = "hidden_bar"

        self.assertEqual(visual._empty_island_display_mode(), "visible_bar")

    def test_empty_text_mode_uses_black_text_island_sized_to_actual_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = True
        visual.music_auto_detect = True
        visual.live_audio_available = False
        visual.empty_island_display_mode = "text"
        visual.persistent_plugin = False
        visual.enabled_plugin_ids = set()
        visual.messages = []
        visual.lyrics = []
        visual.track_title = "Stale song"
        visual.artist = "Stale artist"
        visual.no_lyrics_text = "Idle"
        visual.expanded = False
        visual.location_mode = "free"
        visual.font_shrink_scale = 1.0
        visual.high_quality_font_rendering = False
        visual._u = lambda value: float(value)
        self.assertEqual(visual._main_kind().name, "EMPTY_TEXT")
        self.assertTrue(visual._empty_music_text_active())
        self.assertEqual(visual._empty_text_display_text(), "Idle")

        short_w, short_h = visual._main_size_for_kind(visual._main_kind(), 420.0)
        short_text_w = QFontMetrics(visual._font(14, QFont.Weight.Bold, shrink=False)).horizontalAdvance("Idle")
        self.assertAlmostEqual(short_w, max(56.0, short_text_w + 36.0), delta=2.0)
        self.assertEqual(short_h, 40.0)

        visual.no_lyrics_text = "Idle field with more chars"
        long_w, long_h = visual._main_size_for_kind(visual._main_kind(), 420.0)
        self.assertGreater(long_w, short_w)
        self.assertEqual(long_h, short_h)

        background, text_color = visual._empty_text_island_colors()
        self.assertLess(background.lightness(), 24)
        self.assertGreater(text_color.lightness(), 220)

    def test_plugin_island_prioritizes_over_empty_text_and_time_by_default(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = True
        visual.music_auto_detect = True
        visual.live_audio_available = False
        visual.persistent_plugin = True
        visual.enabled_plugin_ids = {"time"}
        visual.enabled_plugin_count = 1
        visual.messages = []

        visual.empty_island_display_mode = "text"
        self.assertEqual(visual._main_kind(), IslandKind.PLUGIN)

        visual.empty_island_display_mode = "time"
        self.assertEqual(visual._main_kind(), IslandKind.PLUGIN)

    def test_empty_display_can_be_preferred_over_plugin_when_configured(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = True
        visual.music_auto_detect = True
        visual.live_audio_available = False
        visual.persistent_plugin = True
        visual.enabled_plugin_ids = {"time"}
        visual.enabled_plugin_count = 1
        visual.messages = []
        visual.no_music_island_priority = "empty"

        visual.empty_island_display_mode = "text"
        self.assertEqual(visual._main_kind(), IslandKind.EMPTY_TEXT)

        visual.empty_island_display_mode = "time"
        self.assertEqual(visual._main_kind(), IslandKind.TIME)

    def test_empty_text_and_time_islands_match_content_without_scroll_cap(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = True
        visual.music_auto_detect = True
        visual.live_audio_available = False
        visual.empty_island_display_mode = "text"
        visual.persistent_plugin = False
        visual.enabled_plugin_ids = set()
        visual.messages = []
        visual.no_lyrics_text = "Idle field with enough text to be wider than the default narrow stack"
        visual.expanded = False
        visual.location_mode = "free"
        visual.font_shrink_scale = 1.0
        visual.high_quality_font_rendering = False
        visual.ui_font_family = "Microsoft YaHei UI"
        visual._u = lambda value: float(value)

        text_font = visual._empty_text_island_font()
        expected_text_w = QFontMetrics(text_font).horizontalAdvance(visual.no_lyrics_text) + 36.0
        text_w, text_h = visual._main_size_for_kind(visual._main_kind(), 120.0)

        self.assertEqual(text_font.weight(), QFont.Weight.Bold)
        self.assertEqual(text_font.families()[0], "Microsoft YaHei UI")
        self.assertAlmostEqual(text_w, expected_text_w, delta=2.0)
        self.assertGreater(text_w, 120.0)
        self.assertEqual(text_h, 40.0)

        visual.empty_island_display_mode = "time"
        visual.show_seconds = True
        with patch("local.island_stack_visual.datetime") as fake_datetime:
            fake_datetime.now.return_value = datetime(2026, 6, 4, 10, 11, 8).astimezone()
            time_font = visual._empty_time_island_font()
            expected_time_w = QFontMetrics(time_font).horizontalAdvance("10:11:08") + 34.0
            time_w, time_h = visual._main_size_for_kind(visual._main_kind(), 74.0)

        self.assertEqual(time_font.weight(), QFont.Weight.Bold)
        self.assertEqual(time_font.families()[0], "Microsoft YaHei UI")
        self.assertAlmostEqual(time_w, expected_time_w, delta=2.0)
        self.assertGreater(time_w, 74.0)
        self.assertEqual(time_h, 40.0)

    def test_empty_black_island_painter_does_not_scroll_text(self):
        painter_source = inspect.getsource(IslandStackVisual._paint_black_empty_island)

        self.assertNotIn("scroll_phase", painter_source)
        self.assertNotIn("math.sin", painter_source)
        self.assertNotIn("_scrolling_text_x", painter_source)

    def test_empty_time_mode_uses_dedicated_time_island_with_seconds_setting(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = True
        visual.music_auto_detect = True
        visual.live_audio_available = False
        visual.empty_island_display_mode = "time"
        visual.persistent_plugin = False
        visual.enabled_plugin_ids = set()
        visual.messages = []
        visual.show_seconds = False
        visual.expanded = False
        visual.location_mode = "free"
        visual.font_shrink_scale = 1.0
        visual.high_quality_font_rendering = False
        visual._u = lambda value: float(value)

        self.assertEqual(visual._main_kind().name, "TIME")

        with patch("local.island_stack_visual.datetime") as fake_datetime:
            fake_datetime.now.return_value = datetime(2026, 6, 4, 10, 11, 8).astimezone()
            without_seconds = visual._empty_time_text()
            without_seconds_w, without_seconds_h = visual._main_size_for_kind(visual._main_kind(), 420.0)
            sample = visual._enabled_plugin_samples()
            visual.show_seconds = True
            with_seconds = visual._empty_time_text()
            with_seconds_w, with_seconds_h = visual._main_size_for_kind(visual._main_kind(), 420.0)

        self.assertEqual(visual._main_kind().name, "TIME")
        self.assertEqual(without_seconds, "10:11")
        self.assertEqual(with_seconds, "10:11:08")
        self.assertEqual(sample, [])
        self.assertGreater(with_seconds_w, without_seconds_w)
        self.assertEqual(without_seconds_h, 40.0)
        self.assertEqual(with_seconds_h, without_seconds_h)

        background, text_color = visual._empty_time_island_colors()
        self.assertLess(background.lightness(), 24)
        self.assertGreater(text_color.lightness(), 220)

    def test_tasker_empty_bar_uses_vertical_size(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.empty_island_display_mode = "visible_bar"
        visual.expanded = False
        visual._u = lambda value: float(value)

        width, height = visual._main_size_for_kind(IslandKind.THIN_BAR, 320.0)

        self.assertGreater(height, width)
        self.assertEqual(visual._empty_bar_orientation(), "vertical")

    def test_empty_bar_color_inverts_surroundings(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_material_color = QColor(8, 8, 8)
        visual.taskbar_material_is_light = False

        dark_surrounding = visual._empty_bar_color()

        visual.taskbar_material_color = QColor(244, 244, 244)
        visual.taskbar_material_is_light = True

        light_surrounding = visual._empty_bar_color()

        self.assertGreater(dark_surrounding.lightness(), 180)
        self.assertLess(light_surrounding.lightness(), 90)

    def test_taskbar_time_due_alert_overlays_main_rect(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [
            (0, "TIME", "0:00", "时间到", QColor(255, 184, 0), "", "time:countdown", "done", "倒计时", "时间到", 0)
        ]
        main_rect = QRectF(120.0, 30.0, 280.0, 40.0)

        alert = visual._taskbar_main_overlay_alert()
        rect = visual._taskbar_main_overlay_rect(main_rect, alert)

        self.assertIsNotNone(alert)
        self.assertEqual(rect, main_rect)

    def test_tasker_left_handle_drag_adjusts_width_without_exceeding_track(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        main_rect = QRectF(320.0, 1044.0, 260.0, 34.0)
        track = QRectF(100.0, 1040.0, 820.0, 40.0)

        expanded_width = visual._taskbar_manual_width_for_left_edge(main_rect, track, 240.0)
        clamped_width = visual._taskbar_manual_width_for_left_edge(main_rect, track, -200.0)

        self.assertGreater(expanded_width, main_rect.width())
        self.assertLessEqual(clamped_width, track.width())
        self.assertGreaterEqual(clamped_width, 120.0)

    def test_tasker_drag_uses_full_taskbar_track_so_bottom_corners_are_reachable(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_manual_width = 0.0
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, _taskbar: QRectF(360.0, 1040.0, 1200.0, 40.0)
        visual._taskbar_drag_track_rect_for_screen = lambda _screen, _taskbar: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_taskbar_anchor = lambda _kind, _stack_w, _taskbar: (240.0, 34.0)
        visual._main_top_for_direction = lambda *_args: 10.0
        visual.drag_press_main_offset = QPointF(120.0, 17.0)
        visual.save_last_position = lambda _pos: None
        moves: list[QPoint] = []
        visual._move_taskbar_window_if_needed = lambda pos: moves.append(QPoint(pos)) or True

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            visual._move_taskbar_drag(QPoint(4, 1060))

        self.assertTrue(moves)
        self.assertLessEqual(visual.taskbar_selected_drop_zone.left(), 1.0)

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            visual._move_taskbar_drag(QPoint(1916, 1060))

        self.assertGreaterEqual(visual.taskbar_selected_drop_zone.right(), 1919.0)

    def test_taskbar_drop_overlay_draws_center_prompt_text(self):
        self.assertEqual(TaskbarDropOverlay.LABEL, "拖入此处粘贴到任务栏")
        self.assertIn("drawText", inspect.getsource(TaskbarDropOverlay.paintEvent))

    def test_taskbar_drop_overlay_accepts_multiple_zones(self):
        overlay = TaskbarDropOverlay()
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        zones = [QRectF(190.0, 1044.0, 360.0, 32.0), QRectF(1460.0, 1044.0, 320.0, 32.0)]

        overlay.show_zones(taskbar, zones, 6.0)

        self.assertEqual(len(overlay.drop_rects), 2)
        overlay.hide()

    def test_taskbar_preview_shows_center_drop_overlay_frame(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_preview_visible = True
        visual.taskbar_drop_overlay = None
        visual._taskbar_allowed = lambda: True
        visual.taskbar_attach_hot_zone = QRectF(760.0, 450.0, 400.0, 160.0)
        visual.taskbar_attach_hot_zone_host_rect = QRectF(0.0, 0.0, 1920.0, 1080.0)
        visual._u = lambda value: float(value)

        with patch("local.island_stack_visual.TaskbarDropOverlay") as overlay_class:
            overlay = overlay_class.return_value
            visual._sync_taskbar_drop_overlay()

        overlay_class.assert_called_once()
        overlay.show_zones.assert_called_once()
        host_rect, zones, _radius = overlay.show_zones.call_args.args
        self.assertEqual(host_rect, QRectF(0.0, 0.0, 1920.0, 1080.0))
        self.assertEqual(zones, [visual.taskbar_attach_hot_zone])

    def test_taskbar_dock_uses_current_probe_position_without_slot_magnet(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_selected_drop_zone = QRectF(100.0, 1044.0, 320.0, 32.0)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(1500, 1072)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, _taskbar: QRectF(180.0, 1040.0, 1500.0, 40.0)
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_kind = lambda _kind, _stack_w: (300.0, 34.0)
        visual._main_top_for_direction = lambda *_args: 10.0
        visual.move = lambda _pos: None

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._dock_to_taskbar_task_area())

        self.assertGreater(visual.taskbar_position_ratio, 0.75)

    def test_taskbar_dock_stays_visible_when_autohide_is_currently_hidden(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_selected_drop_zone = QRectF()
        visual.taskbar_dock_animation_requested = False
        visual.taskbar_follow_force_visible_until = 0.0
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(960, 540)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_drag_track_rect_for_screen = lambda _screen, taskbar: QRectF(taskbar)
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: {"auto_hide": True, "rect": QRectF(0.0, 1040.0, 1920.0, 40.0), "edge": "bottom"}
        visual._windows_taskbar_rect_for_screen = lambda _screen: None
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: True
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: True
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_taskbar_anchor = lambda _kind, _stack_w, _taskbar: (300.0, 34.0)
        visual._main_top_for_direction = lambda *_args: 10.0
        visual.save_last_position = lambda _pos: None
        moves: list[QPoint] = []
        visual._move_taskbar_window_if_needed = lambda pos: moves.append(QPoint(pos)) or True

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._dock_to_taskbar_task_area())

        self.assertTrue(moves)
        self.assertLessEqual(moves[-1].y() + 10.0 + 34.0, 1080.0)
        self.assertLessEqual(visual.taskbar_selected_drop_zone.bottom(), 1080.0)
        self.assertGreater(visual.taskbar_follow_force_visible_until, 0.0)

    def test_hidden_autohide_taskbar_follow_preserves_normal_tasker_height(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.expanded = False
        visual.taskbar_selected_drop_zone = QRectF()
        visual.taskbar_position_ratio = 0.5
        visual.taskbar_manual_width = 0.0
        visual.taskbar_follow_force_visible_until = 0.0
        visual.taskbar_follow_last_key = ""
        visual.taskbar_follow_material_last_at = 0.0
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(960, 540)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_drag_track_rect_for_screen = lambda _screen, taskbar: QRectF(taskbar)
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: {"auto_hide": True, "rect": QRectF(0.0, 1040.0, 1920.0, 40.0), "edge": "bottom"}
        visual._windows_taskbar_rect_for_screen = lambda _screen: None
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: True
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: True
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_top_for_direction = lambda *_args: 10.0
        visual._set_taskbar_follow_hidden = lambda _hidden: None
        visual._move_taskbar_window_if_needed = lambda _pos: True
        visual._force_super_topmost = lambda: None
        visual.save_last_position = lambda _pos: None
        visual._update_taskbar_material_for_screen = lambda *_args: None

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertAlmostEqual(visual.taskbar_selected_drop_zone.height(), 40.0)

    def test_taskbar_dock_uses_hidden_fallback_when_taskbar_rect_is_missing(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_selected_drop_zone = QRectF()
        visual.last_taskbar_rect = QRectF()
        visual.taskbar_dock_animation_requested = False
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(960, 540)
        visual._u = lambda value: float(value)
        visual._taskbar_rect_for_screen = lambda _screen: None
        visual._taskbar_hidden_fallback_rect_for_screen = lambda _screen: QRectF(0.0, 1032.0, 1920.0, 48.0)
        visual._taskbar_drag_track_rect_for_screen = lambda _screen, taskbar: QRectF(taskbar)
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_taskbar_anchor = lambda _kind, _stack_w, _taskbar: (300.0, 34.0)
        visual._main_top_for_direction = lambda *_args: 10.0
        visual.save_last_position = lambda _pos: None
        moves: list[QPoint] = []
        visual._move_taskbar_window_if_needed = lambda pos: moves.append(QPoint(pos)) or True

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._dock_to_taskbar_task_area())

        self.assertEqual(visual.last_taskbar_rect, QRectF(0.0, 1032.0, 1920.0, 48.0))
        self.assertTrue(moves)

    def test_autohide_full_shell_rect_stays_visible_when_cursor_leaves_reveal_edge(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.taskbar_autohide_visible_until = 0.0
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._windows_taskbar_rect_for_screen = lambda _screen: QRectF(taskbar)
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: {
            "auto_hide": True,
            "rect": QRectF(taskbar),
            "edge": "bottom",
        }
        visual._taskbar_autohide_cursor_reveals = lambda _screen, _taskbar: False

        self.assertFalse(visual._taskbar_autohide_hidden_for_screen(FakeScreen(), taskbar))

    def test_taskbar_rect_for_screen_reuses_short_lived_windows_snapshot(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

            def availableGeometry(self):
                return QRect(0, 0, 1920, 1040)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        appbar_calls: list[str] = []
        shell_calls: list[str] = []
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: appbar_calls.append("appbar") or {
            "auto_hide": False,
            "rect": QRectF(),
            "edge": "",
        }
        visual._windows_taskbar_rect_for_screen = lambda _screen: shell_calls.append("shell") or QRectF(0.0, 1040.0, 1920.0, 40.0)

        first = visual._taskbar_rect_for_screen(FakeScreen())
        second = visual._taskbar_rect_for_screen(FakeScreen())

        self.assertEqual(first, QRectF(0.0, 1040.0, 1920.0, 40.0))
        self.assertEqual(second, first)
        self.assertEqual(appbar_calls, ["appbar"])
        self.assertEqual(shell_calls, ["shell"])

    def test_taskbar_autohide_hidden_reuses_short_lived_windows_snapshot(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

            def availableGeometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.taskbar_autohide_visible_until = 0.0
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        appbar_calls: list[str] = []
        shell_calls: list[str] = []
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: appbar_calls.append("appbar") or {
            "auto_hide": True,
            "rect": QRectF(taskbar),
            "edge": "bottom",
        }
        visual._windows_taskbar_rect_for_screen = lambda _screen: shell_calls.append("shell") or QRectF(0.0, 1078.0, 1920.0, 2.0)
        visual._taskbar_autohide_cursor_reveals = lambda _screen, _taskbar: False

        self.assertTrue(visual._taskbar_autohide_hidden_for_screen(FakeScreen(), taskbar))
        self.assertTrue(visual._taskbar_autohide_hidden_for_screen(FakeScreen(), taskbar))

        self.assertEqual(appbar_calls, ["appbar"])
        self.assertEqual(shell_calls, ["shell"])

    def test_invalid_taskbar_material_sample_is_throttled(self):
        class FakeScreen:
            pass

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_material_color = QColor()
        visual.taskbar_material_sample_key = ""
        visual.taskbar_material_sampled_at = 0.0
        calls: list[str] = []
        visual._sample_taskbar_material_color = lambda *_args: calls.append("sample") or QColor()

        with patch("local.island_stack_visual.time.monotonic", side_effect=[10.0, 10.05]):
            visual._update_taskbar_material_for_screen(FakeScreen(), QRectF(0.0, 1040.0, 1920.0, 40.0))
            visual._update_taskbar_material_for_screen(FakeScreen(), QRectF(0.0, 1040.0, 1920.0, 40.0))

        self.assertEqual(calls, ["sample"])

    def test_autohide_reveal_edge_keeps_visible_until_cursor_leaves_taskbar_area(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.taskbar_auto_hide_mode = "enabled"
        visual.taskbar_windows_snapshot_cache = {}
        visual.taskbar_autohide_visible_until = 0.0
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._windows_taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1078.0, 1920.0, 2.0)
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: {
            "auto_hide": True,
            "rect": QRectF(taskbar),
            "edge": "bottom",
        }
        cursor = {"pos": QPoint(960, 1079)}
        now = {"value": 100.0}

        with (
            patch("local.island_stack_visual.time.monotonic", side_effect=lambda: now["value"]),
            patch("local.island_stack_visual.QCursor.pos", side_effect=lambda: cursor["pos"]),
            patch("local.island_stack_visual.QGuiApplication.mouseButtons", return_value=Qt.MouseButton.NoButton),
        ):
            self.assertFalse(visual._taskbar_autohide_hidden_for_screen(FakeScreen(), taskbar))
            cursor["pos"] = QPoint(960, 1050)
            now["value"] = 100.2
            self.assertFalse(visual._taskbar_autohide_hidden_for_screen(FakeScreen(), taskbar))
            cursor["pos"] = QPoint(960, 1000)
            now["value"] = 100.3
            self.assertFalse(visual._taskbar_autohide_hidden_for_screen(FakeScreen(), taskbar))
            now["value"] = 100.9
            self.assertTrue(visual._taskbar_autohide_hidden_for_screen(FakeScreen(), taskbar))

    def test_autohide_follow_click_on_taskbar_holds_visible_until_outside_click(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.taskbar_auto_hide_mode = "enabled"
        visual.taskbar_windows_snapshot_cache = {}
        visual.taskbar_follow_base_rect = QRectF()
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._windows_taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1078.0, 1920.0, 2.0)
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: {
            "auto_hide": True,
            "rect": QRectF(taskbar),
            "edge": "bottom",
        }
        cursor = {"pos": QPoint(960, 1079)}
        buttons = {"value": Qt.MouseButton.NoButton}

        with (
            patch("local.island_stack_visual.QCursor.pos", side_effect=lambda: cursor["pos"]),
            patch("local.island_stack_visual.QGuiApplication.mouseButtons", side_effect=lambda: buttons["value"]),
        ):
            follow, hidden = visual._taskbar_follow_rect_for_current_visibility(FakeScreen(), taskbar)
            self.assertFalse(hidden)
            self.assertEqual(follow, taskbar)

            cursor["pos"] = QPoint(960, 1050)
            buttons["value"] = Qt.MouseButton.LeftButton
            follow, hidden = visual._taskbar_follow_rect_for_current_visibility(FakeScreen(), taskbar)
            self.assertFalse(hidden)
            self.assertEqual(follow, taskbar)

            cursor["pos"] = QPoint(960, 980)
            buttons["value"] = Qt.MouseButton.NoButton
            follow, hidden = visual._taskbar_follow_rect_for_current_visibility(FakeScreen(), taskbar)
            self.assertFalse(hidden)
            self.assertEqual(follow, taskbar)

            buttons["value"] = Qt.MouseButton.LeftButton
            follow, hidden = visual._taskbar_follow_rect_for_current_visibility(FakeScreen(), taskbar)
            self.assertTrue(hidden)
            self.assertEqual(follow, QRectF(0.0, 1080.0, 1920.0, 40.0))

    def test_autohide_follow_waits_half_second_after_cursor_leaves_taskbar_area(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.taskbar_auto_hide_mode = "enabled"
        visual.taskbar_windows_snapshot_cache = {}
        visual.taskbar_follow_base_rect = QRectF()
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._windows_taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1078.0, 1920.0, 2.0)
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: {
            "auto_hide": True,
            "rect": QRectF(taskbar),
            "edge": "bottom",
        }
        cursor = {"pos": QPoint(960, 1079)}
        now = {"value": 100.0}

        with (
            patch("local.island_stack_visual.time.monotonic", side_effect=lambda: now["value"]),
            patch("local.island_stack_visual.QCursor.pos", side_effect=lambda: cursor["pos"]),
            patch("local.island_stack_visual.QGuiApplication.mouseButtons", return_value=Qt.MouseButton.NoButton),
        ):
            follow, hidden = visual._taskbar_follow_rect_for_current_visibility(FakeScreen(), taskbar)
            self.assertFalse(hidden)
            self.assertEqual(follow, taskbar)

            cursor["pos"] = QPoint(960, 1000)
            now["value"] = 100.5
            follow, hidden = visual._taskbar_follow_rect_for_current_visibility(FakeScreen(), taskbar)
            self.assertFalse(hidden)
            self.assertEqual(follow, taskbar)

            now["value"] = 100.9
            follow, hidden = visual._taskbar_follow_rect_for_current_visibility(FakeScreen(), taskbar)
            self.assertFalse(hidden)
            self.assertEqual(follow, taskbar)

            now["value"] = 101.1
            follow, hidden = visual._taskbar_follow_rect_for_current_visibility(FakeScreen(), taskbar)
            self.assertTrue(hidden)
            self.assertEqual(follow, QRectF(0.0, 1080.0, 1920.0, 40.0))

    def test_autohide_reveal_edge_ignores_island_dragging(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.taskbar_auto_hide_mode = "enabled"
        visual.taskbar_windows_snapshot_cache = {}
        visual.dragging = True
        visual.drag_candidate = True
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._windows_taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1078.0, 1920.0, 2.0)
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: {
            "auto_hide": True,
            "rect": QRectF(taskbar),
            "edge": "bottom",
        }

        with (
            patch("local.island_stack_visual.QCursor.pos", return_value=QPoint(960, 1079)),
            patch("local.island_stack_visual.QGuiApplication.mouseButtons", return_value=Qt.MouseButton.LeftButton),
        ):
            self.assertTrue(visual._taskbar_autohide_hidden_for_screen(FakeScreen(), taskbar))

    def test_autohide_follow_rect_uses_partial_shell_rect_and_hidden_full_rect(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.taskbar_auto_hide_mode = "enabled"
        visual.taskbar_windows_snapshot_cache = {}
        appbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        shells = [
            QRectF(0.0, 1060.0, 1920.0, 20.0),
            QRectF(0.0, 1078.0, 1920.0, 2.0),
        ]
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: {
            "auto_hide": True,
            "rect": QRectF(appbar),
            "edge": "bottom",
        }
        visual._windows_taskbar_rect_for_screen = lambda _screen: shells.pop(0)
        visual._taskbar_autohide_cursor_reveals = lambda _screen, _taskbar: False

        with patch("local.island_stack_visual.time.monotonic", side_effect=[100.0, 100.03]):
            partial, partial_hidden = visual._taskbar_follow_rect_for_current_visibility(FakeScreen(), appbar)
            hidden, hidden_state = visual._taskbar_follow_rect_for_current_visibility(FakeScreen(), appbar)

        self.assertFalse(partial_hidden)
        self.assertEqual(partial, QRectF(0.0, 1060.0, 1920.0, 20.0))
        self.assertTrue(hidden_state)
        self.assertEqual(hidden, QRectF(0.0, 1080.0, 1920.0, 40.0))

    def test_autohide_stays_visible_when_cursor_is_on_taskbar_reveal_edge(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.taskbar_autohide_visible_until = 0.0
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._windows_taskbar_rect_for_screen = lambda _screen: QRectF(taskbar)
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: {
            "auto_hide": True,
            "rect": QRectF(taskbar),
            "edge": "bottom",
        }
        visual._taskbar_autohide_cursor_reveals = lambda _screen, _taskbar: True

        self.assertFalse(visual._taskbar_autohide_hidden_for_screen(FakeScreen(), taskbar))

    def test_autohide_reveal_uses_screen_edge_not_whole_taskbar_band(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)

        with patch("local.island_stack_visual.QCursor.pos", return_value=QPoint(960, 1046)):
            self.assertFalse(visual._taskbar_autohide_cursor_reveals(FakeScreen(), taskbar))

        with patch("local.island_stack_visual.QCursor.pos", return_value=QPoint(960, 1079)):
            self.assertTrue(visual._taskbar_autohide_cursor_reveals(FakeScreen(), taskbar))

    def test_taskbar_dock_reinforces_topmost_after_moving_into_taskbar(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_selected_drop_zone = QRectF()
        visual.taskbar_dock_animation_requested = False
        visual.taskbar_follow_force_visible_until = 0.0
        visual.taskbar_autohide_visible_until = 0.0
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(960, 540)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_drag_track_rect_for_screen = lambda _screen, taskbar: QRectF(taskbar)
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: False
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: True
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_taskbar_anchor = lambda _kind, _stack_w, _taskbar: (300.0, 34.0)
        visual._main_top_for_direction = lambda *_args: 10.0
        visual.save_last_position = lambda _pos: None
        visual._move_taskbar_window_if_needed = lambda _pos: True
        topmost_calls: list[str] = []
        visual._force_super_topmost = lambda: topmost_calls.append("topmost")

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._dock_to_taskbar_task_area())

        self.assertEqual(topmost_calls, ["topmost"])

    def test_tasker_mouse_press_stops_pending_taskbar_position_animation(self):
        class FakePress:
            def button(self):
                return Qt.MouseButton.LeftButton

            def position(self):
                return QPointF(220.0, 56.0)

            def globalPosition(self):
                return QPointF(260.0, 1056.0)

        class FakeAnimation:
            def __init__(self):
                self.stopped = False

            def stop(self):
                self.stopped = True

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = False
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.stack_center_ratio = 0.5
        visual.hover_armed_key = ""
        visual._u = lambda value: float(value)
        visual.pos = lambda: QPoint(40, 1000)
        visual.mapToGlobal = lambda _point: QPoint(40, 1000)
        visual._hit_test = lambda _point: "music"
        visual._build_visuals = lambda: [
            StackVisual("music", IslandKind.MUSIC, QRectF(100.0, 40.0, 360.0, 34.0), False, None, 10)
        ]
        visual.motions = {}
        visual._drag_main_offset_for_press = lambda _point, _rect, _key: QPointF(120.0, 16.0)
        visual.update = lambda: None
        animation = FakeAnimation()
        visual.taskbar_dock_animation = animation
        visual.taskbar_follow_animation = FakeAnimation()

        visual.mousePressEvent(FakePress())

        self.assertTrue(animation.stopped)
        self.assertIsNone(visual.taskbar_dock_animation)
        self.assertIsNone(visual.taskbar_follow_animation)
        self.assertTrue(visual.drag_candidate)

    def test_taskbar_compact_plugin_style_routes_to_rail_or_block(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_fusion_mode = "compact"
        visual.plugin_samples = [("TIME", "计时", "10:00", QColor(255, 204, 0), "T", "time", "running", "", "", 30, 0.4)]
        calls: list[str] = []
        visual._active_plugin_alerts = lambda *_args, **_kwargs: []
        visual._plugin_index_for_id = lambda _plugin_id: 0
        visual._plugin_tuple = lambda plugin: plugin
        visual._plugin_theme_colors = lambda: {"text": QColor(255, 255, 255), "alert": QColor(0, 0, 0), "stroke": QColor(0, 0, 0)}
        visual._pure_plugin_color = lambda color: color
        visual._plugin_display_color = lambda _label, color: color
        visual._paint_taskbar_plugin_rail = lambda *_args, **_kwargs: calls.append("rail")
        visual._paint_taskbar_plugin_block = lambda *_args, **_kwargs: calls.append("block")

        visual.taskbar_plugin_compact_style = "rail"
        visual._paint_plugin_alert(QPainter(), QRectF(0, 0, 8, 34), "plugin-alert:time")
        visual.taskbar_plugin_compact_style = "block"
        visual._paint_plugin_alert(QPainter(), QRectF(0, 0, 34, 34), "plugin-alert:time")

        self.assertEqual(calls, ["rail", "block"])

    def test_taskbar_plugin_rail_segments_use_transparent_base_and_theme_ratio(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        rect = QRectF(0.0, 0.0, 6.0, 42.0)

        segments = visual._taskbar_plugin_rail_segments(rect, QColor(50, 160, 255), "running", 0.25)

        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["color"].alpha(), 0)
        self.assertEqual(segments[1]["color"], QColor(50, 160, 255, 235))
        self.assertAlmostEqual(segments[1]["rect"].height(), rect.height() * 0.25, delta=0.1)

    def test_taskbar_material_classifies_light_without_forcing_sea_level_background(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.music_ambience_style = "sea_level"
        visual.taskbar_material_is_light = True
        visual.tasker_follow_taskbar_color = False

        self.assertTrue(visual._taskbar_material_is_light(QColor(242, 244, 241)))
        self.assertEqual(visual._effective_music_ambience_style(), "sea_level")

    def test_tasker_follow_taskbar_color_applies_once_and_restores_previous_background(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        saved: dict[str, object] = {}
        visual.location_mode = "tasker"
        visual.music_ambience_style = "sea_level"
        visual.taskbar_material_is_light = True
        visual.tasker_follow_taskbar_color = True
        visual.music_background_follow_previous_style = ""
        visual.settings_getter = lambda key, default=None: saved.get(key, default)
        visual.settings_setter = lambda key, value: saved.__setitem__(key, value)
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        with patch("local.island_stack_visual.system_dark_mode", return_value=False):
            self.assertTrue(visual._sync_music_background_to_taskbar_color(force=True))
        self.assertEqual(visual.music_ambience_style, "system")
        self.assertEqual(saved["v2_music_ambience_style"], "system")
        self.assertEqual(saved["v2_music_background_follow_previous_style"], "sea_level")

        visual.taskbar_material_is_light = False
        self.assertTrue(visual._sync_music_background_to_taskbar_color(force=True))
        self.assertEqual(visual.music_ambience_style, "sea_level")
        self.assertEqual(saved["v2_music_background_follow_previous_style"], "")

    def test_system_light_music_background_uses_dark_lyric_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.music_ambience_style = "system"
        visual._music_system_dark = False

        self.assertEqual(visual._music_lyric_text_color().black(), 255)

    def test_pure_white_music_background_remains_available_with_color_fill_enabled(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "sea_level"
        visual.lyric_flow_style = "wave_stream"
        visual.wave_color_fill_enabled = True
        visual._pulse_stack = lambda: None
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        with patch("local.island_stack_visual.system_dark_mode", return_value=False):
            visual.set_music_ambience_style("pure_white", notify=False)

        self.assertEqual(visual.music_ambience_style, "system")
        self.assertEqual(visual._music_lyric_text_color().black(), 255)

    def test_system_light_expanded_music_title_and_artist_use_dark_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "system"
        visual._music_system_dark = False
        visual.track_title = "Title"
        visual.artist = "Artist"
        visual.media_is_playing = True
        visual.music_button_rects = {}
        visual._u = lambda value: float(value)
        visual._font_size = lambda value: int(value)
        visual._should_paint_album_island = lambda: False
        visual._paint_cover = lambda *_args, **_kwargs: None
        visual._paint_lyric_viewport = lambda *_args, **_kwargs: None
        colors: list[QColor] = []
        visual._paint_scrolling_text = lambda _painter, _rect, _text, _size, color, *_args, **_kwargs: colors.append(QColor(color))
        image = QImage(520, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music_expanded(painter, QRectF(12.0, 12.0, 480.0, 176.0))
        painter.end()

        self.assertEqual([color.black() for color in colors[:2]], [255, 255])

    def test_system_light_album_island_expanded_title_and_artist_use_dark_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "system"
        visual._music_system_dark = False
        visual.track_title = "Title"
        visual.artist = "Artist"
        visual.media_is_playing = True
        visual.music_button_rects = {}
        visual._u = lambda value: float(value)
        visual._font_size = lambda value: int(value)
        visual._album_island_slots = lambda *_args, **_kwargs: []
        visual._paint_album_island_slots = lambda *_args, **_kwargs: None
        visual._paint_album_island_listened_text = lambda *_args, **_kwargs: None
        colors: list[QColor] = []
        visual._paint_scrolling_text = lambda _painter, _rect, _text, _size, color, *_args, **_kwargs: colors.append(QColor(color))
        image = QImage(520, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_album_island_expanded(painter, QRectF(12.0, 12.0, 480.0, 176.0))
        painter.end()

        self.assertEqual([color.black() for color in colors[:2]], [255, 255])

    def test_dual_panel_expanded_music_layout_places_lyrics_above_song_info(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_expanded_layout_style = "dual_panel"
        visual.music_ambience_style = "pure_white"
        visual.track_title = "Title"
        visual.artist = "Artist"
        visual.lyrics = ["歌词歌词歌词"]
        visual.media_is_playing = True
        visual.music_button_rects = {}
        visual._u = lambda value: float(value)
        visual._font_size = lambda value: int(value)
        visual._empty_music_text_active = lambda: False
        visual._should_paint_album_island = lambda: False
        cover_rects: list[QRectF] = []
        lyric_rects: list[QRectF] = []
        text_rects: list[QRectF] = []
        visual._paint_cover = lambda _painter, rect, *_args, **_kwargs: cover_rects.append(QRectF(rect))
        visual._paint_lyric_viewport = lambda _painter, rect: lyric_rects.append(QRectF(rect))
        visual._paint_music_control_button = lambda *_args, **_kwargs: None
        visual._paint_scrolling_text = lambda _painter, rect, *_args, **_kwargs: text_rects.append(QRectF(rect))
        image = QImage(560, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(24.0, 18.0, 500.0, 176.0)

        visual._paint_music_expanded(painter, rect)
        painter.end()

        self.assertTrue(cover_rects)
        self.assertTrue(lyric_rects)
        self.assertLess(lyric_rects[0].top(), cover_rects[0].top())
        self.assertLess(lyric_rects[0].bottom(), cover_rects[0].top())
        self.assertTrue(any(key in visual.music_button_rects for key in ("music:previous", "music:toggle_play_pause", "music:next")))

    def test_dual_panel_expanded_background_uses_neutral_lower_panel(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_expanded_layout_style = "dual_panel"
        visual.expanded = True
        visual.music_ambience_style = "pure_white"
        visual._u = lambda value: float(value)
        visual._edge_notch_active = lambda: False
        visual._music_shell_path = IslandStackVisual._music_shell_path.__get__(visual, IslandStackVisual)
        visual._rounded_path = IslandStackVisual._rounded_path.__get__(visual, IslandStackVisual)
        visual._music_shell_radius = lambda _rect, _expanded: 28.0
        visual._effective_music_ambience_style = lambda: visual.music_ambience_style
        visual._active_lyric_color_fill_paints_background = lambda _style=None: False
        visual._paint_music_expanded = lambda *_args, **_kwargs: None
        visual._paint_music_border = lambda *_args, **_kwargs: None
        image = QImage(520, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 480.0, 176.0)

        visual._paint_music(painter, rect, expanded=True)
        painter.end()

        top = image.pixelColor(260, 62)
        bottom = image.pixelColor(260, 166)
        self.assertLess(top.red(), 245)
        self.assertLess(top.green(), 245)
        self.assertLess(top.blue(), 245)
        self.assertGreaterEqual(bottom.red(), 248)
        self.assertGreaterEqual(bottom.green(), 248)
        self.assertGreaterEqual(bottom.blue(), 248)

    def test_dual_panel_expanded_pure_black_uses_deep_gray_over_black(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_expanded_layout_style = "dual_panel"
        visual.expanded = True
        visual.music_ambience_style = "pure_black"
        visual._u = lambda value: float(value)
        visual._edge_notch_active = lambda: False
        visual._music_shell_path = IslandStackVisual._music_shell_path.__get__(visual, IslandStackVisual)
        visual._rounded_path = IslandStackVisual._rounded_path.__get__(visual, IslandStackVisual)
        visual._music_shell_radius = lambda _rect, _expanded: 28.0
        visual._effective_music_ambience_style = lambda: visual.music_ambience_style
        visual._paint_music_expanded = lambda *_args, **_kwargs: None
        visual._paint_music_border = lambda *_args, **_kwargs: None
        image = QImage(520, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 480.0, 176.0)

        visual._paint_music(painter, rect, expanded=True)
        painter.end()

        top = image.pixelColor(260, 62)
        bottom = image.pixelColor(260, 166)
        self.assertGreater(top.red(), bottom.red())
        self.assertGreater(top.green(), bottom.green())
        self.assertGreater(top.blue(), bottom.blue())
        self.assertLessEqual(bottom.red(), 4)
        self.assertLessEqual(bottom.green(), 4)
        self.assertLessEqual(bottom.blue(), 4)

    def test_dual_panel_dynamic_background_renders_against_upper_panel_dimensions(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_expanded_layout_style = "dual_panel"
        visual.music_ambience_style = "sea_level"
        visual._u = lambda value: float(value)
        visual._edge_notch_active = lambda: False
        visual._music_shell_path = IslandStackVisual._music_shell_path.__get__(visual, IslandStackVisual)
        visual._rounded_path = IslandStackVisual._rounded_path.__get__(visual, IslandStackVisual)
        visual._paint_bottom_ambience = lambda *_args, **_kwargs: None
        visual._paint_music_blur_mask = lambda *_args, **_kwargs: None
        visual._paint_music_light_wave = lambda *_args, **_kwargs: None
        captured: list[QRectF] = []
        visual._paint_music_glass_body = lambda _painter, panel_rect, *_args, **_kwargs: captured.append(QRectF(panel_rect))
        image = QImage(520, 240, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 480.0, 176.0)
        lyric_panel, _info_panel = visual._dual_panel_music_rects(rect)

        visual._paint_dual_panel_dynamic_upper_background(painter, rect, 28.0, "sea_level", lyric_panel)
        painter.end()

        self.assertTrue(captured)
        self.assertAlmostEqual(captured[0].height(), lyric_panel.height(), delta=1.0)
        self.assertLess(captured[0].height(), rect.height() * 0.70)

    def test_dual_panel_dynamic_background_uses_background_layer_plan_only(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_expanded_layout_style = "dual_panel"
        visual.music_ambience_style = "sea_level"
        visual.location_mode = "free"
        visual._u = lambda value: float(value)
        calls = []
        visual._execute_music_surface_paint_step = (
            lambda step, *_args, **_kwargs: calls.append(step)
        )
        image = QImage(520, 240, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 480.0, 176.0)
        lyric_panel, _info_panel = visual._dual_panel_music_rects(rect)

        visual._paint_dual_panel_dynamic_upper_background(painter, rect, 28.0, "sea_level", lyric_panel)
        painter.end()

        self.assertEqual(calls, ["glass_body", "bottom_ambience", "blur_mask", "light_wave"])

    def test_dual_panel_dynamic_upper_panel_has_flat_bottom_edge(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_expanded_layout_style = "dual_panel"
        visual.music_ambience_style = "sea_level"
        visual._u = lambda value: float(value)
        visual._edge_notch_active = lambda: False
        visual._music_shell_path = IslandStackVisual._music_shell_path.__get__(visual, IslandStackVisual)
        visual._rounded_path = IslandStackVisual._rounded_path.__get__(visual, IslandStackVisual)
        visual._paint_bottom_ambience = lambda *_args, **_kwargs: None
        visual._paint_music_blur_mask = lambda *_args, **_kwargs: None
        visual._paint_music_light_wave = lambda *_args, **_kwargs: None

        def paint_body(painter: QPainter, panel_rect: QRectF, radius: float, *_args, **_kwargs):
            painter.fillPath(visual._music_shell_path(panel_rect, radius), QColor(255, 0, 0, 255))

        visual._paint_music_glass_body = paint_body
        image = QImage(520, 240, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 480.0, 176.0)
        lyric_panel, _info_panel = visual._dual_panel_music_rects(rect)

        visual._paint_dual_panel_dynamic_upper_background(painter, rect, 28.0, "sea_level", lyric_panel)
        painter.end()

        y = int(round(lyric_panel.bottom() - 3.0))
        left = image.pixelColor(int(round(lyric_panel.left() + 3.0)), y)
        right = image.pixelColor(int(round(lyric_panel.right() - 3.0)), y)
        self.assertGreater(left.red(), 220)
        self.assertGreater(right.red(), 220)

    def test_integrated_sea_level_reactivity_changes_lower_water_band(self):
        quiet = self._reactive_music_background_visual(
            "sea_level",
            response=self._music_reactive_response(),
            bands=(0.05, 0.04, 0.03),
        )
        reactive = self._reactive_music_background_visual(
            "sea_level",
            response=self._music_reactive_response(
                energy=0.82,
                low=0.90,
                mid=0.58,
                high=0.34,
                bass=0.88,
                lower_alpha=1.34,
                lower_scale=1.04,
                wave_height=1.48,
                wave_speed=1.22,
                color_boost=0.38,
                split_shift=0.012,
                top_guard=0.03,
                phase=1.1,
                pulse=0.42,
            ),
            bands=(0.90, 0.58, 0.34),
        )

        quiet_image = self._paint_music_background_image(quiet)
        reactive_image = self._paint_music_background_image(reactive)
        lower = self._image_region_difference(quiet_image, reactive_image, QRect(38, 56, 244, 26))
        upper = self._image_region_difference(quiet_image, reactive_image, QRect(38, 25, 244, 22))

        self.assertGreater(lower, 2400)
        self.assertGreater(lower, upper)

    def test_integrated_duotone_full_reactivity_changes_side_wave_regions(self):
        quiet = self._reactive_music_background_visual(
            "duotone_full",
            response=self._music_reactive_response(),
            bands=(0.04, 0.04, 0.04),
        )
        reactive = self._reactive_music_background_visual(
            "duotone_full",
            response=self._music_reactive_response(
                energy=0.78,
                low=0.62,
                mid=0.74,
                high=0.48,
                bass=0.58,
                color_boost=0.32,
                transition_shift=0.018,
                transition_softness=0.11,
                interference_flow=0.82,
                interference_alpha=1.48,
                side_wave_alpha=0.54,
                side_wave_height=0.72,
                side_wave_width=1.18,
                phase=1.35,
                pulse=0.34,
            ),
            bands=(0.62, 0.74, 0.48),
        )

        quiet_image = self._paint_music_background_image(quiet)
        reactive_image = self._paint_music_background_image(reactive)
        left_side = self._image_region_difference(quiet_image, reactive_image, QRect(28, 30, 68, 42))
        right_side = self._image_region_difference(quiet_image, reactive_image, QRect(224, 30, 68, 42))

        self.assertGreater(left_side + right_side, 2200)

    def test_integrated_pure_white_reactivity_uses_accent_without_dark_flash(self):
        quiet = self._reactive_music_background_visual(
            "pure_white",
            response=self._music_reactive_response(),
            bands=(0.0, 0.0, 0.0),
        )
        reactive = self._reactive_music_background_visual(
            "pure_white",
            response=self._music_reactive_response(
                energy=0.80,
                low=0.66,
                mid=0.72,
                high=0.48,
                bass=0.60,
                shimmer=0.62,
                side_wave_height=0.54,
                solid_accent_alpha=0.56,
                surface_glow=0.44,
                wave_speed=1.20,
                phase=0.95,
                pulse=0.30,
            ),
            bands=(0.66, 0.72, 0.48),
        )

        quiet_image = self._paint_music_background_image(quiet)
        reactive_image = self._paint_music_background_image(reactive)
        changed = self._image_region_difference(quiet_image, reactive_image, QRect(24, 24, 270, 54))
        center = reactive_image.pixelColor(160, 50)

        self.assertGreater(changed, 900)
        self.assertGreater(center.red() + center.green() + center.blue(), 620)
        self.assertLess(self._count_dark_pixels(reactive_image), 4)

    def test_pure_white_wave_stream_keeps_black_reading_tone_without_old_glow_pass(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "pure_white"
        visual.wave_word_fill_enabled = True
        visual.wave_color_fill_enabled = False
        visual._u = lambda value: float(value)
        visual._lyric_layout = lambda *_args, **_kwargs: LyricLayout("hello", ("hello",), (42.0,), 42.0, ())
        visual._lyric_progress_width = lambda _layout, _progress: 21.0
        visual._scrolling_text_x = lambda *_args, **_kwargs: 0.0
        visual._accent_pair = lambda: (QColor(255, 0, 0), QColor(0, 0, 255))
        visual._wave_fill_color = lambda: QColor(0, 215, 255)
        calls = []
        visual._paint_wave_stream_lyric_chunks = (
            lambda _painter, _rect, _x, _layout, _size, _bold, base, live, _progress, _confidence, glow=False:
            calls.append((QColor(base), QColor(live), bool(glow)))
        )
        image = QImage(220, 80, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_live_lyric_text(
            painter,
            QRectF(10.0, 10.0, 180.0, 36.0),
            "hello",
            16,
            QColor(0, 0, 0, 132),
            QColor(0, 0, 0, 246),
            True,
            {"progress": 0.5, "confidence": 1.0},
        )
        painter.end()

        self.assertEqual(len(calls), 1)
        live_call = calls[0]
        self.assertFalse(live_call[2])
        self.assertEqual(live_call[0].black(), 255)
        self.assertEqual(live_call[1].black(), 255)

    def test_ember_base_font_size_uses_independent_setting(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_font_size = 18
        visual.ember_font_size = 21
        visual._u = lambda value: float(value)
        visual._top_notch_height_mode_active = lambda _mode: False

        self.assertEqual(visual._ember_base_font_size(expanded=False, line_count=1), 21)
        self.assertEqual(visual._ember_base_font_size(expanded=True, line_count=1), 21)

    def test_system_light_legacy_gelv_helper_keeps_black_base_and_allows_word_fill(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "system"
        visual._music_system_dark = False
        visual.gelv_color_fill_enabled = True
        visual.gelv_word_fill_enabled = True
        visual.album_color = QColor("#00D7FF")
        visual._gelv_layout = lambda *_args, **_kwargs: LyricLayout("hello", ("hello",), (42.0,), 42.0, ())
        visual._gelv_line_x = lambda *_args, **_kwargs: 0.0
        visual._gelv_spacing_percent = lambda _progress: 100
        captured: list[tuple[QColor, QColor]] = []
        visual._paint_gelv_songscatter_chunks = (
            lambda _painter, _rect, _x, _layout, _size, base, live, *_args, **_kwargs:
            captured.append((QColor(base), QColor(live)))
        )
        image = QImage(220, 80, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_gelv_line(painter, QRectF(10.0, 10.0, 180.0, 36.0), "hello", 16.0, 0.5, True, color_fill=True)
        painter.end()

        self.assertEqual(captured[0][0].black(), 255)
        self.assertLess(captured[0][1].red(), 20)
        self.assertGreater(captured[0][1].green(), 180)
        self.assertGreater(captured[0][1].blue(), 240)

    def test_gelv_songscatter_theme_matrix_splits_color_and_word_fill(self):
        def capture(color_theme: bool, word_fill: bool) -> dict[str, object]:
            visual = IslandStackVisual.__new__(IslandStackVisual)
            visual.music_ambience_style = "system"
            visual._music_system_dark = False
            visual.gelv_color_fill_enabled = color_theme
            visual.gelv_word_fill_enabled = word_fill
            visual.album_color = QColor("#00D7FF")
            visual._gelv_layout = lambda *_args, **_kwargs: LyricLayout("one two", ("one", "two"), (20.0, 20.0), 40.0, ())
            visual._gelv_line_x = lambda *_args, **_kwargs: 0.0
            visual._gelv_spacing_percent = lambda _progress: 100
            captured: list[dict[str, object]] = []
            visual._paint_gelv_songscatter_chunks = (
                lambda _painter, _rect, _x, _layout, _size, base, live, *_args, **_kwargs:
                captured.append(
                    {
                        "base": QColor(base),
                        "live": QColor(live),
                        "word_fill": bool(getattr(visual, "_current_gelv_word_fill_enabled", True)),
                    }
                )
            )

            visual._paint_gelv_line(None, QRectF(0.0, 0.0, 160.0, 36.0), "one two", 18.0, 0.5, True, color_fill=True)

            return captured[0]

        plain_line = capture(False, False)
        self.assertFalse(plain_line["word_fill"])
        self.assertEqual(plain_line["base"].getRgb()[:3], (0, 0, 0))
        self.assertEqual(plain_line["live"].getRgb()[:3], (0, 0, 0))
        self.assertEqual(plain_line["base"].alpha(), plain_line["live"].alpha())

        colorful_line = capture(True, False)
        self.assertFalse(colorful_line["word_fill"])
        self.assertEqual(colorful_line["base"].getRgb()[:3], colorful_line["live"].getRgb()[:3])
        self.assertLess(colorful_line["live"].red(), 20)
        self.assertGreater(colorful_line["live"].green(), 180)
        self.assertGreater(colorful_line["live"].blue(), 240)

        monochrome_word = capture(False, True)
        self.assertTrue(monochrome_word["word_fill"])
        self.assertEqual(monochrome_word["base"].getRgb()[:3], (0, 0, 0))
        self.assertEqual(monochrome_word["live"].getRgb()[:3], (0, 0, 0))
        self.assertLess(monochrome_word["base"].alpha(), monochrome_word["live"].alpha())

        colorful_word = capture(True, True)
        self.assertTrue(colorful_word["word_fill"])
        self.assertEqual(colorful_word["base"].getRgb()[:3], (0, 0, 0))
        self.assertLess(colorful_word["live"].red(), 20)
        self.assertGreater(colorful_word["live"].green(), 180)
        self.assertGreater(colorful_word["live"].blue(), 240)

    def test_duotone_full_gelv_line_disables_word_color_fill(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.music_ambience_style = "duotone_full"
        visual.gelv_color_fill_enabled = True
        visual.gelv_word_fill_enabled = True
        visual.album_color = QColor(255, 0, 0)
        visual._gelv_layout = lambda *_args, **_kwargs: LyricLayout("hello", ("hello",), (42.0,), 42.0, ())
        visual._gelv_line_x = lambda *_args, **_kwargs: 0.0
        visual._gelv_spacing_percent = lambda _progress: 100
        captured: list[tuple[QColor, QColor]] = []
        visual._paint_gelv_songscatter_chunks = (
            lambda _painter, _rect, _x, _layout, _size, base, live, *_args, **_kwargs:
            captured.append((QColor(base), QColor(live)))
        )

        self.assertFalse(visual._active_lyric_color_fill_enabled())
        visual._paint_gelv_line(None, QRectF(10.0, 10.0, 180.0, 36.0), "hello", 16.0, 0.5, True, color_fill=True)

        self.assertEqual(captured[0][1].getRgb()[:3], (255, 255, 255))
        self.assertNotEqual(captured[0][1].name().upper(), "#00D7FF")

    def test_expanded_sea_level_and_duotone_disable_word_backplates(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.music_ambience_style = "sea_level"
        self.assertFalse(visual._expanded_word_backplate_enabled())
        visual.music_ambience_style = "duotone_full"
        self.assertFalse(visual._expanded_word_backplate_enabled())
        visual.expanded = False
        self.assertFalse(visual._expanded_word_backplate_enabled())
        visual.expanded = True
        for style in ("pure_black", "pure_white", "solid_album"):
            visual.music_ambience_style = style
            self.assertFalse(visual._expanded_word_backplate_enabled())

    def test_gelv_songscatter_chunks_do_not_paint_progress_backlight(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._gelv_font = lambda size, weight: QFont("Arial", int(size), int(weight))
        visual._lyric_progress_width = IslandStackVisual._lyric_progress_width.__get__(visual, IslandStackVisual)
        visual._mix_alpha_color = IslandStackVisual._mix_alpha_color.__get__(visual, IslandStackVisual)
        visual._expanded_word_backplate_enabled = lambda: True
        visual._current_word_backplate_enabled = True
        calls = []
        visual._paint_gelv_line_backlight = lambda *_args: calls.append("line")

        image = QImage(260, 90, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        visual._paint_gelv_songscatter_chunks(
            painter,
            QRectF(0.0, 0.0, 240.0, 40.0),
            0.0,
            LyricLayout("one two three", ("one", "two", "three"), (30.0, 30.0, 30.0), 90.0, ()),
            18.0,
            QColor(255, 255, 255, 120),
            QColor(255, 255, 255, 240),
            0.7,
            True,
        )
        painter.end()

        self.assertEqual(calls, [])

    def test_expanded_wave_stream_lyrics_do_not_paint_progress_pulse(self):
        class FakePainter:
            def __init__(self):
                self.opacity_values = []

            def save(self):
                pass

            def restore(self):
                pass

            def setClipRect(self, *_args):
                pass

            def setOpacity(self, value):
                self.opacity_values.append(value)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyrics = ["previous line", "current line", "next line"]
        visual.lyric_index = 1
        visual.prev_lyric_index = 1
        visual.lyric_transition_kind = ""
        visual._u = lambda value: float(value)
        visual._should_paint_album_island = lambda: False
        visual._gelv_enabled = lambda: False
        visual._is_instrumental_track = lambda: False
        visual._current_lyric_display_lines = lambda: ["current line"]
        visual._lyric_phase = lambda _now: {"progress": 0.5, "confidence": 1.0}
        visual._lyric_transition_progress = lambda _duration: (1.0, 1.0)
        pulses = []
        rows = []
        visual._paint_lyric_phase_pulse = lambda *_args: pulses.append("pulse")
        visual._paint_expanded_lyric_row = lambda _painter, _row, text, *_args: rows.append(text)

        visual._paint_lyric_viewport(FakePainter(), QRectF(0.0, 0.0, 260.0, 100.0))

        self.assertEqual(pulses, [])
        self.assertIn("current line", rows)

    def test_expanded_live_lyric_text_does_not_paint_following_focus_blob(self):
        class FakePainter:
            def __init__(self):
                self.ellipses = []

            def save(self):
                pass

            def restore(self):
                pass

            def setClipRect(self, *_args):
                pass

            def setPen(self, *_args):
                pass

            def setBrush(self, *_args):
                pass

            def drawEllipse(self, *_args):
                self.ellipses.append(_args)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual._u = lambda value: float(value)
        visual._lyric_layout = lambda *_args, **_kwargs: LyricLayout("hello", ("hello",), (42.0,), 42.0, ())
        visual._lyric_progress_width = lambda layout, progress: layout.total_width * progress
        visual._scrolling_text_x = lambda *_args, **_kwargs: 0.0
        visual._accent_pair = lambda: (QColor(0, 215, 255), QColor(82, 154, 255))
        visual._pure_white_music_active = lambda: False
        visual._active_lyric_color_fill_enabled = lambda: False
        visual._paint_wave_stream_lyric_chunks = lambda *_args, **_kwargs: None
        painter = FakePainter()

        visual._paint_live_lyric_text(
            painter,
            QRectF(0.0, 0.0, 200.0, 34.0),
            "hello",
            18.0,
            QColor(255, 255, 255, 120),
            QColor(255, 255, 255, 240),
            True,
            {"progress": 0.55, "confidence": 1.0},
            centered=True,
            centered_scroll=True,
        )

        self.assertEqual(painter.ellipses, [])

    def test_expanded_wave_stream_does_not_use_word_backplates(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._lyric_display_lines = lambda _text: ["upper words", "lower words"]
        visual._music_lyric_base_text_color = lambda alpha=132: QColor(255, 255, 255, alpha)
        visual._music_lyric_text_color = lambda alpha=246: QColor(255, 255, 255, alpha)
        calls = []
        visual._paint_live_lyric_text = (
            lambda _painter, _rect, text, *_args, word_backplate=False, **_kwargs:
            calls.append((text, bool(word_backplate)))
        )

        image = QImage(260, 90, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        visual._paint_expanded_lyric_row(
            painter,
            QRectF(0.0, 0.0, 240.0, 56.0),
            "upper words lower words",
            18.0,
            230,
            True,
            True,
            {"progress": 0.5, "confidence": 1.0},
        )
        painter.end()

        self.assertEqual(calls, [("upper words", False), ("lower words", False)])

    def test_wave_stream_chunks_ignore_word_backplate_state(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._font = lambda size, weight: QFont("Arial", int(size), int(weight))
        visual._lyric_progress_width = lambda layout, progress: layout.total_width * progress
        visual._lyric_elastic_scales = lambda count, *_args: [1.0] * count
        visual._mix_alpha_color = IslandStackVisual._mix_alpha_color.__get__(visual, IslandStackVisual)
        visual._current_word_backplate_enabled = True
        visual.expanded = True
        visual.music_ambience_style = "sea_level"
        calls = []
        visual._paint_gelv_line_backlight = lambda *_args: calls.append(True)

        image = QImage(260, 90, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        visual._paint_wave_stream_lyric_chunks(
            painter,
            QRectF(0.0, 0.0, 240.0, 40.0),
            0.0,
            LyricLayout("hello", ("hello",), (42.0,), 42.0, ()),
            18.0,
            True,
            QColor(255, 255, 255, 120),
            QColor(255, 255, 255, 240),
            0.7,
            1.0,
        )
        painter.end()

        self.assertEqual(calls, [])

    def test_expanded_gelv_lyrics_do_not_use_word_backplates(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._gelv_spacing_progress = lambda _now: 0.5
        visual._lyric_phase = lambda _now: {"progress": 0.5, "confidence": 1.0}
        visual._gelv_line_rects = lambda *_args, **_kwargs: [
            {
                "rect": QRectF(0.0, 0.0, 240.0, 30.0),
                "text": "upper words",
                "size": 18.0,
                "active": True,
                "opacity": 1.0,
                "color_fill": True,
                "progress_fill": False,
                "spacing_percent": None,
            },
            {
                "rect": QRectF(0.0, 36.0, 240.0, 26.0),
                "text": "lower words",
                "size": 14.0,
                "active": True,
                "opacity": 1.0,
                "color_fill": False,
                "progress_fill": True,
                "spacing_percent": None,
            },
        ]
        calls = []
        visual._paint_gelv_line = (
            lambda _painter, _rect, text, *_args, word_backplate=False, **_kwargs:
            calls.append((text, bool(word_backplate)))
        )

        image = QImage(260, 90, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        visual._paint_gelv_expanded_lyrics(painter, QRectF(0.0, 0.0, 240.0, 80.0))
        painter.end()

        self.assertEqual(calls, [("upper words", False), ("lower words", False)])

    def test_enabled_plugin_ids_filter_plugin_samples_without_count_slider(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enabled_plugin_ids = {"time", "agenda"}
        visual.enabled_plugin_count = 3
        visual.plugin_samples = [
            ("TIME", "", "", QColor(), "T", "time", "normal", "", "", 0, 0.0),
            ("WEATHER", "", "", QColor(), "W", "weather", "normal", "", "", 0, 0.0),
            ("AGENDA", "", "", QColor(), "A", "agenda", "normal", "", "", 0, 0.0),
        ]

        samples = visual._enabled_plugin_samples()

        self.assertEqual([sample[5] for sample in samples], ["time", "agenda"])

    def test_congested_taskbar_slot_shrinks_music_to_cover_only_size(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual._u = lambda value: float(value)
        visual.taskbar_selected_drop_zone = QRectF(0.0, 0.0, 58.0, 40.0)

        width, height = visual._taskbar_compact_music_size(420.0, 44.0)

        self.assertLessEqual(width, 58.0)
        self.assertLessEqual(height, 44.0)

    def test_taskbar_message_rects_use_user_style_and_collapse_blocks_when_crowded(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_message_compact_style = "block"
        visual._u = lambda value: float(value)
        main_rect = QRectF(500.0, 1044.0, 280.0, 34.0)

        block = visual._taskbar_message_rect(main_rect, 0, 2, 1)
        rail = visual._taskbar_message_rect(main_rect, 0, 5, 1)

        self.assertAlmostEqual(block.width(), block.height(), delta=0.1)
        self.assertLess(rail.width(), rail.height())

    def test_tasker_music_cover_radius_matches_outer_radius(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_fusion_mode = "compact"
        visual.taskbar_corner_style = "win11"
        visual._u = lambda value: float(value)
        rect = QRectF(0.0, 0.0, 280.0, 40.0)

        self.assertEqual(
            visual._compact_cover_radius(rect, two_line=False),
            visual._music_shell_radius(rect, expanded=False),
        )

    def test_expanded_tasker_music_uses_windows_corner_radius(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_fusion_mode = "compact"
        visual.taskbar_corner_style = "win11"
        visual._u = lambda value: float(value)
        rect = QRectF(0.0, 0.0, 520.0, 176.0)

        self.assertLess(visual._music_shell_radius(rect, expanded=True), 30.0)

    def test_taskbar_compact_layout_uses_tighter_height_and_keeps_all_surfaces_inside_bounds(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_fusion_mode = "compact"
        visual.taskbar_corner_style = "win11"

        width, height = visual._taskbar_compact_music_size(420.0, 44.0)

        self.assertLessEqual(height, 44.0)
        self.assertGreater(width, 0.0)

    def test_taskbar_corner_style_switches_between_win10_and_win11(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertEqual(visual._taskbar_corner_radius(44.0, "win10"), 0.0)
        self.assertGreater(visual._taskbar_corner_radius(44.0, "win11"), 0.0)

    def test_taskbar_expanded_panel_uses_outer_material_frame_without_replacing_the_shell(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.expanded = True
        visual.finder_mode = False
        visual.taskbar_corner_style = "win11"

        self.assertTrue(visual._taskbar_uses_expanded_material_frame())
        self.assertTrue(visual._taskbar_shell_remains_opaque())

    def test_taskbar_expanded_panel_uses_windows_corner_policy(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.taskbar_corner_style = "win10"

        self.assertEqual(visual._taskbar_panel_corner_policy(), "square")

    def test_tasker_plugin_panel_shell_uses_windows_corner_radius(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_corner_style = "win11"
        visual._u = lambda value: float(value)
        visual.motions = {}
        visual._enabled_plugin_samples = lambda: []
        visual._plugin_theme_colors = lambda: {"muted": QColor(160, 160, 160)}
        visual._paint_scrolling_text = lambda *_args, **_kwargs: None
        captured = []
        visual._paint_plugin_solid_shell = lambda _painter, shell, radius, folded=False: captured.append((QRectF(shell), radius, folded))
        rect = QRectF(0.0, 0.0, 420.0, 188.0)

        visual._paint_plugin_panel(None, rect)

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][1], visual._taskbar_corner_radius(rect.height()))
        self.assertFalse(captured[0][2])

    def test_tasker_plugin_panel_shell_respects_win10_square_corner_style(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_corner_style = "win10"
        visual._u = lambda value: float(value)
        visual.motions = {}
        visual._enabled_plugin_samples = lambda: []
        visual._plugin_theme_colors = lambda: {"muted": QColor(160, 160, 160)}
        visual._paint_scrolling_text = lambda *_args, **_kwargs: None
        captured = []
        visual._paint_plugin_solid_shell = lambda _painter, _shell, radius, folded=False: captured.append(radius)

        visual._paint_plugin_panel(None, QRectF(0.0, 0.0, 420.0, 188.0))

        self.assertEqual(captured, [0.0])

    def test_tasker_time_plugin_module_shell_uses_windows_corner_radius(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_corner_style = "win11"
        visual._u = lambda value: float(value)
        visual._system_dark_mode = lambda: False
        visual._time_reference_palette = lambda _theme, _color: {
            "module": QColor(20, 20, 20),
            "module_stroke": QColor(80, 80, 80),
            "accent": QColor(255, 184, 0),
        }
        captured = []

        class FakePainter:
            def setPen(self, *_args):
                pass

            def setBrush(self, *_args):
                pass

            def drawRoundedRect(self, _rect, rx, ry):
                captured.append((rx, ry))

        rect = QRectF(0.0, 0.0, 160.0, 80.0)

        visual._paint_time_module_shell(FakePainter(), rect, {})

        expected = visual._taskbar_corner_radius(rect.height())
        self.assertEqual(captured, [(expected, expected), (expected, expected)])

    def test_gelv_songscatter_font_family_is_noto_serif_sc_extra_bold(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        families = visual._gelv_font_families()

        self.assertEqual(families, ["Noto Serif SC ExtraBold", "Noto Serif SC", "Alibaba PuHuiTi"])

    def test_gelv_font_ignores_legacy_pingfang_selection(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_font_family = "PingFang SC"

        families = visual._gelv_font_families()

        self.assertEqual(families, ["Noto Serif SC ExtraBold", "Noto Serif SC", "Alibaba PuHuiTi"])

    def test_gelv_font_setter_keeps_noto_serif_sc_extra_bold(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._clear_lyric_metrics_cache = lambda: None
        visual._pulse_stack = lambda: None
        visual.update = lambda: None

        visual.set_gelv_font_family("PingFang SC", notify=False)

        self.assertEqual(visual.gelv_font_family, "Noto Serif SC ExtraBold")

    def test_noto_serif_sc_asset_paths_are_embedded_assets_only(self):
        paths = IslandStackVisual._noto_serif_sc_asset_paths()

        self.assertTrue(any(path.name == "NotoSerifSC-ExtraBold.ttf" for path in paths))
        self.assertTrue(all("assets\\fonts\\NotoSerifSC" in str(path) for path in paths))

    def test_legacy_pingfang_and_source_han_asset_paths_are_removed(self):
        self.assertFalse(hasattr(IslandStackVisual, "_source_han_serif_asset_paths"))
        self.assertFalse(hasattr(IslandStackVisual, "_pingfangsc_semibold_asset_paths"))

    def test_alibaba_puhuiti_asset_paths_are_embedded_assets_only(self):
        paths = IslandStackVisual._alibaba_puhuiti_asset_paths()

        self.assertTrue(any(path.name == "AlibabaPuHuiTi-Bold.ttf" for path in paths))
        self.assertTrue(any(path.name == "AlibabaPuHuiTi-Heavy.ttf" for path in paths))
        self.assertTrue(all("assets\\fonts\\AlibabaPuHuiTi" in str(path) for path in paths))

    def test_jiangcheng_xiesong_asset_paths_are_embedded_assets_only(self):
        paths = IslandStackVisual._jiangcheng_xiesong_asset_paths()

        self.assertTrue(any(path.name == "JiangChengXieSong-700W.ttf" for path in paths))
        self.assertTrue(any(path.name == "JiangChengXieSong-900W.ttf" for path in paths))
        self.assertTrue(all("assets\\fonts\\JiangChengXieSong" in str(path) for path in paths))

    def test_wave_stream_font_uses_alibaba_puhuiti_weight_choice(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        font = visual._wave_stream_font(18, QFont.Weight.Normal)

        self.assertEqual(font.families()[0], "Alibaba PuHuiTi")
        self.assertGreaterEqual(font.weight(), QFont.Weight.Bold)

        visual.wave_font_weight = "heavy"
        heavy = visual._wave_stream_font(18, QFont.Weight.Normal)

        self.assertEqual(heavy.families()[0], "Alibaba PuHuiTi Heavy")
        self.assertGreaterEqual(heavy.weight(), QFont.Weight.Black)

    def test_wave_stream_chunks_use_wave_font_channel(self):
        from local.music_lyric_reading import LyricReadingRenderer

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._font = lambda *_args, **_kwargs: self.fail("UI font should not be used")
        visual._wave_stream_font = lambda *_args, **_kwargs: QFont("Georgia", 18)
        layout = LyricLayout("one", ("one",), (24.0,), 24.0, ())
        renderer = LyricReadingRenderer()
        fonts = []
        renderer.paint = lambda _painter, _rect, _x, _text, font, *_args: fonts.append(font.family())
        visual._wave_reading_renderer = renderer

        visual._paint_wave_stream_lyric_chunks(
            object(),
            QRectF(0.0, 0.0, 120.0, 32.0),
            0.0,
            layout,
            18,
            True,
            QColor(255, 255, 255, 120),
            QColor(255, 255, 255, 240),
            0.5,
            1.0,
        )

        self.assertEqual(fonts, ["Georgia"])

    def test_lyric_layout_uses_wave_stream_font_family_not_ui_font(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.ui_font_family = "Arial"

        key = visual._lyric_layout_key("one two", 18, True)

        self.assertEqual(key[1], visual._wave_stream_font(18, QFont.Weight.Bold).toString())
        self.assertNotIn("Arial", key[1])

    def test_gelv_font_defaults_to_bold_weight(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        font = visual._gelv_font(18)

        self.assertGreaterEqual(font.weight(), QFont.Weight.Bold)

    def test_ember_font_uses_selected_jiangcheng_weight_for_both_layers(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        front = visual._ember_font(18, back_layer=False)
        back = visual._ember_font(18, back_layer=True)

        self.assertEqual(front.families()[0], "JiangChengXieSong 700W")
        self.assertEqual(back.families()[0], "JiangChengXieSong 700W")
        self.assertFalse(front.italic())
        self.assertFalse(back.italic())
        self.assertEqual(front.weight(), QFont.Weight.Normal)
        self.assertEqual(back.weight(), QFont.Weight.Normal)
        self.assertEqual(back.pointSize(), front.pointSize() + 2)

        visual.ember_font_weight = "bold"
        heavy_front = visual._ember_font(18, back_layer=False)
        heavy_back = visual._ember_font(18, back_layer=True)

        self.assertEqual(heavy_front.families()[0], "JiangChengXieSong 900W")
        self.assertEqual(heavy_back.families()[0], "JiangChengXieSong 900W")
        self.assertGreaterEqual(heavy_front.weight(), QFont.Weight.Bold)

    def test_ember_font_italic_switch_requests_extra_italic_slant(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.ember_italic_enabled = False
        regular = visual._ember_font(18, back_layer=False)
        self.assertFalse(regular.italic())

        visual.ember_italic_enabled = True
        italic = visual._ember_font(18, back_layer=False)
        self.assertTrue(italic.italic())

    def test_removed_ember_split_settings_do_not_grow_live_island(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "ember_fade"
        visual.ember_line_mode = "split"
        visual.ember_dual_line_enabled = False
        visual.lyrics = ["天地玄黄宇宙洪荒日月盈昃辰宿列张寒来暑往秋收冬藏闰余成岁律吕调阳"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.no_lyrics_text = "暂无歌词"
        visual.track_title = ""
        visual.artist = ""
        visual.location_mode = "free"
        visual.expanded = False
        visual.finder_mode = False
        visual.top_notch_height_mode = "standard"
        visual.lyric_font_size = 18
        visual.font_shrink_scale = 1.0
        visual._lyric_layout_cache = {}

        self.assertEqual(visual._ember_effective_line_mode(expanded=False), "split")
        self.assertEqual(len(visual._ember_current_display_lines(expanded=False)), 3)
        free_height = visual._compact_music_content_height_raw()
        visual.ember_line_mode = "scroll"
        self.assertEqual(visual._compact_music_content_height_raw(), free_height)
        self.assertGreaterEqual(free_height, 46.0)

        visual.location_mode = "stick"
        self.assertEqual(visual._ember_effective_line_mode(expanded=False), "scroll")
        self.assertEqual(len(visual._ember_current_display_lines(expanded=False)), 1)
        stick_width, stick_height = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)
        self.assertEqual(stick_width, 420.0)
        self.assertLess(stick_height, visual._u(70.0))

    def test_ember_scroll_mode_keeps_long_line_single_row(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.ember_line_mode = "scroll"
        visual.ember_dual_line_enabled = False
        text = "天地玄黄宇宙洪荒日月盈昃辰宿列张寒来暑往秋收冬藏"

        lines = visual._ember_display_lines(text, expanded=False)
        scroll_lines = visual._ember_display_lines_for_mode(text, expanded=False)

        self.assertGreater(len(lines), 1)
        self.assertEqual(scroll_lines, [text])

    def test_ember_scroll_mode_hides_translation_when_dual_line_is_off(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "ember_fade"
        visual.music_ambience_style = "sea_level"
        visual.ember_line_mode = "scroll"
        visual.ember_dual_line_enabled = False
        visual.lyrics = ["Tonight will be the night\n今夜会是所有夜晚里独一无二的一夜"]
        visual.lyric_index = 0
        visual.no_lyrics_text = "no lyrics"
        visual.track_title = ""
        visual.artist = ""
        visual._empty_music_text_active = lambda: False
        visual._compact_lyric_lines = lambda: [visual._clean_lyric_for_width(line) or line for line in visual.lyrics]

        self.assertEqual(
            visual._ember_current_display_lines(expanded=False),
            ["Tonight will be the night"],
        )

    def test_ember_dual_line_forces_scroll_mode_and_uses_two_scrolling_rows(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.ember_line_mode = "split"
        visual.ember_dual_line_enabled = True
        visual.lyrics = ["stay with me tonight\n今晚请留在我身边"]
        visual.lyric_index = 0
        visual.no_lyrics_text = "暂无歌词"
        visual.track_title = ""
        visual.artist = ""
        visual.location_mode = "free"

        self.assertEqual(visual._ember_effective_line_mode(expanded=False), "scroll")
        self.assertEqual(
            visual._ember_current_display_lines(expanded=False),
            ["stay with me tonight", "今晚请留在我身边"],
        )

    def test_ember_dual_line_compact_rows_use_configured_size_and_tight_gap(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.lyric_flow_style = "ember_fade"
        visual.ember_line_mode = "split"
        visual.ember_dual_line_enabled = True
        visual.ember_font_size = 18
        visual.ember_font_weight = "normal"
        visual.ember_word_fill_enabled = False
        visual.lyrics = ["stay with me tonight\n今夜请留在我身边"]
        visual.lyric_index = 0
        visual.no_lyrics_text = "no lyrics"
        visual.track_title = ""
        visual.artist = ""
        visual.font_shrink_scale = 1.0
        visual.high_quality_font_rendering = False
        visual._empty_music_text_active = lambda: False
        visual._compact_lyric_lines = lambda: [visual._clean_lyric_for_width(line) or line for line in visual.lyrics]
        visual._ember_lyric_phase = lambda _now: {"progress": 0.5, "confidence": 1.0}
        rows = []
        visual._paint_ember_line = lambda _painter, row, text, size, *_args, **_kwargs: rows.append((QRectF(row), text, size))
        image = QImage(360, 120, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_ember_lyrics(painter, QRectF(0.0, 0.0, 320.0, 76.0), expanded=False)
        painter.end()

        self.assertEqual([row[2] for row in rows], [18, 18])
        self.assertLessEqual(rows[1][0].top() - rows[0][0].bottom(), 2.0)

    def test_ember_tasker_combines_dual_scroll_rows_into_one_fixed_height_line(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.ember_line_mode = "split"
        visual.ember_dual_line_enabled = True
        visual.lyrics = ["stay with me tonight\n今晚请留在我身边"]
        visual.lyric_index = 0
        visual.no_lyrics_text = "暂无歌词"
        visual.track_title = ""
        visual.artist = ""
        visual.location_mode = "tasker"

        self.assertEqual(visual._ember_effective_line_mode(expanded=False), "scroll")
        self.assertEqual(len(visual._ember_current_display_lines(expanded=False)), 1)

    def test_removed_ember_cover_switch_does_not_override_shared_cover_setting(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "ember_fade"
        visual.compact_lyric_show_cover = True
        visual.ember_show_cover = False
        self.assertTrue(visual._compact_lyric_cover_enabled())

        visual.lyric_flow_style = "wave_stream"
        self.assertTrue(visual._compact_lyric_cover_enabled())

    def test_ember_display_lines_split_and_truncate_by_surface(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        compact = visual._ember_display_lines(
            "一二三四五六七八九十甲乙丙丁戊己庚辛壬癸子丑寅卯辰",
            expanded=False,
        )
        expanded = visual._ember_display_lines(
            "一二三四五六七八九十甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥天地玄黄宇宙洪荒日月",
            expanded=True,
        )
        compact_words = visual._ember_display_lines(
            "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen",
            expanded=False,
        )

        self.assertEqual(len(compact), 3)
        self.assertLessEqual(len(compact[0]), 12)
        self.assertLessEqual(len(compact[1]), 12)
        self.assertEqual(compact[2], "...")
        self.assertEqual(len(expanded), 3)
        self.assertLessEqual(len(expanded[0]), 20)
        self.assertLessEqual(len(expanded[1]), 20)
        self.assertEqual(expanded[2], "...")
        self.assertEqual(len(compact_words), 3)
        self.assertLessEqual(len(compact_words[0].split()), 8)
        self.assertLessEqual(len(compact_words[1].split()), 8)
        self.assertEqual(compact_words[2], "...")

    def test_ember_display_lines_balances_rows_when_second_line_triggers(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        words = visual._ember_display_lines("one two three four five six seven eight nine", expanded=False)
        cjk = visual._ember_display_lines("一二三四五六七八九十甲乙丙", expanded=False)

        self.assertEqual([len(line.split()) for line in words], [5, 4])
        self.assertEqual([len(line) for line in cjk], [7, 6])

    def test_legacy_ember_front_color_defaults_to_black_on_system_light_background(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "system"
        visual._music_system_dark = False
        visual.ember_color_fill_enabled = False

        color = visual._ember_front_read_color()

        self.assertEqual((color.red(), color.green(), color.blue()), (0, 0, 0))
        self.assertEqual(color.alpha(), 255)

    def test_ember_back_layer_color_inverts_read_color_and_obeys_gray_mix(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "pure_black"
        visual.ember_back_gray_mix = 0
        visual.ember_color_back_gray_mix = 0

        inverted = visual._ember_back_layer_color(QColor(20, 100, 220), alpha=148)
        visual.ember_back_gray_mix = 100
        softened = visual._ember_back_layer_color(QColor(20, 100, 220), alpha=148)

        self.assertGreater(inverted.red(), inverted.blue())
        self.assertGreater(softened.red(), inverted.red())
        self.assertLess(abs(softened.red() - softened.green()), 8)
        self.assertEqual(softened.alpha(), 148)

    def test_ember_color_back_gray_mix_adds_gray_on_colored_music_backgrounds(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.ember_back_gray_mix = 20

        for style in ("sea_level", "duotone_full", "solid_album"):
            with self.subTest(style=style):
                visual.music_ambience_style = style
                visual.ember_color_fill_enabled = False
                visual.ember_color_back_gray_mix = 0
                base = visual._ember_back_layer_color(QColor(20, 100, 220), alpha=148)
                visual.ember_color_back_gray_mix = 100
                extra_gray = visual._ember_back_layer_color(QColor(20, 100, 220), alpha=148)
                self.assertLess(extra_gray.saturation(), base.saturation())

        visual.music_ambience_style = "pure_black"
        visual.ember_color_back_gray_mix = 0
        neutral = visual._ember_back_layer_color(QColor(20, 100, 220), alpha=148)
        visual.ember_color_back_gray_mix = 100
        unchanged = visual._ember_back_layer_color(QColor(20, 100, 220), alpha=148)
        self.assertEqual(unchanged.getRgb(), neutral.getRgb())

    def test_ember_back_layer_opacity_setting_controls_alpha(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "pure_black"
        visual.ember_back_gray_mix = 62
        visual.ember_color_back_gray_mix = 0
        visual.ember_back_opacity = 40

        color = visual._ember_back_layer_color(QColor(20, 100, 220))

        self.assertEqual(color.alpha(), 102)

    def test_ember_phase_adds_style_offset_to_global_offset(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.timed_lyrics = [(0.0, "a"), (4.0, "b")]
        visual.lyric_index = 0
        visual.lyric_changed_at = 10.0
        visual.media_is_playing = False
        visual.media_app_lyric_offset_seconds = 0.0
        visual.lyric_offset_seconds = 0.25
        visual.ember_lyric_offset_seconds = 0.75
        visual._estimated_media_position = lambda _now: 1.0

        phase = visual._ember_lyric_phase(42.0)

        self.assertAlmostEqual(phase["progress"], 0.5, delta=0.001)

    def test_ember_offset_uses_visual_index_without_mutating_synced_lyric_index(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.timed_lyrics = [(0.0, "old"), (4.0, "next"), (8.0, "third")]
        visual.lyrics = ["old", "next", "third"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.lyric_changed_at = 10.0
        visual.media_is_playing = True
        visual.media_app_lyric_offset_seconds = 0.0
        visual.lyric_offset_seconds = 0.0
        visual.ember_lyric_offset_seconds = 0.75
        visual.ember_last_phase_index = -1
        visual.ember_last_phase_progress = 0.0
        visual.ember_last_phase_at = 0.0
        visual.lyric_transition_kind = ""
        visual._estimated_media_position = lambda _now: 3.5
        visual._empty_music_text_active = lambda: False
        visual._is_instrumental_track = lambda: False

        phase = visual._ember_lyric_phase(20.0)
        text = visual._ember_current_text(visual_index=int(phase["active_index"]))

        self.assertEqual(int(phase["active_index"]), 1)
        self.assertEqual(visual.ember_last_phase_index, 1)
        self.assertLess(phase["progress"], 0.2)
        self.assertEqual(text, "next")
        self.assertEqual(visual.lyric_index, 0)

    def test_ember_offset_clamps_to_positive_four_second_range(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._notify_settings_changed = lambda: None
        visual.update = lambda: None

        visual.set_ember_lyric_offset_seconds(-1.0, notify=False)
        self.assertEqual(visual.ember_lyric_offset_seconds, 0.0)

        visual.set_ember_lyric_offset_seconds(9.0, notify=False)
        self.assertEqual(visual.ember_lyric_offset_seconds, 4.0)

    def test_ember_two_line_progress_tracks_full_lyric_span(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertAlmostEqual(visual._ember_line_progress(0.25, line_count=2), 0.25, delta=0.001)
        self.assertAlmostEqual(visual._ember_line_progress(0.75, line_count=2), 0.75, delta=0.001)
        self.assertAlmostEqual(visual._ember_line_progress(0.25, line_count=1), 0.25, delta=0.001)

    def test_music_reactive_background_switch_controls_live_audio_sampling(self):
        class Provider:
            def get_audio_frame(self):
                return AudioFrame(0.8, 0.6, 0.4)

            def has_live_audio(self):
                return True

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.data_provider = Provider()
        visual.audio_frames = []
        visual.media_is_playing = True
        visual.ambient_phase = 0.0
        visual.music_reactive_background_enabled = False
        visual.music_reactive_background_intensity = 100

        self.assertEqual(visual._sample_audio_bands(10.0), (0.10, 0.08, 0.05))

        visual.music_reactive_background_enabled = True
        visual.music_reactive_background_intensity = 50

        self.assertEqual(visual._sample_audio_bands(10.0), (0.8, 0.6, 0.4))

    def test_music_reactive_background_overlay_is_disabled_for_all_styles(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.music_reactive_background_enabled = True
        visual.current_audio_bands = (0.8, 0.5, 0.3)
        visual.album_color = QColor(40, 180, 255)
        visual.secondary_color = QColor(255, 60, 160)
        visual.media_is_playing = True
        visual.ambient_phase = 0.0
        for style, fill in (
            ("pure_black", QColor(0, 0, 0, 255)),
            ("pure_white", QColor(255, 255, 255, 255)),
            ("solid_album", QColor(18, 24, 30, 255)),
        ):
            image = QImage(220, 60, QImage.Format.Format_ARGB32)
            image.fill(fill)
            painter = QPainter(image)

            visual._paint_music_reactive_background_overlay(painter, QRectF(0.0, 0.0, 220.0, 60.0), 24.0, False, style)
            painter.end()

            self.assertEqual(QColor(image.pixel(18, 30)).getRgb()[:3], fill.getRgb()[:3])
            self.assertEqual(QColor(image.pixel(110, 30)).getRgb()[:3], fill.getRgb()[:3])

    def test_music_reactive_background_does_not_mutate_global_ambience_controls(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.ambient_intensity = 0.28
        visual.music_flow_intensity = 0.52
        visual.current_audio_bands = (0.86, 0.56, 0.32)
        visual.music_reactive_background_intensity = 100

        visual.music_reactive_background_enabled = False
        disabled_boost = visual._ambient_boost()
        disabled_flow = visual._music_flow_amount()

        visual.music_reactive_background_enabled = True
        enabled_boost = visual._ambient_boost()
        enabled_flow = visual._music_flow_amount()

        self.assertEqual(enabled_boost, disabled_boost)
        self.assertEqual(enabled_flow, disabled_flow)

    def test_music_reactive_state_reads_last_advanced_frame(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.current_audio_bands = (0.8, 0.5, 0.2)
        visual.music_reactive_background_intensity = 50

        visual.music_reactive_background_enabled = False
        disabled = visual._music_reactive_state()

        visual.music_reactive_background_enabled = True
        cold = visual._music_reactive_state()
        visual._advance_music_reactive_frame(10.0)
        enabled = visual._music_reactive_state()

        self.assertFalse(disabled["enabled"])
        self.assertEqual(disabled["energy"], 0.0)
        self.assertFalse(cold["enabled"])
        self.assertEqual(cold["energy"], 0.0)
        self.assertTrue(enabled["enabled"])
        self.assertAlmostEqual(enabled["low"], 0.4, delta=0.001)
        self.assertAlmostEqual(enabled["mid"], 0.25, delta=0.001)
        self.assertAlmostEqual(enabled["high"], 0.1, delta=0.001)
        self.assertGreater(enabled["energy"], 0.0)

    def test_music_reactive_frame_smooths_short_band_drops(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.current_audio_bands = (0.95, 0.72, 0.36)
        visual.music_reactive_background_intensity = 100
        visual.music_reactive_background_enabled = True

        first = visual._music_reactive_frame(10.0)
        visual.current_audio_bands = (0.0, 0.0, 0.0)
        second = visual._music_reactive_frame(10.04)
        later = visual._music_reactive_frame(10.60)

        self.assertGreater(first.energy, 0.65)
        self.assertGreater(second.energy, 0.45)
        self.assertLess(later.energy, second.energy)

    def test_music_reactive_frame_is_read_only_during_paint(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.current_audio_bands = (0.95, 0.72, 0.36)
        visual.music_reactive_background_intensity = 100
        visual.music_reactive_background_enabled = True

        visual._advance_music_reactive_frame(10.0)
        first = visual._music_reactive_frame()
        visual.current_audio_bands = (0.0, 0.0, 0.0)
        with patch("local.island_stack_visual.time.monotonic", return_value=10.60):
            second = visual._music_reactive_frame()

        self.assertAlmostEqual(second.energy, first.energy, delta=0.0001)
        visual._advance_music_reactive_frame(10.60)
        decayed = visual._music_reactive_frame()
        self.assertLess(decayed.energy, first.energy)

    def test_music_reactive_frame_cold_read_does_not_advance_runtime(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        class Runtime:
            def read_frame(self):
                return ReactiveFrame()

            def advance_frame(self, _bands, _now=None):
                raise AssertionError("paint/read path must not advance the reactive runtime")

        visual._audio_reactive_runtime_for_current_settings = lambda: Runtime()

        self.assertEqual(IslandStackVisual._music_reactive_frame(visual), ReactiveFrame())

    def test_music_wave_input_snapshot_delegates_provider_reads_to_runtime(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        calls: list[str] = []
        visual.music_reactive_background_enabled = True
        visual.audio_frames = []
        visual.audio_started_at = 8.0
        visual.media_is_playing = False
        visual.ambient_phase = 0.5

        class Provider:
            def get_audio_frame(self):
                calls.append("live-frame")
                return "frame"

            def has_live_audio(self):
                calls.append("probe")
                return True

        class Runtime:
            def input_snapshot(self, **kwargs):
                calls.append(f"snapshot:{kwargs['now']}")
                test.assertTrue(kwargs["reactive_enabled"])
                test.assertEqual(kwargs["audio_frames"], [])
                test.assertEqual(kwargs["audio_started_at"], 8.0)
                test.assertFalse(kwargs["media_is_playing"])
                test.assertEqual(kwargs["ambient_phase"], 0.5)
                test.assertEqual(kwargs["live_frame_reader"](), "frame")
                test.assertTrue(kwargs["live_audio_probe"]())
                return presentation_music_wave_input_snapshot(
                    sampled_bands=(0.8, 0.5, 0.25),
                    live_audio_available=True,
                )

        test = self
        visual.data_provider = Provider()
        visual._audio_reactive_runtime_for_current_settings = lambda: Runtime()
        visual._sample_audio_bands = lambda _now: (_ for _ in ()).throw(
            AssertionError("wave input should be sampled by the audio runtime")
        )
        visual._cached_live_audio_available = lambda _now: (_ for _ in ()).throw(
            AssertionError("live availability should be sampled by the audio runtime")
        )

        snapshot = visual._music_wave_input_snapshot(12.0)

        self.assertEqual(calls, ["snapshot:12.0", "live-frame", "probe"])
        self.assertTrue(snapshot.live_audio_available)
        self.assertEqual(snapshot.sampled_bands, (0.8, 0.5, 0.25))

    def test_music_wave_update_plan_uses_runtime_audio_input_snapshot(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_is_playing = False
        visual.music_reactive_background_enabled = True
        visual.audio_frames = []
        visual.ambient_phase = 0.5

        class Frame:
            low = 0.8
            mid = 0.5
            high = 0.25

        class Provider:
            def get_audio_frame(self):
                return Frame()

            def has_live_audio(self):
                raise AssertionError("live frame sampling should refresh live availability cache")

        visual.data_provider = Provider()

        plan = visual._music_wave_update_plan(12.0)

        self.assertTrue(plan.live_audio_available)
        self.assertEqual(plan.current_audio_bands, (0.8, 0.5, 0.25))

    def test_music_wave_update_plan_consumes_single_audio_input_snapshot(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_is_playing = False
        visual._music_wave_input_snapshot = lambda now: presentation_music_wave_input_snapshot(
            sampled_bands=(0.8, 0.5, 0.25),
            live_audio_available=True,
        )
        visual._sample_audio_bands = lambda _now: (_ for _ in ()).throw(
            AssertionError("wave plan should consume the input snapshot seam")
        )
        visual._cached_live_audio_available = lambda _now: (_ for _ in ()).throw(
            AssertionError("wave plan should consume the input snapshot seam")
        )

        plan = visual._music_wave_update_plan(12.0)

        self.assertTrue(plan.live_audio_available)
        self.assertEqual(plan.current_audio_bands, (0.8, 0.5, 0.25))

    def test_cached_live_audio_available_reuses_recent_cache_without_provider_probe(self):
        class Provider:
            def has_live_audio(self):
                raise AssertionError("recent live audio availability cache should skip provider probing")

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.data_provider = Provider()
        visual._live_audio_available_cached = True
        visual._live_audio_available_checked_at = 10.0

        self.assertTrue(visual._cached_live_audio_available(10.20))

    def test_cached_live_audio_available_probes_when_stale_and_caches_failure(self):
        class Provider:
            def has_live_audio(self):
                raise RuntimeError("capture unavailable")

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.data_provider = Provider()
        visual._live_audio_available_cached = True
        visual._live_audio_available_checked_at = 10.0

        self.assertFalse(visual._cached_live_audio_available(10.60))
        self.assertFalse(visual._live_audio_available_cached)
        self.assertEqual(visual._live_audio_available_checked_at, 10.60)

    def test_update_wave_values_applies_wave_plan_before_engine_advance(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.wave_values = [0.2] * 4
        visual.wave_targets = [0.0] * 4
        visual.ambient_phase = 0.5
        plan_calls: list[float] = []
        runtime_calls: list[tuple[list[float], tuple[float, float, float], float, float | None]] = []

        class Advance:
            values = (0.3, 0.4, 0.5, 0.6)
            targets = (0.7, 0.8, 0.9, 1.0)
            updated_at = 12.0

        class Runtime:
            def advance_wave_values(self, values, bands, phase, *, now, previous_at):
                runtime_calls.append((list(values), bands, phase, previous_at))
                return Advance()

        visual._music_wave_update_plan = lambda now: plan_calls.append(now) or type(
            "Plan",
            (),
            {"live_audio_available": False, "current_audio_bands": (0.192, 0.09, 0.03)},
        )()
        visual._audio_reactive_runtime_for_current_settings = lambda: Runtime()

        with patch("local.island_stack_visual.time.monotonic", return_value=12.0):
            visual._update_wave_values()

        self.assertEqual(plan_calls, [12.0])
        self.assertFalse(visual.live_audio_available)
        self.assertEqual(visual.current_audio_bands, (0.192, 0.09, 0.03))
        self.assertEqual(runtime_calls, [([0.2, 0.2, 0.2, 0.2], (0.192, 0.09, 0.03), 0.5, None)])
        self.assertEqual(visual.wave_values, [0.3, 0.4, 0.5, 0.6])
        self.assertEqual(visual.wave_targets, [0.7, 0.8, 0.9, 1.0])
        self.assertEqual(visual._wave_values_updated_at, 12.0)
        self.assertEqual(visual._music_wave_update_count, 1)
        self.assertEqual(visual._music_wave_first_updated_at, 12.0)

    def test_wave_values_do_not_use_random_spikes(self):
        def make_visual():
            visual = IslandStackVisual.__new__(IslandStackVisual)
            visual.current_audio_bands = (0.9, 0.5, 0.8)
            visual.music_reactive_background_enabled = False
            visual.media_is_playing = True
            visual.live_audio_available = False
            visual.audio_frames = []
            visual.ambient_phase = 1.25
            visual.wave_values = [0.2] * 8
            visual.wave_targets = [0.0] * 8
            visual._music_wave_update_plan = lambda _now: type(
                "Plan",
                (),
                {"live_audio_available": True, "current_audio_bands": (0.9, 0.5, 0.8)},
            )()
            return visual

        low_random = make_visual()
        high_random = make_visual()
        with patch("local.island_stack_visual.time.monotonic", return_value=12.0):
            with patch("local.island_stack_visual.random.random", return_value=0.0):
                low_random._update_wave_values()
            with patch("local.island_stack_visual.random.random", return_value=1.0):
                high_random._update_wave_values()

        self.assertEqual(low_random.wave_values, high_random.wave_values)

    def test_wave_values_follow_elapsed_time_not_tick_count(self):
        def make_visual(now: float):
            visual = IslandStackVisual.__new__(IslandStackVisual)
            visual.current_audio_bands = (0.0, 0.0, 0.0)
            visual.music_reactive_background_enabled = False
            visual.media_is_playing = True
            visual.live_audio_available = False
            visual.audio_frames = []
            visual.ambient_phase = 0.0
            visual.wave_values = [0.2] * 8
            visual.wave_targets = [0.0] * 8
            visual._wave_values_updated_at = now
            visual._music_wave_update_plan = lambda _now: type(
                "Plan",
                (),
                {"live_audio_available": True, "current_audio_bands": (0.9, 0.5, 0.8)},
            )()
            return visual

        short_frame = make_visual(10.0)
        long_frame = make_visual(10.0)
        with patch("local.island_stack_visual.time.monotonic", return_value=10.016):
            short_frame._update_wave_values()
        with patch("local.island_stack_visual.time.monotonic", return_value=10.10):
            long_frame._update_wave_values()

        self.assertLess(short_frame.wave_values[3], long_frame.wave_values[3])
        self.assertGreater(short_frame.wave_values[3], 0.2)

    def test_music_reactive_style_response_delegates_to_engine(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        calls = []

        class Runtime:
            def style_response(self, style: str):
                calls.append(style)
                return {"style": style, "energy": 0.42}

        visual._audio_reactive_runtime_for_current_settings = lambda: Runtime()

        first = IslandStackVisual._music_reactive_style_response(visual, "duotone_full")

        self.assertEqual(calls, ["duotone_full"])
        self.assertEqual(first["energy"], 0.42)

    def test_music_reactive_style_response_uses_cached_snapshot_for_same_frame(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        frame = ReactiveFrame(True, 1.0, 0.8, 0.5, 0.2, 0.6, 0.7, 0.3, 1.0, 0.2, 7)
        snapshot_calls: list[tuple[str, ...]] = []

        class Runtime:
            def read_frame(self):
                return frame

            def snapshot(self, styles: tuple[str, ...]):
                snapshot_calls.append(styles)
                return ReactiveSnapshot(
                    frame=frame,
                    style_responses={style: {"energy": 0.6, "style_count": float(len(styles))} for style in styles},
                )

            def style_response(self, _style: str):
                raise AssertionError("snapshot path should own style responses")

        visual._audio_reactive_runtime_for_current_settings = lambda: Runtime()

        first = IslandStackVisual._music_reactive_style_response(visual, "sea_level")
        second = IslandStackVisual._music_reactive_style_response(visual, "sea_level")

        self.assertEqual(first, second)
        self.assertEqual(snapshot_calls, [("sea_level",)])
        self.assertEqual(first["energy"], 0.6)

    def test_music_reactive_snapshot_expands_styles_without_recomputing_cached_style(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        frame = ReactiveFrame(True, 1.0, 0.8, 0.5, 0.2, 0.6, 0.7, 0.3, 1.0, 0.2, 7)
        snapshot_calls: list[tuple[str, ...]] = []

        class Runtime:
            def read_frame(self):
                return frame

            def snapshot(self, styles: tuple[str, ...]):
                snapshot_calls.append(styles)
                return ReactiveSnapshot(
                    frame=frame,
                    style_responses={style: {"energy": 0.6, "style_count": float(len(styles))} for style in styles},
                )

        visual._audio_reactive_runtime_for_current_settings = lambda: Runtime()

        sea = IslandStackVisual._music_reactive_style_response(visual, "sea_level")
        duo = IslandStackVisual._music_reactive_style_response(visual, "duotone_full")
        sea_again = IslandStackVisual._music_reactive_style_response(visual, "sea_level")

        self.assertEqual(snapshot_calls, [("sea_level",), ("duotone_full", "sea_level")])
        self.assertEqual(sea["style_count"], 1.0)
        self.assertEqual(duo["style_count"], 2.0)
        self.assertEqual(sea_again["style_count"], 2.0)

    def test_music_reactive_snapshot_cache_is_cleared_after_advance(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.current_audio_bands = (0.9, 0.6, 0.3)
        snapshot_calls: list[int] = []

        class Runtime:
            def __init__(self):
                self.frame = ReactiveFrame(True, 1.0, 0.8, 0.5, 0.2, 0.6, 0.7, 0.3, 1.0, 0.2, 7)

            def read_frame(self):
                return self.frame

            def snapshot(self, styles: tuple[str, ...]):
                snapshot_calls.append(self.frame.sequence)
                return ReactiveSnapshot(
                    frame=self.frame,
                    style_responses={style: {"energy": self.frame.energy} for style in styles},
                )

            def advance_frame(self, _bands, _now=None):
                self.frame = ReactiveFrame(True, 1.0, 0.4, 0.3, 0.2, 0.35, 0.4, 0.2, 1.3, 0.1, 8)
                return self.frame

        runtime = Runtime()
        visual._audio_reactive_runtime_for_current_settings = lambda: runtime

        before = IslandStackVisual._music_reactive_style_response(visual, "sea_level")
        IslandStackVisual._advance_music_reactive_frame(visual, 10.0)
        after = IslandStackVisual._music_reactive_style_response(visual, "sea_level")

        self.assertEqual(snapshot_calls, [7, 8])
        self.assertEqual(before["energy"], 0.6)
        self.assertEqual(after["energy"], 0.35)

    def test_music_reactive_style_response_cold_read_does_not_advance_runtime(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        calls = []

        class Runtime:
            def advance_frame(self, _bands, _now=None):
                raise AssertionError("style response read path must not advance the reactive runtime")

            def style_response(self, style: str):
                calls.append(style)
                return {"style": style, "energy": 0.0}

        visual._audio_reactive_runtime_for_current_settings = lambda: Runtime()

        response = IslandStackVisual._music_reactive_style_response(visual, "sea_level")

        self.assertEqual(calls, ["sea_level"])
        self.assertEqual(response["energy"], 0.0)

    def test_music_reactive_style_response_covers_native_styles_without_generic_overlay(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.current_audio_bands = (0.9, 0.65, 0.42)
        visual.music_reactive_background_intensity = 100
        visual.music_reactive_background_enabled = True

        visual._advance_music_reactive_frame(10.0)
        sea_level = visual._music_reactive_style_response("sea_level")
        duotone = visual._music_reactive_style_response("duotone_full")
        solid_album = visual._music_reactive_style_response("solid_album")
        pure_black = visual._music_reactive_style_response("pure_black")
        pure_white = visual._music_reactive_style_response("pure_white")

        self.assertGreater(sea_level["lower_alpha"], 1.0)
        self.assertGreater(sea_level["wave_height"], 1.0)
        self.assertGreater(duotone["transition_softness"], 0.045)
        self.assertGreater(duotone["interference_alpha"], 1.10)
        self.assertGreater(solid_album["solid_accent_alpha"], 0.0)
        self.assertGreater(pure_black["solid_accent_alpha"], 0.0)
        self.assertGreater(pure_white["solid_accent_alpha"], 0.0)
        self.assertGreater(pure_white["solid_accent_lightness"], pure_black["solid_accent_lightness"])

    def test_music_reactive_background_throttles_live_audio_sampling_between_frames(self):
        class Provider:
            def __init__(self):
                self.calls = 0

            def get_audio_frame(self):
                self.calls += 1
                return AudioFrame(0.8, 0.6, 0.4)

            def has_live_audio(self):
                return True

        provider = Provider()
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.data_provider = provider
        visual.audio_frames = []
        visual.media_is_playing = True
        visual.ambient_phase = 0.0
        visual.music_reactive_background_enabled = True
        visual.music_reactive_background_intensity = 100

        first = visual._sample_audio_bands(10.0)
        second = visual._sample_audio_bands(10.03)
        third = visual._sample_audio_bands(10.14)
        self.assertEqual(provider.calls, 1)
        fourth = visual._sample_audio_bands(10.22)

        self.assertEqual(first, (0.8, 0.6, 0.4))
        self.assertEqual(second, first)
        self.assertEqual(third, first)
        self.assertEqual(fourth, first)
        self.assertEqual(provider.calls, 2)

    def test_music_reactive_background_cache_hit_does_not_probe_provider(self):
        class Provider:
            def get_audio_frame(self):
                raise AssertionError("recent reactive audio cache should skip provider sampling")

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.data_provider = Provider()
        visual.audio_frames = []
        visual.media_is_playing = True
        visual.ambient_phase = 0.0
        visual.music_reactive_background_enabled = True
        visual._music_reactive_audio_bands = (0.7, 0.4, 0.2)
        visual._music_reactive_audio_sampled_at = 10.0

        self.assertEqual(visual._sample_audio_bands(10.10), (0.7, 0.4, 0.2))

    def test_music_reactive_background_does_not_double_probe_live_audio_when_frame_missing(self):
        class Provider:
            def __init__(self):
                self.frame_calls = 0
                self.available_calls = 0

            def get_audio_frame(self):
                self.frame_calls += 1
                return None

            def has_live_audio(self):
                self.available_calls += 1
                return False

        provider = Provider()
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.data_provider = provider
        visual.audio_frames = []
        visual.media_is_playing = True
        visual.ambient_phase = 0.0
        visual.music_reactive_background_enabled = True
        visual.music_reactive_background_intensity = 100

        self.assertEqual(visual._sample_audio_bands(10.0), (0.10, 0.08, 0.05))
        self.assertEqual(provider.frame_calls, 1)
        self.assertEqual(provider.available_calls, 0)

    def test_sea_level_reactive_response_keeps_layer_stable_and_drives_wave(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.current_audio_bands = (0.9, 0.65, 0.42)
        visual.music_reactive_background_intensity = 100
        visual.music_reactive_background_enabled = True

        visual._advance_music_reactive_frame(10.0)
        sea_level = visual._music_reactive_style_response("sea_level")

        self.assertGreater(sea_level["wave_height"], 1.0)
        self.assertLessEqual(sea_level["split_shift"], 0.012)
        self.assertLessEqual(sea_level["lower_scale"], 1.08)

    def test_pure_white_reactive_background_uses_album_color_without_dark_flash(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._music_shell_path = IslandStackVisual._music_shell_path.__get__(visual, IslandStackVisual)
        visual._rounded_path = IslandStackVisual._rounded_path.__get__(visual, IslandStackVisual)
        visual.current_audio_bands = (0.88, 0.62, 0.34)
        visual.music_reactive_background_intensity = 100
        visual.music_reactive_background_enabled = True
        visual.album_color = QColor(232, 64, 92)
        visual.secondary_color = QColor(80, 160, 255)
        visual.ambient_phase = 0.0
        visual._advance_music_reactive_frame(10.0)

        image = QImage(240, 80, QImage.Format.Format_ARGB32)
        image.fill(QColor(255, 255, 255, 255))
        painter = QPainter(image)
        visual._paint_pure_white_music_background(painter, QRectF(0.0, 0.0, 240.0, 80.0), 28.0)
        painter.end()

        edge = image.pixelColor(18, 48)
        center = image.pixelColor(120, 40)
        self.assertGreater(edge.red() - edge.blue(), 14)
        self.assertGreater(center.red(), 224)
        self.assertGreater(center.green(), 224)
        self.assertGreater(center.blue(), 224)

    def test_pure_black_reactive_background_uses_album_color_accent(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._music_shell_path = IslandStackVisual._music_shell_path.__get__(visual, IslandStackVisual)
        visual._rounded_path = IslandStackVisual._rounded_path.__get__(visual, IslandStackVisual)
        visual.current_audio_bands = (0.90, 0.58, 0.30)
        visual.music_reactive_background_intensity = 100
        visual.music_reactive_background_enabled = True
        visual.album_color = QColor(245, 72, 96)
        visual.secondary_color = QColor(80, 150, 255)
        visual.ambient_phase = 0.0
        visual._advance_music_reactive_frame(10.0)

        image = QImage(240, 80, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 255))
        painter = QPainter(image)
        visual._paint_pure_black_music_background(painter, QRectF(0.0, 0.0, 240.0, 80.0), 28.0)
        painter.end()

        edge = image.pixelColor(18, 48)
        center = image.pixelColor(120, 40)
        self.assertGreater(edge.red(), edge.blue())
        self.assertLess(center.red(), 36)
        self.assertLess(center.green(), 36)
        self.assertLess(center.blue(), 36)

    def test_solid_music_background_methods_delegate_to_renderer(self):
        source = inspect.getsource(IslandStackVisual._paint_pure_white_music_background)
        self.assertIn("_solid_music_background_renderer", source)
        self.assertLess(len(source.splitlines()), 4)

    def test_native_music_background_methods_delegate_to_renderer(self):
        methods = (
            ("_paint_full_duotone_ambience", "paint_full_duotone_ambience"),
            ("_paint_xiaomi_duotone_ambience", "paint_sea_level_ambience"),
            ("_paint_xiaomi_duotone_wave", "paint_sea_level_wave"),
        )
        for method_name, renderer_method in methods:
            with self.subTest(method_name=method_name):
                source = inspect.getsource(getattr(IslandStackVisual, method_name))
                self.assertIn("_native_music_background_renderer", source)
                self.assertIn(renderer_method, source)
                self.assertLess(len(source.splitlines()), 4)

    def test_island_visual_does_not_call_renderer_private_background_helpers(self):
        source = inspect.getsource(IslandStackVisual)
        forbidden = (
            "._paint_bilateral_reactive_waveforms(",
            "._paint_duotone_reactive_side_waves(",
            "._paint_full_duotone_interference(",
            "._paint_interference_blob(",
            "._paint_full_duotone_orbs(",
            "._paint_background_blob(",
        )
        for text in forbidden:
            with self.subTest(text=text):
                self.assertNotIn(text, source)

    def test_ember_line_paints_blur_panel_between_back_and_front_layers(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.ember_blur_panel_strength = 45
        visual.ember_back_opacity = 58
        visual.ember_back_gray_mix = 62
        visual.ember_word_fill_enabled = True
        visual.music_ambience_style = "pure_black"
        calls = []
        visual._ember_layout = lambda text, size, back_layer=False: LyricLayout(text, ("one",), (24.0,), 24.0, ())
        visual._paint_ember_layout = lambda *_args, **kwargs: calls.append("back" if _args[4] else "front")
        visual._paint_ember_blur_panel = lambda *_args, **_kwargs: calls.append("blur")

        visual._paint_ember_line(None, QRectF(0.0, 0.0, 200.0, 40.0), "one", 18, progress=0.6, confidence=1.0)

        self.assertEqual(calls, ["back", "blur", "front"])

    def test_ember_word_fill_off_paints_complete_line_until_next_lyric(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.ember_blur_panel_strength = 45
        visual.ember_back_opacity = 58
        visual.ember_back_gray_mix = 62
        visual.ember_word_fill_enabled = False
        visual.music_ambience_style = "pure_black"
        layout = LyricLayout("one two", ("one", "two"), (24.0, 24.0), 48.0, ())
        calls = []
        visual._ember_layout = lambda _text, _size, back_layer=False: layout
        visual._paint_ember_layout = (
            lambda *_args, **kwargs: calls.append(
                {
                    "back": bool(_args[4]),
                    "progress": kwargs.get("progress"),
                    "word_fill": bool(kwargs.get("word_fill", False)),
                }
            )
        )
        visual._paint_ember_blur_panel = lambda *_args, **_kwargs: None

        visual._paint_ember_line(None, QRectF(0.0, 0.0, 200.0, 40.0), "one two", 18, progress=0.25, confidence=1.0)

        self.assertEqual([call["progress"] for call in calls], [None, None])
        self.assertFalse(calls[-1]["word_fill"])

    def test_ember_blur_mask_uses_back_text_layer_instead_of_solid_rounded_bar(self):
        class FakePainter:
            def __init__(self):
                self.rounded_rects = 0
                self.images = 0

            def save(self):
                pass

            def restore(self):
                pass

            def setRenderHint(self, *_args):
                pass

            def setOpacity(self, *_args):
                pass

            def setPen(self, *_args):
                pass

            def setBrush(self, *_args):
                pass

            def setFont(self, *_args):
                pass

            def drawText(self, *_args):
                pass

            def drawRoundedRect(self, *_args):
                self.rounded_rects += 1

            def drawImage(self, *_args):
                self.images += 1

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.ember_blur_panel_strength = 45
        visual.ember_back_opacity = 58
        visual.ember_back_gray_mix = 62
        visual.ember_word_fill_enabled = True
        visual.music_ambience_style = "pure_black"
        visual.high_quality_font_rendering = False
        visual.font_shrink_scale = 1.0
        visual._lyric_layout_cache = {}
        visual._pure_white_music_active = lambda: False
        visual._ember_color_theme_enabled = lambda: False
        visual._music_lyric_text_color = lambda alpha=255: QColor(255, 255, 255, alpha)
        visual._ember_layout = lambda text, size, back_layer=False: LyricLayout(text, ("one",), (32.0,), 32.0, ())
        painter = FakePainter()

        visual._paint_ember_line(painter, QRectF(0.0, 0.0, 220.0, 48.0), "one", 18, progress=0.6, confidence=1.0)

        self.assertEqual(painter.rounded_rects, 0)
        self.assertGreater(painter.images, 0)

    def test_default_ui_font_is_embedded_alibaba_puhuiti(self):
        self.assertEqual(IslandStackVisual.DEFAULT_FONT_FAMILY, "Alibaba PuHuiTi")

    def test_ui_font_fallbacks_are_limited_to_embedded_fonts(self):
        self.assertEqual(
            IslandStackVisual.CHINESE_FONT_FALLBACKS,
            ("Alibaba PuHuiTi", "Noto Serif SC ExtraBold", "JiangChengXieSong 700W"),
        )

    def test_gelv_chunks_keep_punctuation_with_previous_unit(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertEqual(visual._gelv_chunks("你好，world again!"), ["你", "好，", "world", "again!"])

    def test_gelv_split_long_cjk_line_gives_upper_extra_unit(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        split = visual._gelv_split_line("一二三四五六七", threshold=4)

        self.assertEqual(split, ("一二三四", "五六七"))

    def test_gelv_split_long_english_line_does_not_split_words(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        split = visual._gelv_split_line("one two three four five", threshold=4)

        self.assertEqual(split, ("one two three", "four five"))

    def test_gelv_short_line_does_not_split(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertIsNone(visual._gelv_split_line("短句", threshold=8))

    def test_gelv_mixed_chinese_english_line_splits_into_two_rows(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True
        visual.lyrics = ["A prayer for you my friend 为你祷告我的朋友", "下一句"]
        visual.lyric_index = 0

        lines = visual._gelv_display_lines()

        self.assertEqual(lines, ["A prayer for you my friend", "为你祷告我的朋友"])

    def test_gelv_line_sizes_keep_ten_px_difference(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_font_size = 22

        active, next_size = visual._gelv_line_sizes(taskbar=False)

        self.assertEqual(active, 22)
        self.assertEqual(next_size, 12)

    def test_gelv_line_sizes_use_independent_primary_and_secondary_settings(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_font_size = 18
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 13

        active, next_size = visual._gelv_line_sizes(taskbar=False)

        self.assertEqual(active, 24)
        self.assertEqual(next_size, 13)

    def test_gelv_secondary_font_size_applies_directly(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 10

        active, next_size = visual._gelv_line_sizes(taskbar=False)

        self.assertEqual(active, 24)
        self.assertEqual(next_size, 10)

    def test_gelv_effect_layout_ignores_wave_stream_effect_settings(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_initial_word_spacing = 100
        visual.gelv_word_spacing = 132
        visual.lyric_elastic_strength = 0
        visual.lyric_word_spacing = 60
        base_key = visual._gelv_layout_key("one two", 18)

        visual.lyric_elastic_strength = 160
        visual.lyric_word_spacing = 150
        wave_changed_key = visual._gelv_layout_key("one two", 18)
        visual.gelv_word_spacing = 108
        gelv_changed_key = visual._gelv_layout_key("one two", 18)

        self.assertEqual(base_key, wave_changed_key)
        self.assertNotEqual(base_key, gelv_changed_key)

    def test_gelv_spacing_progress_starts_from_configured_initial_spacing(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_initial_word_spacing = 180
        visual.gelv_word_spacing = 300

        self.assertEqual(visual._gelv_spacing_percent(0.0), 180)
        self.assertEqual(visual._gelv_spacing_percent(0.5), 240)
        self.assertEqual(visual._gelv_spacing_percent(1.0), 300)

    def test_gelv_spacing_target_is_never_tighter_than_initial_spacing(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_initial_word_spacing = 220
        visual.gelv_word_spacing = 160

        self.assertEqual(visual._gelv_spacing_percent(0.0), 220)
        self.assertEqual(visual._gelv_spacing_percent(1.0), 220)

    def test_gelv_initial_spacing_is_visible_for_english_words(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_font_family = "Source Han Serif SC"
        visual.gelv_initial_word_spacing = 100
        visual.gelv_word_spacing = 100
        tight = visual._gelv_layout_width("she said it still us", 18, spacing_progress=0.0)
        visual.gelv_initial_word_spacing = 180
        visual.gelv_word_spacing = 260
        loose_initial = visual._gelv_layout_width("she said it still us", 18, spacing_progress=0.0)

        self.assertGreaterEqual(loose_initial - tight, 28.0)

    def test_gelv_spacing_below_250_is_already_obvious_for_english_words(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_font_family = "Source Han Serif SC"
        visual.gelv_initial_word_spacing = 100
        visual.gelv_word_spacing = 100

        tight = visual._gelv_layout_width("gone go OD", 18, spacing_progress=0.0)
        visual.gelv_initial_word_spacing = 220
        visual.gelv_word_spacing = 240
        loose = visual._gelv_layout_width("gone go OD", 18, spacing_progress=0.0)

        self.assertGreaterEqual(loose - tight, 22.0)

    def test_gelv_word_spacing_is_visibly_large_for_cjk_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_font_family = "Source Han Serif SC"
        visual.gelv_initial_word_spacing = 100
        visual.gelv_word_spacing = 100
        tight = visual._gelv_layout_width("天地玄黄", 22, spacing_progress=1.0)
        visual.gelv_initial_word_spacing = 180
        visual.gelv_word_spacing = 220
        loose = visual._gelv_layout_width("天地玄黄", 22, spacing_progress=1.0)

        self.assertGreaterEqual(loose - tight, 18.0)

    def test_gelv_compact_music_size_stays_tight_for_two_rows(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = False
        visual.lyric_flow_style = "gelv_songscatter"
        visual.lyric_font_size = 22
        visual.lyrics = ["当前歌词", "下一句"]
        visual.lyric_index = 0

        _width, height = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)

        self.assertLessEqual(height, 68.0 * IslandStackVisual.VISUAL_SCALE)

    def test_gelv_compact_rows_leave_room_for_english_descenders(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_font_family = "Source Han Serif SC"
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 13
        visual.lyrics = ["young glory", "gone by"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 58.0), expanded=False)

        for row in rows:
            metrics = QFontMetrics(visual._gelv_font(float(row["size"])))
            self.assertGreaterEqual(row["rect"].height(), metrics.height() + 1.0)

    def test_gelv_compact_music_size_ignores_legacy_independent_font_settings(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = False
        visual.lyric_flow_style = "gelv_songscatter"
        visual.lyrics = ["current lyric", "next lyric"]
        visual.lyric_index = 0

        visual.gelv_primary_font_size = 18
        visual.gelv_secondary_font_size = 9
        _small_w, small_h = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)
        visual.gelv_primary_font_size = 28
        visual.gelv_secondary_font_size = 18
        _large_w, large_h = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)

        self.assertEqual(large_h, small_h)
        visual.lyric_flow_style = "wave_stream"
        _wave_w, wave_h = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)
        self.assertGreaterEqual(wave_h, 46.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertGreaterEqual(large_h, 46.0 * IslandStackVisual.VISUAL_SCALE)

    def test_gelv_compact_rows_are_tighter_than_expanded_rows(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.lyric_font_size = 22
        visual.lyrics = ["当前歌词", "下一句"]
        visual.lyric_index = 0

        compact = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 44.0), expanded=False)
        expanded = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 132.0), expanded=True)

        compact_gap = compact[1]["rect"].top() - compact[0]["rect"].bottom()
        expanded_gap = expanded[1]["rect"].top() - expanded[0]["rect"].bottom()
        self.assertLessEqual(compact_gap, IslandStackVisual.VISUAL_SCALE * 2.0)
        self.assertGreater(expanded_gap, compact_gap)

    def test_gelv_explicit_dual_line_hides_next_line_and_uses_configured_sizes(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.enable_dual_line_lyrics = True
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 13
        visual.gelv_dual_line_font_size = 18
        visual.lyrics = ["原句歌词\n翻译歌词", "下一句不该出现"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 44.0), expanded=False)

        self.assertEqual([row["text"] for row in rows], ["原句歌词", "翻译歌词"])
        self.assertEqual(rows[0]["size"], 24)
        self.assertEqual(rows[1]["size"], 18)
        self.assertTrue(all(row["active"] for row in rows))

    def test_gelv_single_current_line_keeps_next_line_secondary_size(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.enable_dual_line_lyrics = True
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 11
        visual.gelv_dual_line_font_size = 19
        visual.lyrics = ["单行当前", "下一句"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 44.0), expanded=False)

        self.assertEqual([row["text"] for row in rows], ["单行当前", "下一句"])
        self.assertEqual(rows[0]["size"], 24)
        self.assertEqual(rows[1]["size"], 11)
        self.assertFalse(rows[1]["active"])

    def test_gelv_dual_line_switch_off_uses_single_current_line(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.enable_dual_line_lyrics = False
        visual.lyric_font_size = 22
        visual.lyrics = ["原句歌词\n翻译歌词", "下一句"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 44.0), expanded=False)

        self.assertEqual([row["text"] for row in rows], ["原句歌词", "下一句"])
        self.assertFalse(all(row["active"] for row in rows))

    def test_gelv_mixed_language_rows_are_synced_current_pair(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.enable_dual_line_lyrics = True
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 13
        visual.gelv_dual_line_font_size = 18
        visual.lyrics = ["A prayer for you my friend 为你祷告我的朋友", "下一句"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 44.0), expanded=False)

        self.assertEqual([row["text"] for row in rows], ["A prayer for you my friend", "为你祷告我的朋友"])
        self.assertEqual(rows[0]["size"], 24)
        self.assertEqual(rows[1]["size"], 18)
        self.assertTrue(all(row["active"] for row in rows))

    def test_gelv_mixed_language_switch_off_keeps_single_line(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.enable_dual_line_lyrics = False
        visual.lyric_font_size = 22
        visual.lyrics = ["A prayer for you my friend 为你祷告我的朋友", "下一句"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 44.0), expanded=False)

        self.assertEqual([row["text"] for row in rows], ["A prayer for you my friend 为你祷告我的朋友", "下一句"])
        self.assertFalse(all(row["active"] for row in rows))

    def test_gelv_dual_line_uses_configured_secondary_size_without_size_shift_setting(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.enable_dual_line_lyrics = True
        visual.gelv_primary_font_size = 22
        visual.gelv_secondary_font_size = 11
        visual.gelv_dual_line_font_size = 16
        visual.lyrics = ["原句歌词\n翻译歌词", "下一句"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 44.0), expanded=False)

        self.assertEqual(rows[0]["size"], 22)
        self.assertEqual(rows[1]["size"], 16)

    def test_wave_stream_and_gelv_use_sibling_chunk_painters(self):
        self.assertTrue(hasattr(IslandStackVisual, "_paint_wave_stream_lyric_chunks"))
        self.assertTrue(hasattr(IslandStackVisual, "_paint_gelv_songscatter_chunks"))

    def test_gelv_songscatter_chunks_do_not_scale_focused_words(self):
        class FakePainter:
            def __init__(self):
                self.scale_calls = []

            def setFont(self, *_args):
                pass

            def save(self):
                pass

            def translate(self, *_args):
                pass

            def scale(self, x, y):
                self.scale_calls.append((x, y))

            def setPen(self, *_args):
                pass

            def drawText(self, *_args):
                pass

            def restore(self):
                pass

        visual = IslandStackVisual.__new__(IslandStackVisual)
        layout = LyricLayout(
            text="one two three",
            chunks=("one", "two", "three"),
            widths=(24.0, 24.0, 24.0),
            total_width=72.0,
            font_key=(),
        )
        painter = FakePainter()

        visual._paint_gelv_songscatter_chunks(
            painter,
            QRectF(0.0, 0.0, 120.0, 28.0),
            0.0,
            layout,
            18,
            QColor(255, 255, 255, 120),
            QColor(255, 255, 255, 250),
            0.5,
            True,
        )

        self.assertEqual(painter.scale_calls, [])

    def test_gelv_transition_animates_without_vertical_float_or_size_shift(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_changed_at = 10.0
        visual.prev_lyric_index = 0
        visual.lyric_index = 1

        state = visual._gelv_transition_state(now=10.12)

        self.assertGreater(state["out_alpha"], 0.0)
        self.assertEqual(state["out_y_offset"], 0.0)
        self.assertEqual(state["current_y_offset"], 0.0)
        self.assertEqual(state["current_size_mix"], 1.0)

    def test_gelv_line_switch_keeps_current_line_size_stable(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_changed_at = 10.0
        visual.prev_lyric_index = 0
        visual.lyric_index = 1
        visual.lyrics = ["first", "second", "third"]
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 12

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 58.0), expanded=False, now=10.08)
        incoming = next(row for row in rows if row["text"] == "second" and not row.get("transition_out"))

        self.assertEqual(incoming["size"], 24)

    def test_gelv_line_switch_moves_next_line_up_instead_of_only_fading(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_changed_at = 10.0
        visual.prev_lyric_index = 0
        visual.lyric_index = 1
        visual.lyrics = ["first", "second", "third"]
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 12
        rect = QRectF(0.0, 0.0, 300.0, 58.0)

        final_rows = visual._gelv_line_rects(rect, expanded=False, now=10.7)
        moving_rows = visual._gelv_line_rects(rect, expanded=False, now=10.08)
        final_current = next(row for row in final_rows if row["text"] == "second" and not row.get("transition_out"))
        final_next = next(row for row in final_rows if row["text"] == "third")
        moving_current = next(row for row in moving_rows if row["text"] == "second" and not row.get("transition_out"))

        self.assertGreater(moving_current["rect"].top(), final_current["rect"].top() + 1.0)
        self.assertLess(moving_current["rect"].top(), final_next["rect"].top())

    def test_gelv_transition_out_row_keeps_previous_scroll_progress_without_word_fill(self):
        class FakePainter:
            def save(self):
                pass

            def restore(self):
                pass

            def setOpacity(self, _opacity):
                pass

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "free"
        visual.lyric_changed_at = 10.0
        visual.prev_lyric_index = 0
        visual.lyric_index = 1
        visual.lyrics = [
            "previous long lyric line that had already scrolled",
            "current long lyric line entering now",
            "next lyric line",
        ]
        visual.enable_dual_line_lyrics = False
        visual.gelv_primary_font_size = 24
        visual.gelv_secondary_font_size = 12
        visual.gelv_dual_line_font_size = 17
        visual.gelv_word_fill_enabled = False
        visual._u = lambda value: float(value)
        visual._gelv_spacing_progress = lambda _now=None: 0.0
        calls: list[tuple[str, float]] = []
        visual._paint_gelv_line = lambda _painter, _rect, text, _size, progress, **_kwargs: calls.append((str(text), float(progress)))

        with patch("local.island_stack_visual.time.monotonic", return_value=10.08):
            visual._paint_gelv_compact_lyrics(FakePainter(), QRectF(0.0, 0.0, 320.0, 58.0))

        previous = next(progress for text, progress in calls if text.startswith("previous"))
        current = next(progress for text, progress in calls if text.startswith("current"))
        self.assertAlmostEqual(previous, 1.0)
        self.assertAlmostEqual(current, 0.0)

    def test_gelv_color_fill_marks_only_primary_row(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True
        visual.lyrics = ["Original lyric\nTranslated lyric", "next"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 58.0), expanded=False)

        self.assertEqual([row["color_fill"] for row in rows], [True, False])

    def test_gelv_secondary_row_uses_plain_spacing_not_current_spread(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyrics = ["current line", "next line"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 58.0), expanded=False)

        self.assertIsNone(rows[0]["spacing_percent"])
        self.assertEqual(rows[1]["spacing_percent"], 100)
        self.assertFalse(rows[1].get("progress_fill", False))

    def test_gelv_dual_line_second_row_does_not_use_songscatter_progress(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True
        visual.lyrics = ["Original lyric\nTranslated lyric", "next line"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 58.0), expanded=False)

        self.assertIsNone(rows[0]["spacing_percent"])
        self.assertIsNone(rows[1]["spacing_percent"])
        self.assertTrue(rows[1]["active"])
        self.assertFalse(rows[1]["color_fill"])
        self.assertFalse(rows[1]["progress_fill"])

    def test_gelv_mixed_language_second_row_does_not_use_songscatter_progress(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True
        visual.lyrics = ["A prayer for you my friend 为你祈祷我的朋友", "next line"]
        visual.lyric_index = 0

        rows = visual._gelv_line_rects(QRectF(0.0, 0.0, 300.0, 58.0), expanded=False)

        self.assertEqual([row["text"] for row in rows], ["A prayer for you my friend", "为你祈祷我的朋友"])
        self.assertIsNone(rows[1]["spacing_percent"])
        self.assertFalse(rows[1]["progress_fill"])

    def test_gelv_dual_line_secondary_paint_ignores_progress_fill(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.gelv_color_fill_enabled = True
        visual.gelv_word_fill_enabled = True
        visual.album_color = QColor(255, 0, 0)
        calls = []
        visual._gelv_layout = lambda text, size, spacing_percent: LyricLayout(
            text=text,
            chunks=("one", "two"),
            widths=(20.0, 20.0),
            total_width=40.0,
            font_key=(),
        )
        visual._gelv_line_x = lambda *_args, **_kwargs: 0.0
        visual._paint_gelv_songscatter_chunks = (
            lambda _painter, _rect, _x, _layout, _size, base, live, _progress, _active: calls.append(
                {
                    "base": QColor(base),
                    "live": QColor(live),
                    "word_fill": bool(getattr(visual, "_current_gelv_word_fill_enabled", True)),
                }
            )
        )

        visual._paint_gelv_line(
            None,
            QRectF(0.0, 0.0, 120.0, 28.0),
            "one two",
            18.0,
            0.5,
            active=True,
            color_fill=False,
            progress_fill=True,
            allow_word_fill=False,
        )

        self.assertEqual(calls[0]["base"].getRgb()[:3], (255, 255, 255))
        self.assertEqual(calls[0]["live"].getRgb()[:3], (255, 255, 255))
        self.assertEqual(calls[0]["base"].alpha(), calls[0]["live"].alpha())
        self.assertFalse(calls[0]["word_fill"])

    def test_wave_stream_read_word_keeps_live_highlight_neutral(self):
        class FakePainter:
            def save(self):
                pass

            def restore(self):
                pass

            def setClipRect(self, *_args):
                pass

            def setPen(self, *_args):
                pass

            def setBrush(self, *_args):
                pass

            def drawEllipse(self, *_args):
                pass

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "wave_stream"
        visual.wave_word_fill_enabled = True
        visual.wave_color_fill_enabled = False
        visual.music_ambience_style = "pure_black"
        visual.album_color = QColor(120, 120, 120)
        visual.secondary_color = QColor(100, 100, 100)
        calls = []

        def capture_chunks(_painter, _rect, _x, _layout, _size, _bold, base, live, _progress, _confidence, glow=False):
            calls.append({"base": QColor(base), "live": QColor(live), "glow": glow})

        visual._paint_wave_stream_lyric_chunks = capture_chunks

        visual._paint_live_lyric_text(
            FakePainter(),
            QRectF(0.0, 0.0, 240.0, 32.0),
            "gone go OD",
            18,
            QColor(255, 255, 255, 120),
            QColor(255, 255, 255, 240),
            True,
            {"progress": 0.5, "confidence": 0.8},
            centered=True,
            centered_scroll=True,
        )

        live_call = next(call for call in calls if not call["glow"])
        self.assertEqual(live_call["live"].getRgb()[:3], (255, 255, 255))
        self.assertEqual(live_call["base"].getRgb()[:3], (255, 255, 255))

    def test_wave_stream_read_word_off_paints_whole_line_without_focus_residue(self):
        class FakePainter:
            def save(self):
                pass

            def restore(self):
                pass

            def setClipRect(self, *_args):
                pass

            def setPen(self, *_args):
                pass

            def setBrush(self, *_args):
                pass

            def drawEllipse(self, *_args):
                pass

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "wave_stream"
        visual.wave_word_fill_enabled = False
        visual.wave_color_fill_enabled = False
        visual.music_ambience_style = "pure_black"
        visual.album_color = QColor(42, 126, 210)
        visual.secondary_color = QColor(200, 80, 160)
        calls = []

        def capture_chunks(_painter, _rect, _x, _layout, _size, _bold, base, live, progress, confidence, glow=False):
            calls.append({"base": QColor(base), "live": QColor(live), "progress": progress, "confidence": confidence, "glow": glow})

        visual._paint_wave_stream_lyric_chunks = capture_chunks

        visual._paint_live_lyric_text(
            FakePainter(),
            QRectF(0.0, 0.0, 240.0, 32.0),
            "gone go OD",
            18,
            QColor(255, 255, 255, 120),
            QColor(255, 255, 255, 240),
            True,
            {"progress": 0.5, "confidence": 0.8},
            centered=True,
            centered_scroll=True,
        )

        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]["glow"])
        self.assertEqual(calls[0]["live"].getRgb()[:3], (255, 255, 255))
        self.assertEqual(calls[0]["base"].getRgb()[:3], (255, 255, 255))
        self.assertAlmostEqual(calls[0]["progress"], 0.5, delta=0.001)
        self.assertAlmostEqual(calls[0]["confidence"], 1.0, delta=0.001)

    def test_wave_stream_reading_ignores_removed_fill_switches(self):
        class FakePainter:
            def save(self):
                pass

            def restore(self):
                pass

            def setClipRect(self, *_args):
                pass

            def setPen(self, *_args):
                pass

            def setBrush(self, *_args):
                pass

            def drawEllipse(self, *_args):
                pass

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "wave_stream"
        visual.wave_word_fill_enabled = True
        visual.wave_color_fill_enabled = True
        visual.music_ambience_style = "pure_black"
        visual.album_color = QColor(42, 126, 210)
        visual.secondary_color = QColor(200, 80, 160)
        calls = []

        def capture_chunks(_painter, _rect, _x, _layout, _size, _bold, base, live, progress, confidence, glow=False):
            if not glow:
                calls.append({"base": QColor(base), "live": QColor(live), "progress": progress, "confidence": confidence})

        visual._paint_wave_stream_lyric_chunks = capture_chunks

        visual._paint_live_lyric_text(
            FakePainter(),
            QRectF(0.0, 0.0, 240.0, 32.0),
            "gone go OD",
            18,
            QColor(255, 255, 255, 120),
            QColor(255, 255, 255, 240),
            True,
            {"progress": 0.5, "confidence": 0.8},
            centered=True,
            centered_scroll=True,
        )

        self.assertFalse(visual._active_lyric_color_fill_enabled())
        self.assertEqual(calls[-1]["base"].getRgb()[:3], (255, 255, 255))
        self.assertEqual(calls[-1]["live"].getRgb()[:3], (255, 255, 255))
        self.assertAlmostEqual(calls[-1]["progress"], 0.5, delta=0.001)

        visual.wave_word_fill_enabled = False
        visual._paint_live_lyric_text(
            FakePainter(),
            QRectF(0.0, 0.0, 240.0, 32.0),
            "gone go OD",
            18,
            QColor(255, 255, 255, 120),
            QColor(255, 255, 255, 240),
            True,
            {"progress": 0.5, "confidence": 0.8},
            centered=True,
            centered_scroll=True,
        )

        self.assertEqual(calls[-1]["base"].getRgb()[:3], calls[-1]["live"].getRgb()[:3])
        self.assertEqual(calls[-1]["live"].getRgb()[:3], (255, 255, 255))
        self.assertAlmostEqual(calls[-1]["progress"], 0.5, delta=0.001)

        visual.album_color = QColor(120, 120, 120)
        self.assertTrue(visual.wave_color_fill_enabled)
        self.assertFalse(visual._active_lyric_color_fill_enabled())

    def test_gelv_long_line_uses_synced_scroll_position(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        x_start = visual._gelv_line_x(QRectF(0.0, 0.0, 90.0, 24.0), "one two three four five six seven", 18, progress=0.0)
        x_late = visual._gelv_line_x(QRectF(0.0, 0.0, 90.0, 24.0), "one two three four five six seven", 18, progress=0.85)

        self.assertLess(x_late, x_start)

    def test_compact_gelv_viewport_routes_to_shared_continuous_reader(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "gelv_songscatter"
        visual.lyrics = ["当前歌词", "下一句"]
        visual.lyric_index = 0
        visual._should_paint_album_island = lambda: False
        visual._compact_lyric_lines = lambda: visual.lyrics
        visual._lyric_phase = lambda _now: {"progress": 0.4}
        visual._paint_live_lyric_text = lambda *_args, **_kwargs: calls.append("shared")
        calls = []
        visual._paint_gelv_compact_lyrics = lambda *_args: self.fail("Legacy scatter renderer must not run")
        image = QImage(300, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_compact_lyric_viewport(painter, QRectF(0.0, 0.0, 260.0, 70.0))
        painter.end()

        self.assertEqual(calls, ["shared"])

    def test_removed_ember_selection_uses_shared_lyric_viewports(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "ember_fade"
        visual.lyrics = ["current lyric", "next lyric"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.lyric_changed_at = time.monotonic()
        visual.lyric_transition_kind = ""
        visual._u = lambda value: float(value)
        visual._should_paint_album_island = lambda: False
        visual._compact_lyric_lines = lambda: ["current lyric"]
        visual._compact_primary_lyric_font_size = lambda: 18
        visual._lyric_phase = lambda _now: {"progress": 0.5, "confidence": 1.0}
        visual._lyric_transition_progress = lambda _duration: (1.0, 1.0)
        visual._current_lyric_display_lines = lambda: ["current lyric"]
        calls = []
        visual._paint_live_lyric_text = lambda *_args, **_kwargs: calls.append("compact")
        visual._paint_expanded_lyric_row = lambda *_args, **_kwargs: calls.append("expanded")
        visual._paint_ember_lyrics = lambda *_args, **_kwargs: self.fail("Removed Ember renderer must not run")
        image = QImage(320, 140, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_compact_lyric_viewport(painter, QRectF(0.0, 0.0, 260.0, 70.0))
        visual._paint_lyric_viewport(painter, QRectF(0.0, 0.0, 300.0, 110.0))
        painter.end()

        self.assertEqual(calls[0], "compact")
        self.assertIn("expanded", calls)

    def test_ember_renderer_paints_visible_text_pixels(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "ember_fade"
        visual.lyrics = ["Sahiuishauhdasda 你就从地上超声绽炮"]
        visual.lyric_index = 0
        visual.lyric_font_size = 18
        visual.location_mode = "free"
        visual.top_notch_height_mode = "standard"
        visual.font_shrink_scale = 1.0
        visual.high_quality_font_rendering = False
        visual._empty_music_text_active = lambda: False
        visual._compact_lyric_lines = lambda: visual.lyrics
        visual._lyric_phase = lambda _now: {"progress": 0.58, "confidence": 1.0}
        image = QImage(360, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_ember_lyrics(painter, QRectF(0.0, 0.0, 360.0, 90.0), expanded=False)
        painter.end()

        has_visible_pixel = any(
            QColor.fromRgba(image.pixel(x, y)).alpha() > 0
            for y in range(0, image.height(), 3)
            for x in range(0, image.width(), 3)
        )
        self.assertTrue(has_visible_pixel)

    def test_ember_word_fill_keeps_unread_words_visible_with_base_style(self):
        class FakePainter:
            def __init__(self):
                self.drawn = []
                self.current_pen = QColor()

            def save(self):
                pass

            def restore(self):
                pass

            def setFont(self, *_args):
                pass

            def setPen(self, pen):
                self.current_pen = QColor(pen)

            def drawText(self, *_args):
                self.drawn.append((str(_args[-1]), QColor(self.current_pen)))

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.font_shrink_scale = 1.0
        visual.high_quality_font_rendering = False
        visual._u = lambda value: float(value)
        layout = LyricLayout("one two", ("one", "two"), (28.0, 28.0), 56.0, ())
        painter = FakePainter()

        visual._paint_ember_layout(
            painter,
            QRectF(0.0, 0.0, 240.0, 48.0),
            layout,
            18,
            False,
            QColor(20, 20, 20, 220),
            progress=0.5,
            confidence=1.0,
            live_color=QColor(220, 20, 20, 240),
            word_fill=True,
        )

        drawn_texts = [text for text, _color in painter.drawn]
        self.assertIn("one", drawn_texts)
        self.assertIn("two", drawn_texts)
        one_color = next(color for text, color in painter.drawn if text == "one")
        two_color = next(color for text, color in painter.drawn if text == "two")
        self.assertGreater(one_color.red(), two_color.red())
        self.assertGreater(one_color.alpha(), two_color.alpha())
        self.assertGreater(two_color.alpha(), 0)

    def test_gelv_spacing_progress_uses_actual_line_duration(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.timed_lyrics = [(0.0, "a"), (4.0, "b")]
        visual.lyric_index = 0
        visual.lyric_offset_seconds = 0.0
        visual._estimated_media_position = lambda _now: 2.0

        progress = visual._gelv_spacing_progress(99.0)

        self.assertAlmostEqual(progress, 0.5, delta=0.02)

    def test_gelv_spacing_progress_matches_wave_stream_phase(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._lyric_phase = lambda _now: {"progress": 0.73, "confidence": 0.78}

        self.assertAlmostEqual(visual._gelv_spacing_progress(12.0), 0.73, delta=0.001)

    def test_lyric_colorful_temporarily_disables_for_unusable_album_color_without_flipping_setting(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "pure_black"
        visual.lyric_flow_style = "gelv_songscatter"
        visual.album_color = QColor(120, 120, 120)
        visual.gelv_color_fill_enabled = True

        self.assertFalse(visual._gelv_color_theme_enabled())
        self.assertTrue(visual.gelv_color_fill_enabled)
        self.assertFalse(visual._gelv_fill_color().isValid())

        visual.lyric_flow_style = "ember_fade"
        visual.ember_color_fill_enabled = True

        self.assertFalse(visual._ember_color_theme_enabled())
        self.assertTrue(visual.ember_color_fill_enabled)
        self.assertFalse(visual._ember_fill_color().isValid())

        visual.lyric_flow_style = "wave_stream"
        visual.wave_color_fill_enabled = True

        self.assertFalse(visual._wave_color_theme_enabled())
        self.assertTrue(visual.wave_color_fill_enabled)
        self.assertFalse(visual._wave_fill_color().isValid())

    def test_gelv_fill_color_saturates_album_color(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_color = QColor(80, 120, 170)

        color = visual._gelv_fill_color()

        self.assertGreater(color.saturation(), visual.album_color.saturation())

    def test_album_island_cover_count_allows_only_odd_choices(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.update = lambda: None
        visual._emit_settings_changed = lambda *_args: None

        visual.set_album_island_cover_count(4, notify=False)
        self.assertEqual(visual.album_island_cover_count, 5)
        visual.set_album_island_cover_count(7, notify=False)
        self.assertEqual(visual.album_island_cover_count, 7)

    def test_should_use_album_island_when_no_lyrics_mode_selected(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.no_lyrics_display_mode = "album_island"
        visual.lyrics = ["暂无歌词"]
        visual.no_lyrics_text = "暂无歌词"

        self.assertTrue(visual._should_paint_album_island())

    def test_should_use_album_island_when_selected_as_lyric_flow_style(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "album_island"
        visual.no_lyrics_display_mode = "text"
        visual.lyrics = ["这是一句真实歌词", "下一句歌词"]
        visual.no_lyrics_text = "暂无歌词"

        self.assertTrue(visual._should_paint_album_island())

    def test_listened_hours_text_has_no_decimals(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.listened_seconds_total = 3660 * 12.4

        self.assertEqual(visual._album_island_listened_text(), "您已听歌 12 小时")

    def test_album_history_moves_previous_track_left_on_change(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_history = []
        visual.media_track_id = "old"
        visual.track_title = "Old"
        visual.album_art = QImage()

        visual._remember_album_history("new")

        self.assertEqual(visual.album_history[0]["track_id"], "old")

    def test_album_history_previous_track_change_moves_old_current_to_right(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        previous_image = QImage(8, 8, QImage.Format.Format_ARGB32)
        previous_image.fill(QColor(210, 80, 45))
        current_image = QImage(8, 8, QImage.Format.Format_ARGB32)
        current_image.fill(QColor(10, 120, 210))
        visual.album_history = [{"track_id": "previous", "title": "Previous", "album_art": previous_image}]
        visual.album_queue = []
        visual.media_track_id = "current"
        visual.track_title = "Current"
        visual.album_art = current_image

        direction = visual._prepare_album_flow_for_track_change("previous")

        self.assertEqual(direction, -1)
        self.assertEqual(visual.album_queue[0]["track_id"], "current")
        self.assertEqual(visual.album_history, [])

    def test_album_flow_from_known_queue_primes_direct_color_transition(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_history = []
        visual.media_track_id = "current"
        visual.track_title = "Current"
        visual.artist = "Artist"
        visual.album_color = QColor(10, 80, 160)
        visual.secondary_color = QColor(20, 120, 170)
        visual.album_art = QImage()
        visual.album_queue = [
            {
                "track_id": "next",
                "title": "Next",
                "artist": "Artist",
                "album_color": QColor(220, 120, 40),
                "secondary_color": QColor(120, 40, 180),
            }
        ]

        direction = visual._prepare_album_flow_for_track_change("next")

        self.assertEqual(direction, 1)
        self.assertEqual(visual.music_surface_transition_mode, "queued")
        self.assertEqual(visual.music_surface_transition_from[0], QColor(10, 80, 160))
        self.assertEqual(visual.music_surface_transition_to[0], QColor(220, 120, 40))
        self.assertEqual(visual.music_surface_transition_direction, 1)

    def test_music_surface_transition_colors_blend_album_pair(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_color = QColor(220, 120, 40)
        visual.secondary_color = QColor(120, 40, 180)
        visual.music_surface_transition_started_at = 10.0
        visual.music_surface_transition_duration = 1.0
        visual.music_surface_transition_from = (QColor(10, 80, 160), QColor(20, 120, 170))
        visual.music_surface_transition_to = (QColor(220, 120, 40), QColor(120, 40, 180))
        visual.music_surface_transition_mode = "queued"

        primary, secondary = visual._current_music_surface_colors(now=10.35)

        self.assertGreater(primary.red(), 10)
        self.assertLess(primary.red(), 220)
        self.assertLess(primary.blue(), 160)
        self.assertGreater(secondary.red(), 20)
        self.assertEqual(visual.music_surface_transition_mode, "queued")

    def test_music_surface_transition_does_not_wait_for_lyrics_loading(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_color = QColor(220, 120, 40)
        visual.secondary_color = QColor(120, 40, 180)
        visual.lyrics = ["查找歌词中"]
        visual.timed_lyrics = []
        visual.music_surface_transition_started_at = 10.0
        visual.music_surface_transition_duration = 1.0
        visual.music_surface_transition_mode = "queued"
        visual.music_surface_transition_from = (QColor(10, 80, 160), QColor(20, 120, 170))
        visual.music_surface_transition_to = (QColor(220, 120, 40), QColor(120, 40, 180))

        progress = visual._transition_progress_for_surface(now=11.6)

        self.assertEqual(progress, 1.0)
        self.assertEqual(visual.music_surface_transition_mode, "")

    def test_album_flow_without_preloaded_queue_uses_white_bridge(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_history = []
        visual.album_queue = []
        visual.media_track_id = "current"
        visual.track_title = "Current"
        visual.artist = "Artist"
        visual.album_color = QColor(10, 80, 160)
        visual.secondary_color = QColor(20, 120, 170)
        visual.album_art = QImage()

        direction = visual._prepare_album_flow_for_track_change("unknown")

        self.assertEqual(direction, 1)
        self.assertEqual(visual.music_surface_transition_mode, "white_bridge")
        self.assertEqual(visual.music_surface_transition_from[0], QColor(10, 80, 160))
        self.assertEqual(visual.music_surface_transition_to[0], QColor(245, 247, 250))

    def test_white_bridge_retargets_to_loaded_album_colors_on_track_apply(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "old"
        visual.track_title = "Old"
        visual.artist = "Artist"
        visual.album_color = QColor(10, 80, 160)
        visual.secondary_color = QColor(20, 120, 170)
        visual.audio_frames = []
        visual.lyrics = ["old line"]
        visual.timed_lyrics = []
        visual.messages = []
        visual.plugin_samples = []
        visual._seen_provider_message_keys = set()
        visual.album_art = QImage()
        visual.album_art_signature = ""
        visual.album_history = []
        visual.album_queue = []
        visual.album_queue_signature = ""
        visual.listened_seconds_total = 0.0
        visual.media_position_sync_at = 10.0
        visual.media_position_seconds = 0.0
        visual.media_raw_position_seconds = 0.0
        visual.media_raw_position_source = "windows-media"
        visual._scaled_album_art_cache = {}
        visual._prepare_album_flow_for_track_change("new")
        visual._estimated_media_position = lambda _now: 0.0
        visual._clear_lyric_metrics_cache = lambda: None
        visual._sync_timed_lyrics = lambda *_args, **_kwargs: None
        visual._should_paint_album_island = lambda: False

        track = TrackData(
            title="New",
            artist="Artist",
            lyrics=("new line",),
            album_color=ColorToken(220, 120, 40),
            secondary_color=ColorToken(120, 40, 180),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
            track_id="new",
            is_playing=True,
        )

        visual._apply_visual_data(IslandVisualData(track=track, messages=(), plugins=()))

        self.assertEqual(visual.music_surface_transition_mode, "white_to_album")
        self.assertEqual(visual.music_surface_transition_from[0], QColor(245, 247, 250))
        self.assertEqual(visual.music_surface_transition_to[0], QColor(220, 120, 40))

    def test_album_queue_from_track_preserves_prefetched_colors(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "current"
        visual.track_title = "Current"
        visual.artist = "Artist"
        visual.STORED_ALBUM_ART_MAX_SIDE = 96
        visual._bounded_album_image = lambda image, _max_side, smooth=False: QImage(image)
        art = QImage(8, 8, QImage.Format.Format_ARGB32)
        art.fill(QColor(230, 160, 80))
        entry = AlbumQueueEntry(
            "Next",
            "Artist",
            album_art_bytes=b"",
            track_id="next",
            album_color=ColorToken(220, 120, 40),
            secondary_color=ColorToken(120, 40, 180),
        )
        entry = {"title": entry.title, "artist": entry.artist, "track_id": entry.track_id, "album_art": art, "album_color": entry.album_color, "secondary_color": entry.secondary_color}

        queue = visual._album_queue_from_track([entry])

        self.assertEqual(queue[0]["album_color"], QColor(220, 120, 40))
        self.assertEqual(queue[0]["secondary_color"], QColor(120, 40, 180))

    def test_album_island_slots_make_center_dominant(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        old_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_one.fill(QColor(210, 80, 45))
        old_two = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_two.fill(QColor(80, 210, 110))
        next_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        next_one.fill(QColor(230, 160, 80))
        next_two = QImage(8, 8, QImage.Format.Format_ARGB32)
        next_two.fill(QColor(160, 80, 230))
        visual.album_history = [
            {"track_id": "old-one", "album_art": old_one},
            {"track_id": "old-two", "album_art": old_two},
        ]
        visual.album_queue = [
            {"track_id": "next-one", "album_art": next_one},
            {"track_id": "next-two", "album_art": next_two},
        ]

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        center_slot = next(slot for slot in slots if slot["center"])
        left_one = next(slot for slot in slots if slot["side"] == -1)
        left_two = next(slot for slot in slots if slot["side"] == -2)
        right_one = next(slot for slot in slots if slot["side"] == 1)
        center = center_slot["rect"]
        self.assertGreater(center.width(), left_one["rect"].width())
        self.assertGreater(left_one["rect"].width(), left_two["rect"].width())
        self.assertGreater(center_slot["alpha"], left_one["alpha"])
        self.assertIn("perspective", left_one)
        self.assertIn("perspective", right_one)
        center_spacing = center.center().x() - left_one["rect"].center().x()
        self.assertGreater(center_spacing, center.width() * 0.72)

    def test_album_island_known_queue_transition_slides_new_focus_from_right(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_island_secondary_style = "perspective_dim"
        visual.album_island_perspective_amount = 100
        visual.album_island_transition_started_at = 10.0
        visual.album_island_transition_direction = 1
        visual.music_surface_transition_mode = "queued"
        visual._u = lambda value: float(value)
        current = QImage(8, 8, QImage.Format.Format_ARGB32)
        current.fill(QColor(10, 120, 210))
        next_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        next_art.fill(QColor(230, 160, 80))
        visual.album_art = current
        visual.album_history = [{"track_id": "old-current", "album_art": current}]
        visual.album_queue = [{"track_id": "next", "album_art": next_art}]

        with patch("local.island_stack_visual.time.monotonic", return_value=10.12):
            slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        center_slot = next(slot for slot in slots if slot["center"])
        right_slot = next(slot for slot in slots if slot["side"] == 1)
        self.assertGreater(QRectF(center_slot["rect"]).center().x(), 180.0)
        self.assertGreater(QRectF(right_slot["rect"]).center().x(), QRectF(center_slot["rect"]).center().x())

    def test_album_island_legacy_shrink_style_uses_canonical_perspective(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_island_secondary_style = "shrink_dim"
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        old_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_one.fill(QColor(210, 80, 45))
        next_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        next_one.fill(QColor(230, 160, 80))
        visual.album_history = [{"track_id": "old-one", "album_art": old_one}]
        visual.album_queue = [{"track_id": "next-one", "album_art": next_one}]

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        side_slots = [slot for slot in slots if not bool(slot["center"])]
        self.assertTrue(side_slots)
        self.assertTrue(all(abs(float(slot["tilt"])) <= 0.01 for slot in side_slots))
        self.assertTrue(all(isinstance(slot["perspective"], dict) for slot in side_slots))
        self.assertLess(side_slots[0]["perspective"]["right_height"], side_slots[0]["perspective"]["left_height"])

    def test_album_island_secondary_perspective_style_keeps_flatter_side_covers(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_island_secondary_style = "perspective_dim"
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        old_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_one.fill(QColor(210, 80, 45))
        visual.album_history = [{"track_id": "old-one", "album_art": old_one}]
        visual.album_queue = []

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        center = next(slot for slot in slots if slot["center"])
        left = next(slot for slot in slots if slot["side"] == -1)
        self.assertLess(abs(center["rect"].width() - left["rect"].width()), center["rect"].width() * 0.18)
        self.assertEqual(left["tilt"], 0.0)
        self.assertIn("perspective", left)

    def test_album_island_secondary_perspective_uses_horizontal_quad_shape(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 7
        visual.album_island_cover_size = 34
        visual.album_island_spacing = 112
        visual.album_island_secondary_style = "perspective_dim"
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        old_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_one.fill(QColor(210, 80, 45))
        old_two = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_two.fill(QColor(160, 80, 45))
        next_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        next_one.fill(QColor(230, 160, 80))
        visual.album_history = [
            {"track_id": "old-one", "album_art": old_one},
            {"track_id": "old-two", "album_art": old_two},
        ]
        visual.album_queue = [{"track_id": "next-one", "album_art": next_one}]

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        left_one = next(slot for slot in slots if slot["side"] == -1)
        left_two = next(slot for slot in slots if slot["side"] == -2)
        right_one = next(slot for slot in slots if slot["side"] == 1)
        self.assertEqual(left_one["tilt"], 0.0)
        self.assertIn("perspective", left_one)
        self.assertLess(left_one["perspective"]["right_height"], left_one["perspective"]["left_height"])
        self.assertLess(right_one["perspective"]["left_height"], right_one["perspective"]["right_height"])
        self.assertLess(left_two["perspective"]["right_height"], left_one["perspective"]["right_height"])

    def test_album_island_perspective_amount_controls_side_height_delta(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 34
        visual.album_island_spacing = 112
        visual.album_island_secondary_style = "perspective_dim"
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        old_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_one.fill(QColor(210, 80, 45))
        visual.album_history = [{"track_id": "old-one", "album_art": old_one}]
        visual.album_queue = []

        visual.album_island_perspective_amount = 35
        soft_slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))
        visual.album_island_perspective_amount = 155
        strong_slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        soft_left = next(slot for slot in soft_slots if slot["side"] == -1)
        strong_left = next(slot for slot in strong_slots if slot["side"] == -1)
        soft_delta = soft_left["perspective"]["left_height"] - soft_left["perspective"]["right_height"]
        strong_delta = strong_left["perspective"]["left_height"] - strong_left["perspective"]["right_height"]
        self.assertGreater(strong_delta, soft_delta * 2.0)

    def test_album_island_slots_shift_inward_when_left_cover_hits_boundary(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 7
        visual.album_island_cover_size = 44
        visual.album_island_spacing = 170
        visual.album_island_secondary_style = "shrink_dim"
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        visual.album_history = []
        for index in range(3):
            image = QImage(8, 8, QImage.Format.Format_ARGB32)
            image.fill(QColor(210 - index * 30, 80, 45))
            visual.album_history.append({"track_id": f"old-{index}", "album_art": image})
        visual.album_queue = []

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 180.0, 64.0), reserve_text=True)

        leftmost = min(QRectF(slot["rect"]).left() for slot in slots)
        center = next(slot for slot in slots if slot["center"])
        self.assertGreaterEqual(leftmost, -0.01)
        self.assertLessEqual(QRectF(center["rect"]).center().x(), 90.0)

    def test_album_island_slots_use_distinct_album_sources(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        old_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_one.fill(QColor(210, 80, 45))
        old_two = QImage(8, 8, QImage.Format.Format_ARGB32)
        old_two.fill(QColor(80, 210, 110))
        visual.album_history = [
            {"track_id": "old-one", "album_art": old_one},
            {"track_id": "old-two", "album_art": old_two},
        ]

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        center_slot = next(slot for slot in slots if slot["center"])
        left_slots = [slot for slot in slots if int(slot["side"]) < 0]
        self.assertEqual(center_slot["album_art"].cacheKey(), visual.album_art.cacheKey())
        self.assertIn(old_one.cacheKey(), [slot["album_art"].cacheKey() for slot in left_slots if not slot["album_art"].isNull()])
        self.assertNotIn(visual.album_art.cacheKey(), [slot["album_art"].cacheKey() for slot in slots if not slot["center"] and not slot["album_art"].isNull()])

    def test_album_island_slots_skip_empty_album_placeholders(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 7
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        visual.album_history = []
        visual.album_queue = []

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        self.assertEqual([slot["side"] for slot in slots], [0])

    def test_album_island_slots_show_only_real_right_queue_covers(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 7
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        next_one = QImage(8, 8, QImage.Format.Format_ARGB32)
        next_one.fill(QColor(230, 160, 80))
        visual.album_history = []
        visual.album_queue = [{"track_id": "next-one", "album_art": next_one}]

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        self.assertEqual([slot["side"] for slot in slots], [0, 1])
        self.assertEqual(slots[1]["album_art"].cacheKey(), next_one.cacheKey())

    def test_album_island_slots_do_not_duplicate_current_album_on_right_queue(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.media_track_id = "current"
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        later = QImage(8, 8, QImage.Format.Format_ARGB32)
        later.fill(QColor(230, 160, 80))
        visual.album_history = []
        visual.album_queue = [
            {"track_id": "current", "album_art": visual.album_art},
            {"track_id": "later", "album_art": later},
        ]

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        side_art_keys = [slot["album_art"].cacheKey() for slot in slots if not bool(slot["center"]) and not slot["album_art"].isNull()]
        self.assertNotIn(visual.album_art.cacheKey(), side_art_keys)
        self.assertIn(later.cacheKey(), side_art_keys)

    def test_album_island_slots_skip_current_art_when_same_bytes_decode_to_new_image(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.media_track_id = "current"
        current_bytes = self._png_bytes(QColor(10, 120, 210))
        visual.album_art = QImage()
        self.assertTrue(visual.album_art.loadFromData(current_bytes))
        visual.album_art_signature = visual._album_art_bytes_signature(current_bytes)
        repeated = QImage()
        self.assertTrue(repeated.loadFromData(current_bytes))
        later = QImage(8, 8, QImage.Format.Format_ARGB32)
        later.fill(QColor(230, 160, 80))
        visual.album_history = []
        visual.album_queue = [
            {"track_id": "same-cover-next", "album_art": repeated},
            {"track_id": "later", "album_art": later},
        ]

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        side_art_keys = [slot["album_art"].cacheKey() for slot in slots if not bool(slot["center"]) and not slot["album_art"].isNull()]
        self.assertNotIn(repeated.cacheKey(), side_art_keys)
        self.assertIn(later.cacheKey(), side_art_keys)

    def test_album_queue_from_track_skips_current_art_by_album_art_signature(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "current"
        current_bytes = self._png_bytes(QColor(10, 120, 210))
        visual.album_art = QImage()
        self.assertTrue(visual.album_art.loadFromData(current_bytes))
        visual.album_art_signature = visual._album_art_bytes_signature(current_bytes)
        later_bytes = self._png_bytes(QColor(230, 160, 80))

        queue = visual._album_queue_from_track(
            [
                AlbumQueueEntry("Same Cover", "Artist", track_id="same-cover-next", album_art_bytes=current_bytes),
                AlbumQueueEntry("Later", "Artist", track_id="later", album_art_bytes=later_bytes),
            ]
        )

        self.assertEqual([entry["track_id"] for entry in queue], ["later"])

    def test_album_image_content_signature_is_cached_per_qimage_cache_key(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        image = QImage(32, 32, QImage.Format.Format_ARGB32)
        image.fill(QColor(10, 120, 210))

        first = visual._album_image_content_signature(image)
        cache_after_first = dict(getattr(visual, "_album_image_signature_cache", {}))
        second = visual._album_image_content_signature(image)

        self.assertEqual(first, second)
        self.assertTrue(first)
        self.assertIn(image.cacheKey(), cache_after_first)
        self.assertEqual(cache_after_first, getattr(visual, "_album_image_signature_cache", {}))

    def test_album_island_slots_skip_history_art_matching_current_cover(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        current_bytes = self._png_bytes(QColor(10, 120, 210))
        visual.album_art = QImage()
        self.assertTrue(visual.album_art.loadFromData(current_bytes))
        visual.album_art_signature = visual._album_art_bytes_signature(current_bytes)
        previous_same_cover = QImage()
        self.assertTrue(previous_same_cover.loadFromData(current_bytes))
        older = QImage(8, 8, QImage.Format.Format_ARGB32)
        older.fill(QColor(230, 160, 80))
        visual.album_history = [
            {"track_id": "previous-same-cover", "album_art": previous_same_cover},
            {"track_id": "older", "album_art": older},
        ]
        visual.album_queue = []

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        side_art_keys = [slot["album_art"].cacheKey() for slot in slots if not bool(slot["center"]) and not slot["album_art"].isNull()]
        self.assertNotIn(previous_same_cover.cacheKey(), side_art_keys)
        self.assertIn(older.cacheKey(), side_art_keys)

    def test_music_surface_transition_paints_visible_wash_over_dark_body(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_color = QColor(12, 24, 44)
        visual.secondary_color = QColor(30, 40, 58)
        visual.music_surface_transition_started_at = 100.0
        visual.music_surface_transition_duration = 1.0
        visual.music_surface_transition_mode = "white_to_album"
        visual.music_surface_transition_from = (QColor(245, 247, 250), QColor(228, 233, 241))
        visual.music_surface_transition_to = (QColor(12, 24, 44), QColor(30, 40, 58))
        visual._transition_progress_for_surface = lambda now=None: 0.0
        image = QImage(220, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music_surface_transition_wash(painter, QRectF(0.0, 0.0, 220.0, 70.0), 18.0, expanded=False)
        painter.end()

        center = QColor(image.pixel(110, 35))
        self.assertGreater(center.red(), 220)
        self.assertGreater(center.green(), 220)
        self.assertGreater(center.blue(), 220)

    def test_edge_notch_path_supports_bottom_and_top_attachment(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        rect = QRectF(20.0, 10.0, 200.0, 50.0)

        bottom = visual._edge_notch_path(rect, "bottom")
        top = visual._edge_notch_path(rect, "top")

        self.assertTrue(bottom.contains(QPointF(rect.left() + 1.0, rect.bottom() - 1.0)))
        self.assertFalse(bottom.contains(QPointF(rect.left() + 1.0, rect.top() + 1.0)))
        self.assertTrue(top.contains(QPointF(rect.left() + 1.0, rect.top() + 1.0)))
        self.assertFalse(top.contains(QPointF(rect.left() + 1.0, rect.bottom() - 1.0)))

    def test_stick_music_background_uses_bottom_notch_shape_instead_of_capsule(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "stick"
        visual._u = lambda value: float(value)
        visual._effective_music_ambience_style = lambda: "pure_black"
        visual._active_lyric_color_fill_paints_background = lambda _style=None: False
        visual._paint_music_surface_transition_wash = lambda *_args, **_kwargs: None
        visual._paint_music_compact = lambda *_args, **_kwargs: None
        visual._paint_music_border = lambda *_args, **_kwargs: None
        image = QImage(260, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 10.0, 200.0, 50.0)

        visual._paint_music(painter, rect, expanded=False)
        painter.end()

        self.assertLess(QColor(image.pixelColor(21, 11)).alpha(), 32)
        self.assertGreater(QColor(image.pixelColor(21, 58)).alpha(), 220)

    def test_top_music_background_uses_top_notch_shape_instead_of_capsule(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "top"
        visual._u = lambda value: float(value)
        visual._effective_music_ambience_style = lambda: "pure_black"
        visual._active_lyric_color_fill_paints_background = lambda _style=None: False
        visual._paint_music_surface_transition_wash = lambda *_args, **_kwargs: None
        visual._paint_music_compact = lambda *_args, **_kwargs: None
        visual._paint_music_border = lambda *_args, **_kwargs: None
        image = QImage(260, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 10.0, 200.0, 50.0)

        visual._paint_music(painter, rect, expanded=False)
        painter.end()

        self.assertGreater(QColor(image.pixelColor(21, 11)).alpha(), 220)
        self.assertLess(QColor(image.pixelColor(21, 58)).alpha(), 32)

    def test_expanded_music_layout_styles_satisfy_edge_notch_attachment_standard(self):
        for style in IslandStackVisual.MUSIC_EXPANDED_LAYOUT_STYLES:
            for location_mode, attached_point in (
                ("top", QPoint(21, 11)),
                ("stick", QPoint(21, 118)),
            ):
                with self.subTest(style=style, location_mode=location_mode):
                    visual = IslandStackVisual.__new__(IslandStackVisual)
                    visual.location_mode = location_mode
                    visual.music_expanded_layout_style = style
                    visual._u = lambda value: float(value)
                    visual._effective_music_ambience_style = lambda: "pure_black"
                    visual._active_lyric_color_fill_paints_background = lambda _style=None: False
                    visual._paint_music_surface_transition_wash = lambda *_args, **_kwargs: None
                    visual._paint_music_expanded = lambda *_args, **_kwargs: None
                    visual._paint_music_border = lambda *_args, **_kwargs: None
                    image = QImage(260, 150, QImage.Format.Format_ARGB32)
                    image.fill(QColor(0, 0, 0, 0))
                    painter = QPainter(image)
                    rect = QRectF(20.0, 10.0, 200.0, 110.0)

                    visual._paint_music(painter, rect, expanded=True)
                    painter.end()

                    self.assertGreater(QColor(image.pixelColor(attached_point)).alpha(), 220)

    def test_edge_notch_clip_wraps_all_main_island_kinds_before_music_branch(self):
        source = inspect.getsource(IslandStackVisual._paint_island_frame)
        plan_start = source.index("paint_plan = stack_frame_paint_plan(visuals, self.motions")
        loop_start = source.index("for paint_item in paint_plan.paint_items", plan_start)
        music_branch = source.index("if visual.kind == IslandKind.MUSIC", loop_start)
        segment = source[loop_start:music_branch]

        self.assertIn("visual = paint_item.visual", segment)
        self.assertIn("motion = paint_item.motion", segment)
        self.assertIn("context_plan = stack_paint_context_plan(", segment)
        self.assertIn("edge_notch_active=self._edge_notch_active()", segment)
        self.assertIn("if context_plan.paint_edge_notch_backing:", segment)
        self.assertIn("self._paint_edge_notch_backing(painter, rect)", segment)
        self.assertIn("if context_plan.clip_edge_notch:", segment)
        self.assertIn("painter.setClipPath(self._edge_notch_path(rect, self._edge_notch_edge()))", segment)

    def test_album_island_missing_next_art_appears_as_right_placeholder_not_center_duplicate(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.media_track_id = "current"
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        visual.album_history = []
        visual.album_queue = visual._album_queue_from_track(
            [AlbumQueueEntry("Next", "Artist", track_id="next-without-art")]
        )

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 360.0, 64.0))

        center = next(slot for slot in slots if bool(slot["center"]))
        placeholder = next(slot for slot in slots if bool(slot.get("placeholder")))
        self.assertEqual(center["album_art"].cacheKey(), visual.album_art.cacheKey())
        self.assertEqual(placeholder["side"], 1)
        self.assertTrue(placeholder["album_art"].isNull())

    def test_album_island_expanded_width_grows_to_fit_configured_cover_row(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.lyric_flow_style = "album_island"
        visual.no_lyrics_display_mode = "text"
        visual.lyrics = ["Line"]
        visual.track_title = "Song"
        visual.artist = "Artist"
        visual.album_island_cover_count = 7
        visual.album_island_cover_size = 30
        visual.album_island_compact_cover_size = 30
        visual.album_island_expanded_cover_size = 96
        visual.album_island_spacing = 170
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        visual.album_history = []
        visual.album_queue = []
        for index in range(3):
            left = QImage(8, 8, QImage.Format.Format_ARGB32)
            left.fill(QColor(210 - index * 20, 80, 45))
            right = QImage(8, 8, QImage.Format.Format_ARGB32)
            right.fill(QColor(80, 160 + index * 20, 210))
            visual.album_history.append({"track_id": f"old-{index}", "album_art": left})
            visual.album_queue.append({"track_id": f"next-{index}", "album_art": right})

        width = visual._music_target_width(expanded=True)
        _main_w, main_h = visual._main_size_for_kind(IslandKind.MUSIC, width)
        stage = QRectF(18.0 * IslandStackVisual.VISUAL_SCALE, 58.0 * IslandStackVisual.VISUAL_SCALE, width - 36.0 * IslandStackVisual.VISUAL_SCALE, main_h - 76.0 * IslandStackVisual.VISUAL_SCALE)
        slots = visual._album_island_slots(stage, reserve_text=True)

        self.assertGreater(width, 760.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertGreaterEqual(min(QRectF(slot["rect"]).left() for slot in slots), stage.left() - 0.1)
        self.assertLessEqual(max(QRectF(slot["rect"]).right() for slot in slots), stage.right() + 0.1)

    def test_album_island_compact_replaces_standard_music_cover_layout(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.no_lyrics_display_mode = "album_island"
        visual.no_lyrics_text = "no lyrics"
        visual.lyrics = ["no lyrics"]
        visual.lyric_index = 0
        visual.lyric_flow_style = "wave_stream"
        visual.enable_dual_line_lyrics = False
        calls = []
        visual._paint_album_island_compact = lambda *_args: calls.append("album")
        visual._paint_cover = lambda *_args: calls.append("cover")
        visual._paint_compact_lyric_viewport = lambda *_args: calls.append("viewport")
        image = QImage(320, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music_compact(painter, QRectF(12.0, 12.0, 260.0, 54.0))
        painter.end()

        self.assertEqual(calls, ["album"])

    def test_compact_lyric_can_hide_album_cover_and_left_align_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.no_lyrics_display_mode = "text"
        visual.lyric_flow_style = "wave_stream"
        visual.compact_lyric_show_cover = False
        visual.enable_dual_line_lyrics = False
        visual.lyrics = ["line"]
        visual.lyric_index = 0
        visual.track_title = "Song"
        visual.artist = "Artist"
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        calls = []
        visual._paint_cover = lambda *_args: calls.append(("cover",))
        visual._paint_compact_lyric_viewport = lambda _painter, text_rect: calls.append(("viewport", QRectF(text_rect)))
        image = QImage(320, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music_compact(painter, QRectF(12.0, 12.0, 260.0, 54.0))
        painter.end()

        self.assertFalse(any(call[0] == "cover" for call in calls))
        viewport = next(call[1] for call in calls if call[0] == "viewport")
        self.assertLessEqual(viewport.left(), 24.1)

    def test_album_island_expanded_replaces_standard_music_cover_layout(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.no_lyrics_display_mode = "album_island"
        visual.no_lyrics_text = "no lyrics"
        visual.lyrics = ["no lyrics"]
        visual.track_title = "Current Song"
        visual.artist = "Artist"
        visual.media_is_playing = True
        calls = []
        visual._paint_album_island_expanded = lambda *_args: calls.append("album")
        visual._paint_cover = lambda *_args: calls.append("cover")
        visual._paint_scrolling_text = lambda *_args: calls.append("text")
        visual._paint_lyric_viewport = lambda *_args: calls.append("viewport")
        image = QImage(520, 240, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music_expanded(painter, QRectF(18.0, 18.0, 420.0, 160.0))
        painter.end()

        self.assertEqual(calls, ["album"])
        self.assertTrue(getattr(visual, "music_cover_rect", QRectF()).isNull())

    def test_album_island_compact_fallback_paints_heard_hours_without_future_slots(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_island_cover_count = 5
        visual.album_island_cover_size = 30
        visual.album_island_spacing = 112
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(10, 120, 210))
        previous = QImage(8, 8, QImage.Format.Format_ARGB32)
        previous.fill(QColor(210, 80, 45))
        visual.album_history = [{"track_id": "old-one", "album_art": previous}]
        visual.album_queue = []
        visual.listened_seconds_total = 55 * 3600
        calls = []
        visual._paint_album_island_slot = lambda _painter, slot, _radius: calls.append(("slot", int(slot["side"])))
        visual._paint_album_island_listened_text = lambda _painter, _rect, slots: calls.append(
            ("text", [int(slot["side"]) for slot in slots])
        )
        image = QImage(340, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_album_island_compact(painter, QRectF(12.0, 12.0, 280.0, 56.0))
        painter.end()

        slot_sides = [side for kind, side in calls if kind == "slot"]
        text_calls = [sides for kind, sides in calls if kind == "text"]
        self.assertEqual(slot_sides, [-1, 0])
        self.assertEqual(text_calls, [[-1, 0]])

    def test_album_island_cover_size_and_spacing_are_adjustable(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.update = lambda: None
        visual._emit_settings_changed = lambda *_args: None

        visual.set_album_island_cover_size(34, notify=False)
        visual.set_album_island_spacing(140, notify=False)

        self.assertEqual(visual.album_island_cover_size, 34)
        self.assertEqual(visual.album_island_spacing, 140)

    def test_album_island_spacing_lower_bound_allows_tighter_layout(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.update = lambda: None
        visual._emit_settings_changed = lambda *_args: None

        visual.set_album_island_spacing(48, notify=False)

        self.assertEqual(visual.album_island_spacing, 48)

    def test_apply_visual_data_loads_album_queue_art(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = ""
        visual.media_position_sync_at = 10.0
        visual.media_position_seconds = 0.0
        visual.media_raw_position_seconds = 0.0
        visual.media_raw_position_source = "windows-media"
        visual.lyrics = []
        visual.timed_lyrics = []
        visual.messages = []
        visual.barrage_clock = 0.0
        visual._seen_provider_message_keys = set()
        visual.listened_seconds_total = 0.0
        visual._scaled_album_art_cache = {}
        visual.album_art = QImage()
        visual._estimated_media_position = lambda _now: 0.0
        visual._clear_lyric_metrics_cache = lambda: None
        visual._sync_timed_lyrics = lambda *_args, **_kwargs: None
        queue_bytes = self._png_bytes(QColor(230, 160, 80))

        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("暂无歌词",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
            album_art_bytes=self._png_bytes(QColor(10, 120, 210)),
            album_art_source="thumbnail",
            track_id="song",
            album_queue=(AlbumQueueEntry("Next", "Artist", album_art_bytes=queue_bytes),),
        )

        visual._apply_visual_data(IslandVisualData(track=track, messages=(), plugins=()), initial=True)

        self.assertEqual(len(visual.album_queue), 1)
        self.assertFalse(visual.album_queue[0]["album_art"].isNull())
        self.assertEqual(visual.album_art_source, "thumbnail")

    def test_album_queue_from_track_keeps_missing_future_cover_as_placeholder_and_collapses_repeated_art(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        cover_bytes = self._png_bytes(QColor(230, 160, 80))

        missing_first = visual._album_queue_from_track(
            (
                AlbumQueueEntry("Missing", "Artist"),
                AlbumQueueEntry("Later", "Artist", album_art_bytes=cover_bytes),
            )
        )
        missing_second = visual._album_queue_from_track(
            (
                AlbumQueueEntry("First", "Artist", album_art_bytes=cover_bytes),
                AlbumQueueEntry("Missing", "Artist"),
                AlbumQueueEntry("Later", "Artist", album_art_bytes=cover_bytes),
            )
        )

        self.assertEqual([entry["title"] for entry in missing_first], ["Missing", "Later"])
        self.assertTrue(missing_first[0]["placeholder"])
        self.assertTrue(missing_first[0]["album_art"].isNull())
        self.assertEqual([entry["title"] for entry in missing_second], ["First", "Missing"])
        self.assertTrue(missing_second[1]["placeholder"])

    def test_light_time_module_shell_uses_neutral_border_not_orange_frame(self):
        class FakePainter:
            def __init__(self):
                self.pens = []

            def setPen(self, pen):
                self.pens.append(pen)

            def setBrush(self, *_args):
                pass

            def drawRoundedRect(self, *_args):
                pass

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._system_dark_mode = lambda: False
        painter = FakePainter()

        visual._paint_time_module_shell(painter, QRectF(0.0, 0.0, 140.0, 80.0), visual._plugin_theme_colors())

        border_color = painter.pens[-1].color()
        self.assertLess(abs(border_color.red() - border_color.green()), 18)
        self.assertLess(abs(border_color.green() - border_color.blue()), 18)

    def test_time_palette_exposes_contrast_and_theme_aware_compact_tokens(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._system_dark_mode = lambda: False

        light = visual._time_reference_palette({}, QColor(255, 184, 0))

        self.assertLess(light["accent"].red(), 245)
        self.assertLess(light["module_stroke"].lightness(), 205)
        self.assertIn("compact_cell", light)
        self.assertIn("compact_time_chip", light)
        self.assertGreater(light["compact_cell"].lightness(), light["compact_time_chip"].lightness())

        visual._system_dark_mode = lambda: True
        dark = visual._time_reference_palette({}, QColor(255, 184, 0))

        self.assertLess(dark["compact_cell"].lightness(), 90)
        self.assertGreater(dark["compact_time_text"].lightness(), 120)

    def test_dual_line_compact_music_size_grows(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = False
        visual.enable_dual_line_lyrics = True
        visual.lyrics = ["First lyric line\nSecond lyric line"]
        visual.lyric_index = 0

        _width, height = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)

        self.assertGreaterEqual(height, 80.0 * IslandStackVisual.VISUAL_SCALE)

    def test_dual_line_compact_music_width_tracks_current_lyric(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        width = visual._compact_music_target_width_from_content(
            content_w=100.0,
            title_w=80.0,
            artist_w=70.0,
            lyric_w=120.0,
            current_dual_w=520.0,
            instrumental=False,
        )

        self.assertGreater(width, 610.0 * IslandStackVisual.VISUAL_SCALE)

    def test_dual_line_compact_width_measurement_has_upper_bound(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True
        visual.location_mode = "free"
        visual.lyrics = [
            "This primary lyric line keeps going far beyond a useful layout width and should scroll instead\n"
            "This translated lyric line is also long enough that it must not make the island wider forever"
        ]
        visual.lyric_index = 0
        visual.font_shrink_scale = 1.0
        visual.lyric_elastic_strength = 100
        visual.lyric_word_spacing = 100
        visual._u = lambda value: float(value)

        width = visual._current_compact_dual_line_width(16.0)

        self.assertLessEqual(width, 640.0)

    def test_expanded_dual_line_music_width_uses_measurement_ceiling(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.location_mode = "free"
        visual.track_title = "LOSER"
        visual.artist = "米津玄师"
        visual.enable_dual_line_lyrics = True
        visual.lyric_flow_style = "wave_stream"
        visual.font_shrink_scale = 1.0
        visual.lyric_elastic_strength = 100
        visual.lyric_word_spacing = 100
        visual._u = lambda value: float(value)
        visual._should_paint_album_island = lambda: False
        visual._empty_music_text_active = lambda: False
        visual._is_instrumental_track = lambda: False

        visual.lyrics = ["short primary line\nshort translated line"]
        short_width = visual._music_target_width(expanded=True)
        visual.lyrics = [
            "もう一回もう一回行こうぜ仆らの声 もう一回もう一回行こうぜ仆らの声\n"
            "再一次再一次前行吧我们的声音 再一次再一次前行吧我们的声音"
        ]
        long_width = visual._music_target_width(expanded=True)

        self.assertLessEqual(long_width, 940.0)
        self.assertLess(long_width - short_width, 360.0)

    def test_music_main_width_is_capped_to_screen_bounds_for_expanded_dual_line(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "free"
        visual.finder_mode = False
        visual.expanded = True
        visual.music_playing = True
        visual.music_auto_detect = False
        visual.persistent_plugin = False
        visual.enabled_plugin_ids = set()
        visual.messages = []
        visual.lyrics = ["A very long primary lyric line that would normally push the island wide\nA very long translated lyric line that also wants more width"]
        visual.lyric_index = 0
        visual.track_title = "Long title"
        visual.artist = "Long artist"
        visual.enable_dual_line_lyrics = True
        visual.lyric_flow_style = "wave_stream"
        visual.no_lyrics_display_mode = "text"
        visual.compact_lyric_show_cover = True
        visual.font_shrink_scale = 1.0
        visual.stack_direction = 1
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.hovered_key = ""
        visual.hover_bounce_until = 0.0
        visual.hover_started_at = 0.0
        visual.scene_suppressed = False
        visual.plugin_panel_open = False
        visual.enabled_plugin_ids = set()
        visual._u = lambda value: float(value)
        visual.width = lambda: 1180
        visual.height = lambda: 720
        visual.mapToGlobal = lambda point: QPoint(point.x(), point.y())
        visual._current_screen_geometry = lambda: QRect(0, 0, 960, 540)
        visual._plugin_enabled = lambda: False
        visual._plugin_panel_visible = lambda: False
        visual._folded_message_limit = lambda _kind: 3
        visual._should_paint_album_island = lambda: False
        visual._drag_hides_auxiliary_visuals = lambda: False
        visual._empty_music_text_active = lambda: False
        visual._is_instrumental_track = lambda: False
        visual._active_plugin_alerts = lambda *_args, **_kwargs: []
        visual._taskbar_main_overlay_alert = lambda: None

        music = next(item for item in visual._build_visuals() if item.key == "music")

        self.assertLessEqual(visual._music_target_width(expanded=True), 940.0)
        self.assertGreaterEqual(music.rect.left(), 8.0)
        self.assertLessEqual(music.rect.right(), 952.0)

    def test_finder_music_main_width_is_capped_to_screen_bounds_for_expanded_dual_line(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "finder"
        visual.finder_mode = True
        visual.finder_material = type("FakeFinderMaterial", (), {"bar_height": 34.0})()
        visual.expanded = True
        visual.music_playing = True
        visual.music_auto_detect = False
        visual.persistent_plugin = False
        visual.enabled_plugin_ids = set()
        visual.messages = []
        visual.lyrics = ["A very long primary lyric line that would normally push the island wide\nA very long translated lyric line that also wants more width"]
        visual.lyric_index = 0
        visual.track_title = "Long title"
        visual.artist = "Long artist"
        visual.enable_dual_line_lyrics = True
        visual.lyric_flow_style = "wave_stream"
        visual.no_lyrics_display_mode = "text"
        visual.compact_lyric_show_cover = True
        visual.font_shrink_scale = 1.0
        visual.stack_direction = 1
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.hovered_key = ""
        visual.hover_bounce_until = 0.0
        visual.hover_started_at = 0.0
        visual.scene_suppressed = False
        visual.plugin_panel_open = False
        visual._u = lambda value: float(value)
        visual.width = lambda: 960
        visual.height = lambda: 720
        visual.mapToGlobal = lambda point: QPoint(point.x(), point.y())
        visual._current_screen_geometry = lambda: QRect(0, 0, 960, 540)
        visual._plugin_enabled = lambda: False
        visual._plugin_panel_visible = lambda: False
        visual._folded_message_limit = lambda _kind: 3
        visual._should_paint_album_island = lambda: False
        visual._drag_hides_auxiliary_visuals = lambda: False
        visual._empty_music_text_active = lambda: False
        visual._is_instrumental_track = lambda: False
        visual._active_plugin_alerts = lambda *_args, **_kwargs: []
        visual._taskbar_main_overlay_alert = lambda: None

        music = next(item for item in visual._build_visuals() if item.key == "music")

        self.assertLessEqual(visual._music_target_width(expanded=True), 940.0)
        self.assertGreaterEqual(music.rect.left(), 8.0)
        self.assertLessEqual(music.rect.right(), 952.0)

    def test_dual_line_compact_cover_grows_and_shifts_down(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enable_dual_line_lyrics = True
        visual.lyrics = ["First lyric line\nSecond lyric line"]
        visual.lyric_index = 0
        dual_rect = QRectF(0.0, 0.0, 420.0, 80.0 * IslandStackVisual.VISUAL_SCALE)

        dual_cover = visual._compact_cover_rect(dual_rect)

        visual.lyrics = ["First lyric line"]
        normal_rect = QRectF(0.0, 0.0, 420.0, 46.0 * IslandStackVisual.VISUAL_SCALE)
        normal_cover = visual._compact_cover_rect(normal_rect)

        self.assertAlmostEqual(dual_cover.width(), 50.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertGreater(dual_cover.width(), normal_cover.width())
        self.assertAlmostEqual(dual_cover.center().y(), dual_rect.center().y() + 1.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertAlmostEqual(normal_cover.center().y(), normal_rect.center().y())

    def test_coalesced_timed_lyrics_merge_same_timestamp_as_two_lines(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        lines = visual._coalesced_timed_lyrics(
            [
                (10.0, "第一行歌词"),
                (10.0, "第二行歌词"),
                (14.0, "下一句"),
            ]
        )

        self.assertEqual(lines, [(10.0, "第一行歌词\n第二行歌词"), (14.0, "下一句")])

    def test_coalesced_timed_lyric_data_keeps_primary_units_and_timing_kind(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        units = (
            SimpleNamespace(start_seconds=10.0, end_seconds=10.5, text="谁"),
            SimpleNamespace(start_seconds=10.5, end_seconds=11.1, text="人"),
            SimpleNamespace(start_seconds=11.1, end_seconds=12.4, text="都"),
        )
        rows, row_units, kinds = visual._coalesced_timed_lyric_data([
            SimpleNamespace(time_seconds=10.0, text="谁人都", units=units, timing_kind="unit"),
            SimpleNamespace(time_seconds=10.0, text="Anyone", units=(), timing_kind="line"),
            SimpleNamespace(time_seconds=14.0, text="下一句", units=(), timing_kind="synthetic"),
        ])

        self.assertEqual(rows, [(10.0, "谁人都\nAnyone"), (14.0, "下一句")])
        self.assertEqual(row_units, [units, ()])
        self.assertEqual(kinds, ["unit", "synthetic"])

    def test_coalesced_timed_lyric_data_rejects_units_for_other_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        wrong_units = (SimpleNamespace(start_seconds=1.0, end_seconds=2.0, text="旧词"),)

        rows, row_units, kinds = visual._coalesced_timed_lyric_data([
            SimpleNamespace(time_seconds=1.0, text="新词", units=wrong_units, timing_kind="unit"),
        ])

        self.assertEqual(rows, [(1.0, "新词")])
        self.assertEqual(row_units, [()])
        self.assertEqual(kinds, ["line"])

    def test_coalesced_timed_lyric_data_promotes_timed_original_after_translation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        units = (SimpleNamespace(start_seconds=10.0, end_seconds=11.0, text="原文"),)

        rows, row_units, kinds = visual._coalesced_timed_lyric_data([
            SimpleNamespace(time_seconds=10.0, text="Translation", units=(), timing_kind="line"),
            SimpleNamespace(time_seconds=10.0, text="原文", units=units, timing_kind="unit"),
        ])

        self.assertEqual(rows, [(10.0, "原文\nTranslation")])
        self.assertEqual(row_units, [units])
        self.assertEqual(kinds, ["unit"])

    def test_lyric_phase_uses_actual_unit_clock_without_reading_future_characters(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.timed_lyrics = [(10.0, "谁都"), (14.0, "下一句")]
        visual.timed_lyric_units = [
            (
                TimedLyricUnit(10.0, 11.0, "谁"),
                TimedLyricUnit(12.0, 13.0, "都"),
            ),
            (),
        ]
        visual.timed_lyric_timing_kinds = ["unit", "line"]
        visual.lyric_index = 0
        visual.lyric_changed_at = 10.0
        visual.lyric_last_phase_index = -1
        visual.lyric_last_phase_progress = 0.0
        visual.lyric_last_phase_at = 10.0
        visual.lyric_transition_kind = ""
        visual.media_is_playing = True
        visual._effective_lyric_offset_seconds = lambda: 0.0
        position = [10.5]
        visual._estimated_media_position = lambda _now: position[0]

        self.assertAlmostEqual(visual._lyric_phase(10.5)["progress"], 0.25)
        position[0] = 11.5
        self.assertAlmostEqual(visual._lyric_phase(11.5)["progress"], 0.5)
        position[0] = 12.5
        self.assertAlmostEqual(visual._lyric_phase(12.5)["progress"], 0.75)
        visual.media_is_playing = False
        self.assertAlmostEqual(visual._lyric_phase(20.0)["progress"], 0.75)

    def test_low_perception_treats_one_marker_for_a_sentence_as_row_timing(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.timed_lyrics = [(10.0, "Hello world"), (14.0, "Good night")]
        visual.timed_lyric_units = [
            (TimedLyricUnit(10.0, None, "Hello world"),),
            (TimedLyricUnit(14.0, None, "Good night"),),
        ]
        visual.timed_lyric_timing_kinds = ["unit", "unit"]
        visual.music_surface_reading_strategy = "low_perception"
        visual.lyric_index = 0
        visual.lyric_changed_at = 10.0
        visual.lyric_last_phase_index = -1
        visual.lyric_last_phase_progress = 0.0
        visual.lyric_last_phase_at = 10.0
        visual.lyric_transition_kind = ""
        visual.media_is_playing = True
        visual._effective_lyric_offset_seconds = lambda: 0.0
        visual._estimated_media_position = lambda _now: 11.0

        phase = visual._lyric_phase(11.0)
        self.assertTrue(phase["whole_row"])
        self.assertNotIn("timed_character_front", phase)

    def test_lyric_offset_slider_reanchors_backward_and_applies_active_player_profile(self):
        visual = IslandStackVisual(data_provider=EmptyIslandDataProvider(empty_visual_data()))
        visual.timer.stop()
        surface_calls = []
        visual.music_fullscreen_overlay = SimpleNamespace(
            on_lyric_offset_changed=lambda: surface_calls.append("reanchor"),
        )
        try:
            visual.timed_lyrics = [(0.0, "first"), (5.0, "second"), (10.0, "third")]
            visual.lyrics = [text for _start, text in visual.timed_lyrics]
            visual.lyric_index = 1
            visual.media_is_playing = False
            visual.media_position_seconds = 5.4
            visual.media_position_sync_at = time.monotonic()
            visual.media_app_lyric_offset_seconds = 0.0
            visual.lyric_last_phase_index = 1
            visual.lyric_last_phase_progress = 0.9

            visual.set_lyric_offset_seconds(-2.0, notify=False)
            self.assertEqual(visual.lyric_index, 0)
            self.assertEqual(visual.lyric_last_phase_index, -1)
            self.assertEqual(surface_calls, ["reanchor"])

            visual.media_lyric_offset_adapter = "netease_betterncm"
            self.assertFalse(visual.set_active_music_app_lyric_offset("qq", 3.0))
            self.assertTrue(visual.set_active_music_app_lyric_offset("netease", 3.0))
            self.assertEqual(visual.media_app_lyric_offset_seconds, 3.0)
            self.assertEqual(visual.lyric_index, 1)
            self.assertEqual(surface_calls, ["reanchor", "reanchor"])
        finally:
            visual.music_fullscreen_overlay = None
            visual.close()

    def test_lyric_phase_keeps_line_average_and_synthetic_line_modes(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.timed_lyrics = [(10.0, "谁都"), (14.0, "下一句")]
        visual.timed_lyric_units = [(), ()]
        visual.timed_lyric_timing_kinds = ["line", "synthetic"]
        visual.lyric_index = 0
        visual.lyric_changed_at = 10.0
        visual.lyric_last_phase_index = -1
        visual.lyric_last_phase_progress = 0.0
        visual.lyric_last_phase_at = 10.0
        visual.lyric_transition_kind = ""
        visual.media_is_playing = True
        visual._effective_lyric_offset_seconds = lambda: 0.0
        position = [11.0]
        visual._estimated_media_position = lambda _now: position[0]

        self.assertAlmostEqual(visual._lyric_phase(11.0)["progress"], 0.25)
        visual.lyric_index = 1
        position[0] = 14.2
        self.assertEqual(visual._lyric_phase(14.2)["progress"], 1.0)

    def test_track_data_units_reach_island_phase_through_visual_update(self):
        visual = IslandStackVisual(data_provider=EmptyIslandDataProvider(empty_visual_data()))
        visual.timer.stop()
        self.assertEqual(visual.timer.timerType(), Qt.TimerType.PreciseTimer)
        units = (
            TimedLyricUnit(10.0, 11.0, "\u8c01"),
            TimedLyricUnit(11.0, 12.0, "\u4eba"),
            TimedLyricUnit(12.0, 14.0, "\u90fd"),
        )
        timed = (
            TimedLyricLine(10.0, "\u8c01\u4eba\u90fd", units, "unit"),
            TimedLyricLine(10.0, "Anyone"),
            TimedLyricLine(14.0, "\u4e0b\u4e00\u53e5"),
        )
        track = TrackData(
            title="Song", artist="Artist", lyrics=tuple(line.text for line in timed),
            album_color=ColorToken(60, 60, 60), secondary_color=ColorToken(40, 40, 40),
            audio_frames=(), timed_lyrics=timed, track_id="timed-song",
            position_seconds=10.5, duration_seconds=30.0, is_playing=False,
            lyric_offset_adapter="netease_betterncm",
        )
        try:
            visual._apply_visual_data(IslandVisualData(track, (), ()))
            self.assertEqual(visual.timed_lyrics[0][1], "\u8c01\u4eba\u90fd\nAnyone")
            self.assertEqual(visual.timed_lyric_units[0], units)
            self.assertEqual(visual.timed_lyric_timing_kinds[0], "unit")
            self.assertEqual(visual.media_lyric_offset_adapter, "netease_betterncm")
            phase = visual._lyric_phase(time.monotonic())
            self.assertAlmostEqual(phase["progress"], 0.5 / 3.0)
            self.assertEqual(phase["timed_primary_text"], "\u8c01\u4eba\u90fd")
            self.assertEqual(phase["timed_units"], units)

            synthetic = (
                TimedLyricLine(20.0, "Plain song", timing_kind="synthetic"),
                TimedLyricLine(24.0, "Next", timing_kind="synthetic"),
            )
            next_track = replace(
                track, title="Other", track_id="synthetic-song",
                timed_lyrics=synthetic, lyrics=("Plain song", "Next"),
                position_seconds=20.5,
            )
            visual._apply_visual_data(IslandVisualData(next_track, (), ()))
            self.assertEqual(visual.timed_lyric_timing_kinds[0], "synthetic")
            self.assertEqual(visual._lyric_phase(time.monotonic())["progress"], 1.0)
        finally:
            visual.close()

    def test_same_text_real_timing_upgrade_refreshes_once_and_reanchors_island(self):
        visual = IslandStackVisual(data_provider=EmptyIslandDataProvider(empty_visual_data()))
        visual.timer.stop()
        track = TrackData(
            title="same song", artist="artist", lyrics=("谁都", "下一句"),
            album_color=ColorToken(60, 60, 60), secondary_color=ColorToken(40, 40, 40),
            audio_frames=(), timed_lyrics=(TimedLyricLine(0.0, "谁都"), TimedLyricLine(4.0, "下一句")),
            track_id="upgrade-song", position_seconds=1.5, duration_seconds=30.0, is_playing=False,
        )
        try:
            visual._apply_visual_data(IslandVisualData(track, (), ()))
            visual.lyric_last_phase_index = 0
            visual.lyric_last_phase_progress = 0.9
            units = (TimedLyricUnit(0.0, 2.0, "谁"), TimedLyricUnit(2.0, 4.0, "都"))
            upgraded = replace(track, timed_lyrics=(TimedLyricLine(0.0, "谁都", units, "unit"), TimedLyricLine(4.0, "下一句")))
            self.assertTrue(visual._apply_visual_data(IslandVisualData(upgraded, (), ())))
            self.assertEqual(visual.lyric_last_phase_index, -1)
            self.assertEqual(visual.timed_lyric_timing_kinds[0], "unit")
            self.assertAlmostEqual(visual._lyric_phase(time.monotonic())["progress"], 0.375)
            timing_containers = (visual.timed_lyrics, visual.timed_lyric_units, visual.timed_lyric_timing_kinds)
            self.assertFalse(visual._apply_visual_data(IslandVisualData(upgraded, (), ())))
            self.assertAlmostEqual(visual.lyric_last_phase_progress, 0.375)
            for previous, current in zip(timing_containers, (visual.timed_lyrics, visual.timed_lyric_units, visual.timed_lyric_timing_kinds)):
                self.assertIs(previous, current)
        finally:
            visual.close()

    def test_coalesced_timed_lyrics_does_not_merge_adjacent_duet_lines(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        lines = visual._coalesced_timed_lyrics(
            [
                (10.0, "薛 : 我等你的雨季 如此安静"),
                (18.0, "刘 : 我记得是雨季 爱上的你"),
                (30.0, "薛 : 下一句歌词"),
            ]
        )

        self.assertEqual(
            lines,
            [
                (10.0, "薛 : 我等你的雨季 如此安静"),
                (18.0, "刘 : 我记得是雨季 爱上的你"),
                (30.0, "薛 : 下一句歌词"),
            ],
        )


    def test_coalesced_timed_lyrics_preserves_provider_newline(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        lines = visual._coalesced_timed_lyrics(
            [
                (10.0, "薛：我记得那草坪 一览无遗\n刘：我等你的草坪 高楼耸起"),
                (18.0, "下一句"),
            ]
        )

        self.assertEqual(lines, [(10.0, "薛：我记得那草坪 一览无遗\n刘：我等你的草坪 高楼耸起"), (18.0, "下一句")])

    def test_new_timed_lyrics_can_resync_back_to_current_position(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_is_playing = True
        visual.media_position_seconds = 9.0
        visual.media_position_sync_at = 100.0
        visual.media_duration_seconds = 120.0
        visual.lyric_offset_seconds = 0.0
        visual.timed_lyrics = [(8.0, "current"), (20.0, "next")]
        visual.lyrics = ["current", "next"]
        visual.lyric_index = 1
        visual.prev_lyric_index = 1
        visual.lyric_last_sync_position = 0.0
        visual.lyric_changed_at = 99.0

        visual._sync_timed_lyrics(100.0, allow_backward=True)

        self.assertEqual(visual.lyric_index, 0)

    def test_plugin_alert_prefers_due_or_soon_plugin(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 3
        visual.plugin_samples = [
            ("TIME", "20:00", "05/26", QColor(67, 162, 255), "◷", "time", "normal"),
            ("WEATHER", "26°C 晴", "北京", QColor(80, 210, 255), "☁", "weather", "normal"),
            ("AGENDA", "3 分钟后", "开会", QColor(118, 236, 160), "□", "agenda", "soon"),
        ]

        alert = visual._active_plugin_alert()

        self.assertIsNotNone(alert)
        self.assertEqual(alert[1], "AGENDA")
        self.assertEqual(alert[6], "agenda")

    def test_plugin_alerts_include_time_status_when_collapsed(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 3
        visual.plugin_samples = [
            ("TIME", "计时结束", "完成", QColor(67, 162, 255), "◷", "time", "done"),
            ("WEATHER", "26°C 晴", "北京", QColor(80, 210, 255), "☁", "weather", "normal"),
            ("AGENDA", "现在", "开会", QColor(118, 236, 160), "□", "agenda", "due"),
        ]

        alerts = visual._active_plugin_alerts(2)

        self.assertEqual([alert[6] for alert in alerts], ["agenda", "time"])

    def test_running_timer_creates_time_side_plugin_alert_when_collapsed(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 3
        visual.plugin_samples = [
            ("TIME", "00:24", "倒计时 0:24", QColor(67, 162, 255), "●", "time", "running"),
            ("WEATHER", "26°C 晴", "北京", QColor(80, 210, 255), "☀", "weather", "normal"),
            ("AGENDA", "暂无日程", "JSON / ICS", QColor(118, 236, 160), "■", "agenda", "normal"),
        ]

        alerts = visual._active_plugin_alerts(1)

        self.assertEqual([alert[6] for alert in alerts], ["time"])

    def test_time_side_alerts_split_future_alarm_and_running_countdown(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = [
            {"time": "23:59", "label": "备注", "enabled": True, "frequency_unit": "daily"},
        ]
        visual.plugin_samples = [
            ("TIME", "10:11", "倒计时 4:59", QColor(255, 184, 0), "T", "time", "running", "计时进行中", "倒计时 4:59", 30, 0.4),
        ]

        alerts = visual._active_plugin_alerts(2)

        self.assertEqual([alert[6] for alert in alerts], ["time:alarm", "time"])
        self.assertEqual(alerts[0][9], "备注")
        self.assertIn("4:59", alerts[1][9])

    def test_time_alarm_side_alert_key_paints_alarm_model_not_original_timer_model(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = [
            {"time": "23:59", "label": "备注", "enabled": True, "frequency_unit": "daily"},
        ]
        visual.plugin_samples = [
            ("TIME", "10:11", "倒计时 4:59", QColor(255, 184, 0), "T", "time", "running", "计时进行中", "倒计时 4:59", 30, 0.4),
        ]
        calls = []
        visual._paint_time_plugin_alert = lambda _painter, _rect, label, detail, summary, _color, status, alert_title, alert_body, progress, reminder_kind="": calls.append(
            (label, detail, summary, status, alert_title, alert_body, progress, reminder_kind)
        )
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin_alert(painter, QRectF(12.0, 12.0, 150.0, 42.0), "plugin-alert:time:alarm")
        painter.end()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "备注")
        self.assertEqual(calls[0][2], "闹钟")
        self.assertEqual(calls[0][3], "running")
        self.assertEqual(calls[0][5], "备注")
        self.assertEqual(calls[0][7], "alarm")

    def test_time_virtual_alert_key_forces_split_alarm_and_countdown_symbols(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.scroll_phase = 0.0
        visual.plugin_samples = [
            ("TIME", "10:11", "Countdown 4:59", QColor(255, 184, 0), "T", "time", "running", "Timer running", "Countdown 4:59", 30, 0.4),
        ]
        forced_alerts = {
            "alarm": [(0, "TIME", "4:59", "4:59", QColor(255, 184, 0), "T", "time:alarm", "running", "Timer running", "4:59", 29)],
            "countdown": [(0, "TIME", "4:59", "闹钟", QColor(255, 184, 0), "T", "time", "running", "闹钟", "4:59", 30)],
        }
        symbols = []
        visual._paint_time_symbol = lambda _painter, _rect, kind, *_args: symbols.append(str(kind))
        image = QImage(360, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._active_plugin_alerts = lambda *_args, **_kwargs: forced_alerts["alarm"]
        visual._paint_plugin_alert(painter, QRectF(12.0, 12.0, 150.0, 42.0), "plugin-alert:time:alarm")
        visual._active_plugin_alerts = lambda *_args, **_kwargs: forced_alerts["countdown"]
        visual._paint_plugin_alert(painter, QRectF(174.0, 12.0, 118.0, 42.0), "plugin-alert:time")
        painter.end()

        self.assertEqual(symbols[:2], ["alarm", "countdown"])

    def test_plugin_tuple_reads_progress_field_compatibly(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        plugin = visual._plugin_tuple(("TIME", "12:05", "倒计时 4:52", QColor(67, 162, 255), "●", "time", "running", "", "", 30, 0.52))

        self.assertAlmostEqual(plugin[10], 0.52)

    def test_plugin_theme_surfaces_are_opaque(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        for dark in (False, True):
            visual._system_dark_mode = lambda dark=dark: dark
            theme = visual._plugin_theme_colors()

            for key in ("shell", "shell_expanded", "cell", "cell_active", "alert"):
                self.assertEqual(theme[key].alpha(), 255, key)

    def test_music_and_black_glass_base_surfaces_are_opaque(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        black_image = QImage(96, 64, QImage.Format.Format_ARGB32)
        black_image.fill(QColor(0, 0, 0, 0))
        black_painter = QPainter(black_image)
        visual._paint_black_glass(black_painter, QRectF(8.0, 8.0, 80.0, 44.0), 22.0)
        black_painter.end()

        self.assertEqual(black_image.pixelColor(48, 30).alpha(), 255)

        music_image = QImage(128, 72, QImage.Format.Format_ARGB32)
        music_image.fill(QColor(0, 0, 0, 0))
        music_painter = QPainter(music_image)
        visual._paint_music_glass_body(music_painter, QRectF(8.0, 8.0, 112.0, 52.0), 26.0)
        music_painter.end()

        self.assertEqual(music_image.pixelColor(64, 34).alpha(), 255)

    def test_compact_plugin_value_is_value_only(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        value = visual._plugin_compact_value("TIME", "12:05", "倒计时 4:52", "running")

        self.assertEqual(value, "4:52")
        self.assertNotIn("倒计时", value)

    def test_expanded_plugin_main_surface_stays_strip_height(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True

        _width, height = visual._main_size_for_kind(IslandKind.PLUGIN, 420.0)

        self.assertAlmostEqual(height, 48.0 * IslandStackVisual.VISUAL_SCALE)

    def test_expanded_plugin_strip_defaults_to_equal_widths_without_hover(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.enabled_plugin_count = 3
        visual.plugin_focus_from = 0
        visual.plugin_focus_target = 0
        visual.plugin_focus_changed_at = 0.0

        cells = visual._plugin_item_rects(QRectF(0.0, 0.0, 420.0, 48.0), 1.0, expand_active=False)

        widths = [round(cell.width(), 2) for _index, cell in cells]
        self.assertEqual(widths, [140.0, 140.0, 140.0])

    def test_expanded_plugin_strip_keeps_unfocused_cells_readable(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.enabled_plugin_count = 3
        visual.plugin_focus_from = 0
        visual.plugin_focus_target = 1
        visual.plugin_focus_changed_at = -10.0

        cells = visual._plugin_item_rects(QRectF(0.0, 0.0, 420.0, 48.0), 1.0, expand_active=True)

        widths = [cell.width() for _index, cell in cells]
        self.assertGreater(widths[1], widths[0])
        self.assertGreaterEqual(widths[0], 90.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertGreaterEqual(widths[2], 90.0 * IslandStackVisual.VISUAL_SCALE)

    def test_expanded_plugin_focus_width_is_capped(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.enabled_plugin_count = 3
        visual.plugin_focus_from = 1
        visual.plugin_focus_target = 1
        visual.plugin_focus_changed_at = -10.0

        cells = visual._plugin_item_rects(QRectF(0.0, 0.0, 420.0, 48.0), 1.0, expand_active=True)

        widths = [cell.width() for _index, cell in cells]
        self.assertLessEqual(widths[1], 220.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertGreaterEqual(widths[0], 120.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertGreaterEqual(widths[2], 120.0 * IslandStackVisual.VISUAL_SCALE)

    def test_plugin_alert_display_value_is_value_only(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        value = visual._plugin_alert_display_value("TIME", "12:05:49", "Countdown 0:24", "running", "Timer running", "Countdown 0:24")

        self.assertEqual(value, "0:24")
        self.assertNotIn("Countdown", value)

    def test_plugin_alert_width_is_small_for_value_only_alerts(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        width = visual._plugin_alert_width_for("TIME", "12:05:49", "0:24")

        self.assertLessEqual(width, 174.0 * IslandStackVisual.VISUAL_SCALE)

    def _expanded_plugin_layout_visual(self, music_playing: bool = False):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_playing = music_playing
        visual.music_auto_detect = False
        visual.persistent_plugin = True
        visual.expanded = True
        visual.plugin_panel_open = False
        visual.enabled_plugin_count = 3
        visual.plugin_focus = 0
        visual.plugin_focus_from = 0
        visual.plugin_focus_target = 0
        visual.plugin_focus_changed_at = 0.0
        visual.plugin_samples = []
        visual.messages = []
        visual.finder_mode = False
        visual.pointer_pos = QPointF(450.0, 96.0)
        visual.motions = {}
        visual.music_button_rects = {}
        visual.music_cover_rect = QRectF()
        visual.width = lambda: 900
        visual.height = lambda: 420
        visual._current_screen_geometry = lambda: None
        visual._stack_target_width = lambda _kind: 420.0
        visual._standalone_width_limit = lambda _center_x, _screen: 420.0
        visual.mapToGlobal = lambda point: point
        return visual

    def test_expanded_plugin_keeps_down_panel_hidden_until_focus(self):
        visual = self._expanded_plugin_layout_visual(music_playing=False)

        visuals = visual._build_visuals()

        self.assertEqual([item.key for item in visuals], ["plugin"])
        strip = visuals[0]
        self.assertAlmostEqual(strip.rect.height(), 48.0 * IslandStackVisual.VISUAL_SCALE)

    def test_collapsed_plugin_main_shows_time_alarm_and_countdown_side_alerts(self):
        visual = self._expanded_plugin_layout_visual(music_playing=False)
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.plugin_panel_open = False
        visual.plugin_alarm_due_pending = {}
        visual.plugin_alarm_due_acknowledged_key = ""
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []
        visual.show_seconds = False
        visual.plugin_samples = [
            ("TIME", "07:31", "倒计时 4:58", QColor(255, 184, 0), "T", "time", "alarm", "闹钟提醒", "晨会", 3, 1.0),
        ]

        visuals = visual._build_visuals()

        keys = [item.key for item in visuals]
        self.assertIn("plugin-alert:time:alarm", keys)
        self.assertIn("plugin-alert:time", keys)
        self.assertEqual([item.kind for item in visuals if item.key.startswith("plugin-alert:")], [IslandKind.PLUGIN_ALERT, IslandKind.PLUGIN_ALERT])

    def test_tasker_collapsed_notifications_share_three_external_side_slots(self):
        visual = self._expanded_plugin_layout_visual(music_playing=False)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.plugin_panel_open = False
        visual.taskbar_message_compact_style = "strip"
        visual.taskbar_plugin_compact_style = "rail"
        visual.messages = [
            Message("WeChat", f"Sender {index}", "hello", QColor(75, 210, 110), QColor(75, 210, 110), serial=10 + index)
            for index in range(4)
        ]
        visual._active_plugin_alerts = lambda limit=2, **_kwargs: [
            (0, "TIME", "0:00", "due", QColor(255, 184, 0), "T", "time:countdown", "due", "Timer", "Due", 0),
            (1, "WEATHER", "Rain", "soon", QColor(50, 160, 255), "W", "weather", "soon", "Weather", "Rain soon", 1),
        ][:limit]

        visuals = visual._build_visuals()

        keys = [item.key for item in visuals]
        main = next(item for item in visuals if item.z_index == 10)
        side = [
            item for item in visuals
            if item.key.startswith("plugin-alert:") or item.key.startswith("message-dot:")
        ]
        self.assertNotIn("taskbar-overlay:time:countdown", keys)
        self.assertEqual(len(side), 3)
        self.assertEqual([item.key for item in side[:2]], ["plugin-alert:time:countdown", "plugin-alert:weather"])
        self.assertEqual(side[2].message.serial, 10)
        self.assertTrue(all(item.rect.left() >= main.rect.right() for item in side))
        self.assertGreaterEqual(side[0].rect.left() - main.rect.right(), 9.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertFalse(any(item.kind == IslandKind.MESSAGE and not item.folded for item in visuals))

    def test_expanded_empty_click_does_not_collapse_stack(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.scene_suppressed = False
        visual.drag_candidate = True
        visual.dragging = False
        visual.drag_press_key = ""
        visual._hit_test = lambda _point: ""
        visual.update = lambda: None
        visual._save_placement_state = lambda: None
        collapsed = []
        visual._set_expanded = lambda expanded: collapsed.append(expanded)

        class FakeRelease:
            def button(self):
                return Qt.MouseButton.LeftButton

            def position(self):
                return QPointF(800.0, 300.0)

        visual.mouseReleaseEvent(FakeRelease())

        self.assertEqual(collapsed, [])
        self.assertFalse(visual.drag_candidate)

    def test_plugin_island_double_click_does_not_collapse_expanded_stack(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.scene_suppressed = False
        visual._main_kind = lambda: IslandKind.PLUGIN
        visual.update = lambda: None
        pulses = []
        collapsed = []
        visual._pulse_stack = lambda: pulses.append(True)
        visual._set_expanded = lambda expanded: collapsed.append(bool(expanded))

        class FakeDoubleClick:
            def button(self):
                return Qt.MouseButton.LeftButton

            def position(self):
                return QPointF(120.0, 40.0)

        visual.mouseDoubleClickEvent(FakeDoubleClick())

        self.assertEqual(collapsed, [])
        self.assertEqual(pulses, [])

    def test_music_island_double_click_still_collapses_expanded_stack(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.scene_suppressed = False
        visual._main_kind = lambda: IslandKind.MUSIC
        visual.update = lambda: None
        collapsed = []
        visual._pulse_stack = lambda: None
        visual._set_expanded = lambda expanded: collapsed.append(bool(expanded))

        class FakeDoubleClick:
            def button(self):
                return Qt.MouseButton.LeftButton

            def position(self):
                return QPointF(120.0, 40.0)

        visual.mouseDoubleClickEvent(FakeDoubleClick())

        self.assertEqual(collapsed, [False])

    def test_plugin_focus_click_reveals_separate_down_panel_below_strip(self):
        visual = self._expanded_plugin_layout_visual(music_playing=False)

        visual._activate_key("plugin")
        visuals = visual._build_visuals()

        self.assertEqual([item.key for item in visuals], ["plugin", "plugin-panel"])
        strip, panel = visuals
        self.assertAlmostEqual(strip.rect.height(), 48.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertAlmostEqual(panel.rect.width(), strip.rect.width())
        self.assertAlmostEqual(panel.rect.top(), strip.rect.bottom())
        self.assertEqual(panel.kind.value, "plugin_panel")

    def test_plugin_panel_joins_strip_without_gap(self):
        visual = self._expanded_plugin_layout_visual(music_playing=True)
        visual.plugin_panel_open = True

        visuals = visual._build_visuals()

        strip = next(item for item in visuals if item.key == "plugin")
        panel = next(item for item in visuals if item.key == "plugin-panel")
        self.assertAlmostEqual(panel.rect.top(), strip.rect.bottom())

    def test_plugin_panel_shell_rect_includes_strip_and_panel(self):
        visual = self._expanded_plugin_layout_visual(music_playing=True)
        strip = QRectF(10.0, 20.0, 420.0, 48.0)
        panel = QRectF(10.0, 68.0, 420.0, 188.0)

        shell = visual._plugin_panel_shell_rect(panel, strip)

        self.assertAlmostEqual(shell.top(), strip.top())
        self.assertAlmostEqual(shell.bottom(), panel.bottom())
        self.assertAlmostEqual(shell.left(), strip.left())
        self.assertAlmostEqual(shell.right(), strip.right())

    def test_plugin_panel_initial_motion_starts_from_strip_edge(self):
        visual = self._expanded_plugin_layout_visual(music_playing=True)
        visual.plugin_panel_open = True
        visuals = visual._build_visuals()
        strip = next(item for item in visuals if item.key == "plugin")
        panel = next(item for item in visuals if item.key == "plugin-panel")

        start = visual._initial_motion_rect_for_visual(panel, visuals)

        self.assertAlmostEqual(start.top(), strip.rect.bottom())
        self.assertLessEqual(start.height(), visual._u(12.0))

    def test_plugin_hover_keeps_down_panel_hidden_until_delay(self):
        visual = self._expanded_plugin_layout_visual(music_playing=False)
        visual.scene_suppressed = False
        visual.drag_candidate = False
        visual.last_pointer_move_at = -10.0
        visual.left_stack_at = 0.0
        visual.hovered_key = ""
        visual.hover_armed_key = ""
        visual.hover_started_at = 0.0
        visual.hover_bounce_until = 0.0
        visual.update = lambda: None
        strip = visual._build_visuals()[0]

        class MoveEvent:
            def __init__(self, position: QPointF):
                self._position = position

            def position(self):
                return self._position

            def buttons(self):
                return Qt.MouseButton.NoButton

        visual.mouseMoveEvent(MoveEvent(strip.rect.center()))

        self.assertEqual(visual.hovered_key, "plugin")
        self.assertFalse(visual.plugin_panel_open)
        self.assertNotIn("plugin-panel", [item.key for item in visual._build_visuals()])

    def test_plugin_hover_reveals_down_panel_after_half_second_delay(self):
        visual = self._expanded_plugin_layout_visual(music_playing=True)
        visual.scene_suppressed = False
        visual.hovered_key = "plugin"
        visual.hover_armed_key = ""
        visual.hover_started_at = 0.0
        visual.left_stack_at = 0.0
        visual.last_tick_at = 0.48
        visual.animation_speed = 1.0
        visual.barrage_hovered = False
        visual.barrage_clock = 0.0
        visual.pointer_pos = QPointF(450.0, 120.0)
        visual.super_topmost_enabled = False
        visual.media_is_playing = False
        visual.timed_lyrics = []
        visual.lyrics = []
        visual.scroll_phase = 0.0
        visual.ambient_phase = 0.0
        visual._refresh_media_data = lambda _now: None
        visual._update_scene_state = lambda _now: None
        visual._point_in_barrage = lambda _point: False
        visual._update_wave_values = lambda: None
        visual.update = lambda: None

        with patch("local.island_stack_visual.time.monotonic", return_value=0.49):
            visual._tick()

        self.assertFalse(visual.plugin_panel_open)
        self.assertNotIn("plugin-panel", [item.key for item in visual._build_visuals()])

        with patch("local.island_stack_visual.time.monotonic", return_value=0.51):
            visual._tick()

        self.assertTrue(visual.plugin_panel_open)
        self.assertIn("plugin-panel", [item.key for item in visual._build_visuals()])

    def test_plugin_panel_focus_model_uses_active_plugin_only(self):
        visual = self._expanded_plugin_layout_visual(music_playing=False)
        visual.plugin_focus = 1
        visual.plugin_focus_from = 1
        visual.plugin_focus_target = 1
        visual.plugin_samples = [
            ("TIME", "19:04", "时间", QColor(67, 162, 255), "T", "time", "normal", "", "", 50, 0.0),
            ("WEATHER", "24.6°C", "小雨", QColor(80, 210, 255), "W", "weather", "normal", "", "", 50, 0.0),
            ("AGENDA", "暂无日程", "今天", QColor(118, 236, 160), "A", "agenda", "normal", "", "", 50, 0.0),
        ]

        model = visual._plugin_panel_focus_model()

        self.assertIsNotNone(model)
        self.assertEqual(model["label"], "WEATHER")
        self.assertEqual(model["plugin_id"], "weather")
        self.assertEqual(model["action"]["command"], "weather-refresh")

    def test_plugin_hit_test_falls_back_to_layout_rect_for_hover_activation(self):
        visual = self._expanded_plugin_layout_visual(music_playing=False)
        visual.expanded = False
        visual.motions = {}

        plugin = next(item for item in visual._build_visuals() if item.key == "plugin")

        self.assertEqual(visual._hit_test(plugin.rect.center()), "plugin")

    def test_expanded_music_keeps_plugin_strip_without_panel_until_focus(self):
        visual = self._expanded_plugin_layout_visual(music_playing=True)

        visuals = visual._build_visuals()

        self.assertEqual([item.key for item in visuals], ["music", "plugin"])
        music, strip = visuals
        self.assertGreater(strip.rect.top(), music.rect.bottom())
        self.assertAlmostEqual(strip.rect.height(), 48.0 * IslandStackVisual.VISUAL_SCALE)

        visual._activate_key("plugin")
        visuals = visual._build_visuals()

        self.assertEqual([item.key for item in visuals], ["music", "plugin", "plugin-panel"])
        self.assertAlmostEqual(visuals[2].rect.top(), visuals[1].rect.bottom())

    def test_plugin_panel_closes_when_hover_returns_to_music(self):
        visual = self._expanded_plugin_layout_visual(music_playing=True)
        visual.plugin_panel_open = True
        visual.scene_suppressed = False
        visual.drag_candidate = False
        visual.last_pointer_move_at = -10.0
        visual.left_stack_at = 0.0
        visual.hovered_key = "plugin-panel"
        visual.update = lambda: None
        music = next(item for item in visual._build_visuals() if item.key == "music")

        class MoveEvent:
            def __init__(self, position: QPointF):
                self._position = position

            def position(self):
                return self._position

            def buttons(self):
                return Qt.MouseButton.NoButton

        visual.mouseMoveEvent(MoveEvent(music.rect.center()))

        self.assertTrue(visual.expanded)
        self.assertFalse(visual.plugin_panel_open)

    def test_plugin_panel_stays_open_when_hover_moves_down_to_message(self):
        visual = self._expanded_plugin_layout_visual(music_playing=True)
        visual.plugin_panel_open = True
        visual.scene_suppressed = False
        visual.drag_candidate = False
        visual.last_pointer_move_at = -10.0
        visual.left_stack_at = 0.0
        visual.hovered_key = "plugin-panel"
        visual.update = lambda: None
        visual.messages = [
            Message("QQ", "Alice", "hello", QColor(80, 170, 255), QColor(80, 170, 255), serial=1),
        ]
        message = next(item for item in visual._build_visuals() if item.key.startswith("message:"))

        class MoveEvent:
            def __init__(self, position: QPointF):
                self._position = position

            def position(self):
                return self._position

            def buttons(self):
                return Qt.MouseButton.NoButton

        visual.mouseMoveEvent(MoveEvent(message.rect.center()))

        self.assertTrue(visual.plugin_panel_open)

    def test_finder_full_screen_texture_source_uses_global_xy(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        image = QImage(800, 600, QImage.Format.Format_ARGB32)
        visual.finder_material = FinderGlassMaterial(
            QColor(240, 240, 240, 110),
            QColor(190, 190, 190, 132),
            QColor(110, 110, 110, 128),
            88,
            166,
            50,
            28.0,
            11,
            "test",
            texture_image=image,
            texture_screen_left=100,
            texture_screen_top=50,
            texture_is_full_screen=True,
        )
        visual.mapToGlobal = lambda point: QPoint(point.x() + 120, point.y() + 70)
        target = QRectF(20.0, 30.0, 200.0, 100.0)

        source = visual._finder_texture_source_rect(target, image)

        self.assertAlmostEqual(source.left(), 40.0)
        self.assertAlmostEqual(source.top(), 50.0)
        self.assertAlmostEqual(source.width(), 200.0)
        self.assertAlmostEqual(source.height(), 100.0)

    def test_finder_mode_keeps_normal_music_shell_and_border(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = True
        visual.group_frame_mask_path = object()
        visual.music_ambience_style = "sea_level"
        calls = []
        visual._paint_music_glass_body = lambda *_args: calls.append("shell")
        visual._paint_bottom_ambience = lambda *_args: calls.append("ambience")
        visual._paint_music_blur_mask = lambda *_args: calls.append("blur")
        visual._paint_music_light_wave = lambda *_args: calls.append("wave")
        visual._paint_music_border = lambda *_args: calls.append("border")
        visual._paint_music_compact = lambda *_args: calls.append("compact")
        image = QImage(320, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music(painter, QRectF(20.0, 20.0, 240.0, 48.0), False)
        painter.end()

        self.assertEqual(calls, ["shell", "ambience", "blur", "wave", "compact", "border"])

    def test_wave_color_fill_is_ignored_on_sea_level_music_background(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "wave_stream"
        visual.wave_color_fill_enabled = True
        visual.gelv_color_fill_enabled = False
        visual.music_ambience_style = "sea_level"
        calls = []
        visual._paint_music_glass_body = lambda *_args: calls.append("shell")
        visual._paint_bottom_ambience = lambda *_args: calls.append("ambience")
        visual._paint_music_blur_mask = lambda *_args: calls.append("blur")
        visual._paint_music_light_wave = lambda *_args: calls.append("wave")
        visual._paint_music_compact = lambda *_args: calls.append("compact")
        visual._paint_music_border = lambda *_args: calls.append("border")
        image = QImage(320, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_music(painter, QRectF(20.0, 20.0, 240.0, 48.0), False)
        painter.end()

        self.assertFalse(visual._active_lyric_color_fill_enabled())
        self.assertEqual(calls, ["shell", "ambience", "blur", "wave", "compact", "border"])

    def test_same_track_black_placeholder_album_art_does_not_replace_existing_cover(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "same-track"
        visual.media_position_sync_at = 10.0
        visual.media_position_seconds = 12.0
        visual.media_raw_position_seconds = 12.0
        visual.media_raw_position_source = "windows-media"
        visual.lyrics = ["line"]
        visual.timed_lyrics = []
        visual.messages = []
        visual._seen_provider_message_keys = set()
        visual.listened_seconds_total = 0.0
        visual._scaled_album_art_cache = {}
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(210, 36, 44))
        visual._estimated_media_position = lambda _now: 12.0
        visual._clear_lyric_metrics_cache = lambda: None
        visual._sync_timed_lyrics = lambda *_args, **_kwargs: None

        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("line",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
            album_art_bytes=self._png_bytes(QColor(0, 0, 0)),
            track_id="same-track",
            position_seconds=12.0,
            position_sampled_at=9.5,
        )

        visual._apply_visual_data(IslandVisualData(track=track, messages=(), plugins=()))

        kept = visual.album_art.pixelColor(0, 0)
        self.assertEqual((kept.red(), kept.green(), kept.blue()), (210, 36, 44))
        self.assertEqual(visual.media_position_sampled_at, 9.5)

    def test_message_refresh_black_placeholder_album_art_does_not_replace_existing_cover(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "old-track"
        visual.track_title = "Song"
        visual.artist = "Artist"
        visual.media_position_sync_at = 10.0
        visual.media_position_seconds = 12.0
        visual.media_raw_position_seconds = 12.0
        visual.media_raw_position_source = "windows-media"
        visual.lyrics = ["line"]
        visual.timed_lyrics = []
        visual.messages = []
        visual._seen_provider_message_keys = set()
        visual.listened_seconds_total = 0.0
        visual._scaled_album_art_cache = {}
        visual.album_history = []
        visual.album_queue = []
        visual.album_island_transition_started_at = 0.0
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(210, 36, 44))
        visual._estimated_media_position = lambda _now: 12.0
        visual._clear_lyric_metrics_cache = lambda: None
        visual._sync_timed_lyrics = lambda *_args, **_kwargs: None

        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("line",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
            album_art_bytes=self._png_bytes(QColor(0, 0, 0)),
            track_id="message-refresh",
            position_seconds=12.0,
        )

        visual._apply_visual_data(
            IslandVisualData(
                track=track,
                messages=(MessageData("WeChat", "Alice", "new", ColorToken(75, 210, 110), ColorToken(75, 210, 110)),),
                plugins=(),
            )
        )

        kept = visual.album_art.pixelColor(0, 0)
        self.assertEqual((kept.red(), kept.green(), kept.blue()), (210, 36, 44))

    def test_message_refresh_same_visible_track_does_not_start_album_surface_transition(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "old-track"
        visual.track_title = "Song"
        visual.artist = "Artist"
        visual.media_position_sync_at = 10.0
        visual.media_position_seconds = 12.0
        visual.media_raw_position_seconds = 12.0
        visual.media_raw_position_source = "windows-media"
        visual.lyrics = ["line"]
        visual.timed_lyrics = []
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.lyric_changed_at = 10.0
        visual.messages = []
        visual._seen_provider_message_keys = set()
        visual.listened_seconds_total = 0.0
        visual._scaled_album_art_cache = {}
        visual.album_history = []
        visual.album_queue = []
        visual.album_queue_signature = ""
        visual.album_island_transition_started_at = 0.0
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(210, 36, 44))
        visual._estimated_media_position = lambda _now: 12.0
        visual._clear_lyric_metrics_cache = lambda: None
        visual._sync_timed_lyrics = lambda *_args, **_kwargs: None
        transition_calls = []
        visual._begin_music_surface_transition = lambda *args, **kwargs: transition_calls.append((args, kwargs))

        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("line",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
            album_art_bytes=self._png_bytes(QColor(0, 0, 0)),
            track_id="message-refresh-id-churn",
            position_seconds=12.0,
        )

        visual._apply_visual_data(
            IslandVisualData(
                track=track,
                messages=(MessageData("WeChat", "Alice", "new", ColorToken(75, 210, 110), ColorToken(75, 210, 110)),),
                plugins=(),
            )
        )

        self.assertEqual(transition_calls, [])
        self.assertEqual(visual.album_history, [])
        self.assertEqual(visual.lyric_index, 0)

    def test_coarse_uia_lyric_clock_stays_in_observed_row_until_new_sample(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.timed_lyrics = [(20.0, "observed"), (24.0, "next"), (28.0, "later")]
        visual.media_position_seconds = 23.92
        visual.media_position_sync_at = 100.0
        visual.media_duration_seconds = 100.0
        visual.media_is_playing = True
        visual.media_position_source = "netease-uia-lyrics"

        self.assertAlmostEqual(visual._estimated_media_position(101.0), 23.92)
        self.assertAlmostEqual(visual._estimated_media_position(105.0), 23.92)
        visual.media_position_seconds = 24.25
        visual.media_position_sync_at = 105.0
        self.assertAlmostEqual(visual._estimated_media_position(110.0), 27.92)

        visual.media_position_seconds = 23.92
        visual.media_position_sync_at = 100.0
        visual.media_position_source = "netease-uia-progress"
        self.assertAlmostEqual(visual._estimated_media_position(101.0), 24.92)
        visual.media_position_source = "local-background"
        self.assertAlmostEqual(visual._estimated_media_position(101.0), 24.92)

    def test_coarse_uia_lyric_row_does_not_cross_despite_normal_visual_lead(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.timed_lyrics = [(20.0, "observed"), (24.0, "next"), (28.0, "later")]
        visual.lyrics = ["observed", "next", "later"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.media_position_seconds = 23.92
        visual.media_position_sync_at = 100.0
        visual.media_duration_seconds = 100.0
        visual.media_is_playing = True
        visual.media_position_source = "netease-uia-lyrics"
        visual.lyric_offset_seconds = 0.0
        visual.media_app_lyric_offset_seconds = 0.0
        visual.lyric_last_sync_position = 23.0

        for now in (100.0, 100.5, 101.0, 105.0):
            self.assertFalse(visual._sync_timed_lyrics(now))
            self.assertEqual(visual.lyric_index, 0)
        visual.media_position_seconds = 24.25
        visual.media_position_sync_at = 105.0
        self.assertTrue(visual._sync_timed_lyrics(105.1))
        self.assertEqual(visual.lyric_index, 1)

        visual.lyric_index = 0
        visual.media_position_seconds = 23.92
        visual.media_position_sync_at = 100.0
        visual.media_position_source = "netease-uia-progress"
        self.assertTrue(visual._sync_timed_lyrics(100.0))
        self.assertEqual(visual.lyric_index, 1)

    def test_same_title_new_duration_with_message_resets_old_song_clock(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "old-version"
        visual.track_title = "Song"
        visual.artist = "Artist"
        visual.media_position_sync_at = 10.0
        visual.media_position_seconds = 92.0
        visual.media_duration_seconds = 120.0
        visual.media_raw_position_seconds = 92.0
        visual.media_raw_position_source = "windows-media"
        visual.lyrics = ["line"]
        visual.timed_lyrics = []
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.messages = []
        visual._seen_provider_message_keys = set()
        visual.listened_seconds_total = 0.0
        visual._scaled_album_art_cache = {}
        visual.album_history = []
        visual.album_queue = []
        visual.album_queue_signature = ""
        visual.album_island_transition_started_at = 0.0
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(210, 36, 44))
        visual._estimated_media_position = lambda _now: 92.0
        visual._prepare_album_flow_for_track_change = lambda _track_id: 1
        visual._clear_lyric_metrics_cache = lambda: None
        visual._sync_timed_lyrics = lambda *_args, **_kwargs: None
        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("new version",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
            track_id="new-version",
            duration_seconds=210.0,
            position_seconds=0.0,
        )

        visual._apply_visual_data(IslandVisualData(
            track=track,
            messages=(MessageData("WeChat", "Alice", "new", ColorToken(75, 210, 110), ColorToken(75, 210, 110)),),
            plugins=(),
        ))
        self.assertEqual(visual.media_track_id, "new-version")
        self.assertEqual(visual.media_position_seconds, 0.0)
        self.assertEqual(visual.lyrics, ["new version"])

    def test_message_popup_paint_does_not_change_album_cover_pixels(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._taskbar_compact_active = lambda: False
        visual._message_card_progress = lambda _rect: 1.0
        visual._paint_scrolling_text = lambda *_args, **_kwargs: None
        visual.media_track_id = "stable-track"
        visual.secondary_color = QColor(20, 90, 180)
        visual._scaled_album_art_cache = {}

        def font(size: float, weight: QFont.Weight = QFont.Weight.Normal, *_args, **_kwargs) -> QFont:
            out = QFont()
            out.setPointSize(max(1, int(size)))
            out.setWeight(weight)
            return out

        visual._font = font
        album_art = QImage(32, 32, QImage.Format.Format_ARGB32)
        album_art.fill(QColor(214, 42, 56, 255))
        visual.album_art = album_art

        image = QImage(300, 140, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cover = QRectF(34.0, 74.0, 48.0, 48.0)
        message_rect = QRectF(34.0, 14.0, 228.0, 46.0)
        visual._paint_cover(painter, cover, 10.0)
        before_message = QImage(image)

        visual._paint_message(
            painter,
            message_rect,
            Message("WeChat", "Alice", "new", QColor(75, 210, 110), QColor(75, 210, 110)),
            folded=False,
        )
        painter.end()

        cover_region = cover.toAlignedRect().adjusted(2, 2, -2, -2)
        self.assertEqual(self._image_region_difference(before_message, image, cover_region), 0)

    def test_message_refresh_near_black_placeholder_album_art_does_not_replace_existing_cover(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "old-track"
        visual.track_title = "Song"
        visual.artist = "Artist"
        visual.media_position_sync_at = 10.0
        visual.media_position_seconds = 12.0
        visual.media_raw_position_seconds = 12.0
        visual.media_raw_position_source = "windows-media"
        visual.lyrics = ["line"]
        visual.timed_lyrics = []
        visual.messages = []
        visual._seen_provider_message_keys = set()
        visual.listened_seconds_total = 0.0
        visual._scaled_album_art_cache = {}
        visual.album_history = []
        visual.album_queue = []
        visual.album_island_transition_started_at = 0.0
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(210, 36, 44))
        visual._estimated_media_position = lambda _now: 12.0
        visual._clear_lyric_metrics_cache = lambda: None
        visual._sync_timed_lyrics = lambda *_args, **_kwargs: None

        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("line",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
            album_art_bytes=self._near_black_placeholder_png_bytes(),
            track_id="message-refresh",
            position_seconds=12.0,
        )

        visual._apply_visual_data(
            IslandVisualData(
                track=track,
                messages=(MessageData("WeChat", "Alice", "new", ColorToken(75, 210, 110), ColorToken(75, 210, 110)),),
                plugins=(),
            )
        )

        kept = visual.album_art.pixelColor(0, 0)
        self.assertEqual((kept.red(), kept.green(), kept.blue()), (210, 36, 44))

    def test_message_refresh_black_placeholder_album_art_is_rejected_without_existing_cover(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "old-track"
        visual.track_title = "Song"
        visual.artist = "Artist"
        visual.media_position_sync_at = 10.0
        visual.media_position_seconds = 12.0
        visual.media_raw_position_seconds = 12.0
        visual.media_raw_position_source = "windows-media"
        visual.lyrics = ["line"]
        visual.timed_lyrics = []
        visual.messages = []
        visual._seen_provider_message_keys = set()
        visual.listened_seconds_total = 0.0
        visual._scaled_album_art_cache = {}
        visual.album_history = []
        visual.album_queue = []
        visual.album_island_transition_started_at = 0.0
        visual.album_art = QImage()
        visual._estimated_media_position = lambda _now: 12.0
        visual._clear_lyric_metrics_cache = lambda: None
        visual._sync_timed_lyrics = lambda *_args, **_kwargs: None

        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("line",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
            album_art_bytes=self._png_bytes(QColor(0, 0, 0)),
            track_id="message-refresh",
            position_seconds=12.0,
        )

        visual._apply_visual_data(
            IslandVisualData(
                track=track,
                messages=(MessageData("WeChat", "Alice", "new", ColorToken(75, 210, 110), ColorToken(75, 210, 110)),),
                plugins=(),
            )
        )

        self.assertTrue(visual.album_art.isNull())

    def test_message_refresh_missing_album_art_does_not_clear_existing_cover_for_same_song(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "old-track"
        visual.track_title = "Song"
        visual.artist = "Artist"
        visual.media_position_sync_at = 10.0
        visual.media_position_seconds = 12.0
        visual.media_raw_position_seconds = 12.0
        visual.media_raw_position_source = "windows-media"
        visual.lyrics = ["line"]
        visual.timed_lyrics = []
        visual.messages = []
        visual._seen_provider_message_keys = set()
        visual.listened_seconds_total = 0.0
        visual._scaled_album_art_cache = {}
        visual.album_history = []
        visual.album_queue = []
        visual.album_island_transition_started_at = 0.0
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(210, 36, 44))
        visual._estimated_media_position = lambda _now: 12.0
        visual._clear_lyric_metrics_cache = lambda: None
        visual._sync_timed_lyrics = lambda *_args, **_kwargs: None

        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("line",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
            album_art_bytes=b"",
            track_id="message-refresh",
            position_seconds=12.0,
        )

        visual._apply_visual_data(
            IslandVisualData(
                track=track,
                messages=(MessageData("WeChat", "Alice", "new", ColorToken(75, 210, 110), ColorToken(75, 210, 110)),),
                plugins=(),
            )
        )

        kept = visual.album_art.pixelColor(0, 0)
        self.assertEqual((kept.red(), kept.green(), kept.blue()), (210, 36, 44))

    def test_same_visible_track_missing_album_art_does_not_clear_existing_cover(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "old-track"
        visual.track_title = "Song"
        visual.artist = "Artist"
        visual.media_position_sync_at = 10.0
        visual.media_position_seconds = 12.0
        visual.media_raw_position_seconds = 12.0
        visual.media_raw_position_source = "windows-media"
        visual.lyrics = ["line"]
        visual.timed_lyrics = []
        visual.messages = []
        visual._seen_provider_message_keys = set()
        visual.listened_seconds_total = 0.0
        visual._scaled_album_art_cache = {}
        visual.album_history = []
        visual.album_queue = []
        visual.album_island_transition_started_at = 0.0
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(210, 36, 44))
        visual._estimated_media_position = lambda _now: 12.0
        visual._clear_lyric_metrics_cache = lambda: None
        visual._sync_timed_lyrics = lambda *_args, **_kwargs: None

        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("line",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
            album_art_bytes=b"",
            track_id="same-visible-title-refresh",
            position_seconds=12.0,
        )

        visual._apply_visual_data(IslandVisualData(track=track, messages=(), plugins=()))

        kept = visual.album_art.pixelColor(0, 0)
        self.assertEqual((kept.red(), kept.green(), kept.blue()), (210, 36, 44))

    def test_album_queue_from_track_keeps_missing_art_placeholder_without_blocking_later_covers(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        red = QImage(8, 8, QImage.Format.Format_ARGB32)
        red.fill(QColor(230, 20, 20))
        entries = [
            AlbumQueueEntry(title="No Art", artist="A", album_art_bytes=b"", track_id="no-art"),
            {"title": "With Art", "artist": "B", "album_art": red, "track_id": "with-art"},
        ]

        queue = visual._album_queue_from_track(entries)

        self.assertEqual(len(queue), 2)
        self.assertEqual(queue[0]["track_id"], "no-art")
        self.assertTrue(queue[0]["placeholder"])
        self.assertEqual(queue[1]["track_id"], "with-art")

    def test_apply_visual_data_reuses_same_album_queue_without_decoding_again(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "same-track"
        visual.track_title = "Song"
        visual.artist = "Artist"
        visual.media_position_sync_at = 10.0
        visual.media_position_seconds = 12.0
        visual.media_raw_position_seconds = 12.0
        visual.media_raw_position_source = "windows-media"
        visual.lyrics = ["line"]
        visual.timed_lyrics = []
        visual.messages = []
        visual.plugin_samples = []
        visual._seen_provider_message_keys = set()
        visual.listened_seconds_total = 0.0
        visual._scaled_album_art_cache = {}
        visual.album_history = []
        visual.album_queue = []
        visual.album_queue_signature = ""
        visual.album_art_signature = ""
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(210, 36, 44))
        visual._estimated_media_position = lambda _now: 12.0
        visual._clear_lyric_metrics_cache = lambda: None
        visual._sync_timed_lyrics = lambda *_args, **_kwargs: None
        visual._should_paint_album_island = lambda: True
        calls = []
        original = visual._album_queue_from_track

        def counting_queue(entries):
            calls.append(tuple(entries or ()))
            return original(entries)

        visual._album_queue_from_track = counting_queue
        queue_entries = (
            AlbumQueueEntry("Next", "Artist", track_id="next"),
        )
        track = TrackData(
            title="Song",
            artist="Artist",
            lyrics=("line",),
            album_color=ColorToken(1, 2, 3),
            secondary_color=ColorToken(4, 5, 6),
            audio_frames=(AudioFrame(0.0, 0.0, 0.0),),
            album_art_bytes=b"",
            track_id="same-track",
            position_seconds=12.0,
            album_queue=queue_entries,
        )

        visual._apply_visual_data(IslandVisualData(track=track, messages=(), plugins=()))
        visual._apply_visual_data(IslandVisualData(track=track, messages=(), plugins=()))

        self.assertEqual(len(calls), 1)

    def test_album_queue_from_track_skips_current_visible_track_and_current_art_duplicates(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "current-id"
        visual.track_title = "Song"
        visual.artist = "Artist"
        current_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        current_art.fill(QColor(20, 80, 220))
        visual.album_art = current_art
        entries = [
            {"track_id": "queue-id", "title": "Song", "artist": "Artist", "album_art": current_art},
            {"track_id": "next", "title": "Next", "artist": "Artist", "album_art": current_art},
        ]

        queue = visual._album_queue_from_track(entries)

        self.assertEqual([entry["track_id"] for entry in queue], [])

    def test_tasker_album_island_uses_configured_cover_count(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.location_mode = "tasker"
        visual.expanded = False
        visual.album_island_cover_count = 5
        visual.album_island_compact_cover_size = 30
        visual.album_island_expanded_cover_size = 72
        visual.album_island_spacing = 100
        visual.album_island_secondary_style = "shrink_dim"
        visual.album_island_perspective_amount = 100
        visual.album_island_transition_started_at = 0.0
        visual.audio_started_at = 0.0
        visual.album_art = QImage(8, 8, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(20, 80, 220))
        visual.album_history = []
        visual.album_queue = []
        for index in range(2):
            left = QImage(8, 8, QImage.Format.Format_ARGB32)
            left.fill(QColor(20 + index, 40, 80))
            right = QImage(8, 8, QImage.Format.Format_ARGB32)
            right.fill(QColor(120 + index, 40, 80))
            visual.album_history.append({"track_id": f"left-{index}", "album_art": left})
            visual.album_queue.append({"track_id": f"right-{index}", "album_art": right})

        slots = visual._album_island_slots(QRectF(0.0, 0.0, 220.0, 34.0))

        self.assertEqual(len(slots), 5)
        self.assertEqual([int(slot["side"]) for slot in slots], [-2, -1, 0, 1, 2])

    def test_album_island_uses_expanded_cover_size_for_expanded_stage(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.location_mode = "free"
        visual.expanded = False
        visual.album_island_cover_size = 30
        visual.album_island_compact_cover_size = 26
        visual.album_island_expanded_cover_size = 78

        compact = visual._album_island_cover_px(QRectF(0.0, 0.0, 240.0, 42.0))
        visual.expanded = True
        expanded = visual._album_island_cover_px(QRectF(0.0, 0.0, 420.0, 122.0))

        self.assertAlmostEqual(compact, 26.0, delta=0.01)
        self.assertAlmostEqual(expanded, 78.0, delta=0.01)

    def test_album_island_reset_clears_persistent_record(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.listened_seconds_total = 7210.0
        visual.album_history = [{"track_id": "old"}]
        visual.album_island_transition_started_at = 1.0
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        visual.reset_album_island_record(notify=False)

        self.assertEqual(visual.listened_seconds_total, 0.0)
        self.assertEqual(visual.album_history, [])
        self.assertEqual(visual.album_island_transition_started_at, 0.0)

    def test_music_ambience_migrates_removed_styles_and_keeps_supported_styles(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        visual.set_music_ambience_style("pure_black", notify=False)
        self.assertEqual(visual.music_ambience_style, "system")

        visual.set_music_ambience_style("pure_white", notify=False)
        self.assertEqual(visual.music_ambience_style, "system")

        visual.set_music_ambience_style("duotone_full", notify=False)
        self.assertEqual(visual.music_ambience_style, "sea_level")

        visual.set_music_ambience_style("bilateral_diffraction", notify=False)
        self.assertEqual(visual.music_ambience_style, "sea_level")

        visual.set_music_ambience_style("yaoguang", notify=False)
        self.assertEqual(visual.music_ambience_style, "sea_level")

        visual.set_music_ambience_style("solid_album", notify=False)
        self.assertEqual(visual.music_ambience_style, "solid_album")

    def test_solid_album_background_blocks_color_fill_and_uses_contrast_neutral_unread_lyrics(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "solid_album"
        visual.lyric_flow_style = "wave_stream"
        visual.wave_color_fill_enabled = True
        visual.gelv_color_fill_enabled = False
        visual.album_color = QColor(180, 90, 45)
        visual.secondary_color = QColor(180, 90, 45)

        self.assertFalse(visual._active_lyric_color_fill_enabled())
        unread = visual._music_lyric_base_text_color()
        self.assertEqual(unread.getRgb(), (255, 255, 255, 132))
        self.assertLess(visual._music_lyric_base_text_color(82).alpha(), unread.alpha())
        self.assertEqual(visual._music_lyric_text_color().getRgb()[:3], (255, 255, 255))

    def test_solid_album_background_uses_monochrome_album_gray_not_blue_fallback(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "solid_album"
        visual.album_art = QImage(60, 60, QImage.Format.Format_RGB32)
        visual.album_art.fill(QColor(220, 220, 220))
        visual.album_color = QColor(22, 105, 205)
        visual.secondary_color = QColor(16, 80, 190)
        visual.solid_album_background_fallback = "#16BCFF"

        color = visual._solid_album_background_color()
        unread = visual._music_lyric_base_text_color()

        self.assertEqual(color.red(), color.green())
        self.assertEqual(color.green(), color.blue())
        self.assertEqual(unread.red(), unread.green())
        self.assertEqual(unread.green(), unread.blue())
        self.assertNotEqual(color.name().upper(), "#16BCFF")

    def test_solid_album_preserves_role_extraction_but_displays_original_primary_and_neutral_ink(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.music_ambience_style = "solid_album"
        art = QImage(100, 100, QImage.Format.Format_RGB32)
        art.fill(QColor(8, 9, 12))
        with QPainter(art) as painter:
            painter.fillRect(0, 55, 100, 35, QColor(181, 112, 59))
            painter.fillRect(0, 90, 100, 10, QColor(226, 184, 43))
        visual.album_art = art
        visual.album_color = QColor(21, 46, 92)
        visual.secondary_color = QColor(61, 89, 142)

        palette = visual._solid_album_surface_palette()
        backdrop = visual._solid_album_background_color()
        unread = visual._music_lyric_base_text_color()
        repeat = visual._music_lyric_base_text_color()

        self.assertGreater(palette.secondary.red(), palette.secondary.blue() + 60)
        self.assertGreater(palette.characteristic.red(), palette.characteristic.blue() + 80)
        self.assertEqual(backdrop.getRgb(), (8, 9, 12, 255))
        self.assertEqual(unread.getRgb(), (255, 255, 255, 132))
        self.assertEqual(unread.rgba(), repeat.rgba())
        composite = MusicSurfaceWindow._composited_color(unread, backdrop)
        self.assertGreaterEqual(MusicSurfaceWindow._contrast_ratio(composite, backdrop), 2.0)

    def test_solid_album_keeps_a_single_role_color_across_island_shell(self):
        visual = self._reactive_music_background_visual(
            "solid_album", response=self._music_reactive_response(), bands=(0.0, 0.0, 0.0),
        )
        art = QImage(100, 100, QImage.Format.Format_RGB32)
        art.fill(QColor(8, 9, 12))
        with QPainter(art) as painter:
            painter.fillRect(0, 55, 100, 45, QColor(181, 112, 59))
        visual.album_art = art
        image = self._paint_music_background_image(visual)
        samples = [image.pixelColor(x, 48) for x in (45, 160, 255)]

        self.assertTrue(all(color.getRgb() == (8, 9, 12, 255) for color in samples))

    def test_solid_album_reactive_light_stays_with_artwork_roles_not_provider_blue(self):
        visual = self._reactive_music_background_visual(
            "solid_album",
            response=self._music_reactive_response(
                energy=0.9, solid_accent_alpha=1.0, solid_accent_lightness=0.8,
                side_wave_height=0.7, surface_glow=0.7,
            ),
            bands=(0.9, 0.8, 0.7),
        )
        art = QImage(100, 100, QImage.Format.Format_RGB32)
        art.fill(QColor(8, 9, 12))
        with QPainter(art) as painter:
            painter.fillRect(0, 55, 100, 35, QColor(181, 112, 59))
            painter.fillRect(0, 90, 100, 10, QColor(226, 184, 43))
        visual.album_art = art
        # The host's provider swatches are deliberately blue/pink. The solid
        # style must use the artwork's shadow/warm companion even in motion.
        image = self._paint_music_background_image(visual)
        for x in (42, 75, 160, 245, 258):
            sample = image.pixelColor(x, 48)
            self.assertEqual(sample.getRgb(), (8, 9, 12, 255))

        visual.album_art.fill(QColor(210, 210, 210))
        monochrome = self._paint_music_background_image(visual)
        for x in (42, 75, 160, 245, 258):
            sample = monochrome.pixelColor(x, 48)
            self.assertLessEqual(max(sample.red(), sample.green(), sample.blue()) - min(sample.red(), sample.green(), sample.blue()), 2)

    def test_solid_album_palette_does_not_change_other_island_lyric_styles(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_color = QColor(204, 50, 54)
        for style, expected in (
            ("sea_level", (255, 255, 255)),
            ("pure_black", (255, 255, 255)),
            ("pure_white", (0, 0, 0)),
        ):
            visual.music_ambience_style = style
            visual._music_system_dark = style != "pure_white"
            self.assertEqual(visual._music_lyric_base_text_color().getRgb()[:3], expected)

    def test_ember_scroll_line_starts_at_left_edge_and_advances_by_line_progress(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.scroll_phase = 18.0
        rect = QRectF(40.0, 12.0, 120.0, 28.0)
        layout = LyricLayout(
            text="a long ember lyric line",
            chunks=("a long ember lyric line",),
            widths=(260.0,),
            total_width=260.0,
            font_key=("test",),
        )

        start_x = visual._ember_layout_x(rect, layout, progress=0.0)
        middle_x = visual._ember_layout_x(rect, layout, progress=0.5)
        end_x = visual._ember_layout_x(rect, layout, progress=1.0)

        self.assertAlmostEqual(start_x, rect.left(), delta=0.01)
        self.assertLess(middle_x, start_x - 40.0)
        self.assertAlmostEqual(end_x, rect.right() - layout.total_width, delta=0.01)

    def test_color_fill_keeps_conflicting_music_ambience_styles_inactive(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = "wave_stream"
        visual.wave_color_fill_enabled = True
        visual.gelv_color_fill_enabled = False
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        visual.set_music_ambience_style("sea_level", notify=False)
        self.assertEqual(visual.music_ambience_style, "sea_level")
        self.assertFalse(visual._active_lyric_color_fill_enabled())

        visual.set_music_ambience_style("duotone_full", notify=False)
        self.assertEqual(visual.music_ambience_style, "sea_level")
        self.assertFalse(visual._active_lyric_color_fill_enabled())

        visual.set_music_ambience_style("solid_album", notify=False)
        self.assertEqual(visual.music_ambience_style, "solid_album")
        self.assertFalse(visual._active_lyric_color_fill_enabled())

    def test_legacy_gelv_and_ember_color_fill_settings_stay_inactive(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.album_color = QColor(80, 120, 170)
        visual.music_ambience_style = "sea_level"
        visual.update = lambda: None
        visual._notify_settings_changed = lambda: None

        visual.lyric_flow_style = "gelv_songscatter"
        visual.gelv_color_fill_enabled = False
        visual.set_gelv_color_fill_enabled(True, notify=False)

        self.assertEqual(visual.music_ambience_style, "sea_level")
        self.assertTrue(visual.gelv_color_fill_enabled)
        self.assertFalse(visual._active_lyric_color_fill_enabled())
        visual.music_ambience_style = "pure_black"
        self.assertFalse(visual._active_lyric_color_fill_enabled())

        visual.lyric_flow_style = "wave_stream"
        visual.music_ambience_style = "sea_level"
        visual.wave_color_fill_enabled = False
        visual.set_wave_color_fill_enabled(True, notify=False)

        self.assertTrue(visual.wave_color_fill_enabled)
        self.assertFalse(visual._active_lyric_color_fill_enabled())
        visual.music_ambience_style = "pure_black"
        self.assertFalse(visual._active_lyric_color_fill_enabled())

        visual.lyric_flow_style = "ember_fade"
        visual.music_ambience_style = "sea_level"
        visual.ember_color_fill_enabled = False
        visual.set_ember_color_fill_enabled(True, notify=False)

        self.assertTrue(visual.ember_color_fill_enabled)
        self.assertFalse(visual._active_lyric_color_fill_enabled())
        visual.music_ambience_style = "pure_black"
        self.assertFalse(visual._active_lyric_color_fill_enabled())

    def test_music_surface_paint_plan_orders_solid_album_background_before_content(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._taskbar_compact_active = lambda: False
        visual._effective_music_ambience_style = lambda: "solid_album"
        visual._normalize_music_expanded_layout_style = lambda style: str(style)
        visual._active_lyric_color_fill_paints_background = lambda _style=None: False
        visual.music_expanded_layout_style = "standard"
        calls = []
        visual._paint_solid_album_music_background = lambda *_args: calls.append("solid")
        visual._paint_music_reactive_background_overlay = lambda *_args: calls.append("reactive")
        visual._paint_music_surface_transition_wash = lambda *_args: calls.append("transition")
        visual._paint_music_compact = lambda *_args: calls.append("compact")
        visual._paint_music_border = lambda *_args: calls.append("border")

        visual._paint_music(None, QRectF(20.0, 20.0, 240.0, 48.0), False)

        self.assertEqual(calls, ["solid", "reactive", "transition", "compact", "border"])

    def test_music_surface_paint_records_step_diagnostics(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._taskbar_compact_active = lambda: False
        visual._effective_music_ambience_style = lambda: "solid_album"
        visual._normalize_music_expanded_layout_style = lambda style: str(style)
        visual._active_lyric_color_fill_paints_background = lambda _style=None: False
        visual.music_expanded_layout_style = "standard"
        calls = []
        visual._paint_solid_album_music_background = lambda *_args: calls.append("solid")
        visual._paint_music_reactive_background_overlay = lambda *_args: calls.append("reactive")
        visual._paint_music_surface_transition_wash = lambda *_args: calls.append("transition")
        visual._paint_music_compact = lambda *_args: calls.append("compact")
        visual._paint_music_border = lambda *_args: calls.append("border")

        with patch("local.island_stack_visual.time.perf_counter", side_effect=[100.000 + index * 0.001 for index in range(32)]):
            visual._paint_music(None, QRectF(20.0, 20.0, 240.0, 48.0), False)
        diagnostics = visual._visual_paint_section_diagnostics_summary()

        self.assertEqual(calls, ["solid", "reactive", "transition", "compact", "border"])
        self.assertIn("music.total", diagnostics["sections"])
        for section in (
            "music.solid_album_background",
            "music.reactive_overlay",
            "music.surface_transition",
            "music.compact_content",
            "music.border",
        ):
            with self.subTest(section=section):
                self.assertIn(section, diagnostics["sections"])
                self.assertEqual(diagnostics["sections"][section]["sample_count"], 1)
        self.assertGreater(diagnostics["sections"]["music.total"]["last_ms"], 0.0)

    def test_removed_music_styles_fall_back_before_building_paint_plan(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._taskbar_compact_active = lambda: False
        visual._normalize_music_expanded_layout_style = lambda style: str(style)
        visual._active_lyric_color_fill_paints_background = lambda _style=None: False
        visual.music_expanded_layout_style = "standard"
        calls = []
        visual._paint_music_glass_body = lambda *_args: calls.append("glass")
        visual._paint_bottom_ambience = lambda *_args: calls.append("ambience")
        visual._paint_music_blur_mask = lambda *_args: calls.append("blur")
        visual._paint_music_light_wave = lambda *_args: calls.append("wave")
        visual._paint_music_reactive_background_overlay = lambda *_args: calls.append("reactive")
        visual._paint_music_surface_transition_wash = lambda *_args: calls.append("transition")
        visual._paint_music_compact = lambda *_args: calls.append("compact")
        visual._paint_music_border = lambda *_args: calls.append("border")

        for style in ("yaoguang", "bilateral_diffraction"):
            with self.subTest(style=style):
                visual.music_ambience_style = style
                calls.clear()
                self.assertEqual(visual._effective_music_ambience_style(), "sea_level")
                visual._paint_music(None, QRectF(20.0, 20.0, 240.0, 48.0), False)
                self.assertEqual(calls, ["glass", "ambience", "blur", "wave", "reactive", "transition", "compact", "border"])

    def test_music_surface_paint_plan_order_keeps_dual_panel_isolated(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual._taskbar_compact_active = lambda: False
        visual._effective_music_ambience_style = lambda: "sea_level"
        visual._normalize_music_expanded_layout_style = lambda style: str(style)
        visual._active_lyric_color_fill_paints_background = lambda _style=None: False
        visual.music_expanded_layout_style = "dual_panel"
        calls = []
        visual._paint_dual_panel_music_background = lambda *_args: calls.append("dual")
        visual._paint_music_reactive_background_overlay = lambda *_args: calls.append("reactive")
        visual._paint_music_surface_transition_wash = lambda *_args: calls.append("transition")
        visual._paint_music_expanded = lambda *_args: calls.append("expanded")
        visual._paint_music_border = lambda *_args: calls.append("border")

        visual._paint_music(None, QRectF(20.0, 20.0, 320.0, 128.0), True)

        self.assertEqual(calls, ["dual", "expanded", "border"])


    def test_finder_time_side_alert_keeps_own_background_instead_of_material_replacement(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = True
        visual.scroll_phase = 0.0
        visual._system_dark_mode = lambda: True
        visual._paint_plugin_alert_material_background = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Finder mode must not replace the side alert's own background")
        )
        visual._paint_time_symbol = lambda *_args, **_kwargs: None
        visual._paint_plugin_progress_ring = lambda *_args, **_kwargs: None
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(12.0, 12.0, 156.0, 42.0)

        visual._paint_time_plugin_alert(
            painter,
            rect,
            "TIME",
            "0:26",
            "Countdown 0:26",
            QColor(255, 184, 0),
            "running",
            "Timer running",
            "Countdown 0:26",
            0.75,
            "countdown",
        )
        painter.end()

    def test_finder_generic_side_alert_keeps_own_background_for_future_plugins(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = True
        visual.enabled_plugin_count = 1
        visual.scroll_phase = 0.0
        visual._system_dark_mode = lambda: True
        visual.plugin_samples = [
            ("WEATHER", "Storm", "Right side content", QColor(80, 210, 255), "W", "weather", "severe", "Weather", "Right side content", 20, 1.0),
        ]
        visual._paint_plugin_alert_material_background = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Finder mode must not replace the side alert's own background")
        )
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin_alert(painter, QRectF(12.0, 12.0, 180.0, 42.0), "plugin-alert:weather")
        painter.end()

    def test_finder_outer_frame_expands_to_cover_plugin_side_alerts(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.finder_mode = True
        visual.expanded = False
        visual.group_frame_motion = None
        visual.finder_hover_rect = QRectF()
        visual.finder_material = FinderGlassMaterial(
            QColor(190, 198, 194, 255),
            QColor(170, 184, 178, 255),
            QColor(146, 164, 158, 255),
            92,
            150,
            54,
            28.0,
            11,
            "test",
            available=True,
        )
        visual.width = lambda: 900
        visual._has_current_two_line_lyric = lambda: False
        main_rect = QRectF(180.0, 42.0, 420.0, 48.0)
        alert_rect = QRectF(612.0, 45.0, 132.0, 42.0)
        main_motion = SpringRect(main_rect)
        main_motion.opacity = 1.0
        alert_motion = SpringRect(alert_rect)
        alert_motion.opacity = 1.0
        items = [
            (StackVisual("music", IslandKind.MUSIC, main_rect, False, None, 10), main_motion),
            (StackVisual("plugin-alert:time", IslandKind.PLUGIN_ALERT, alert_rect, False, None, 8), alert_motion),
        ]
        image = QImage(900, 180, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_myfinder_group_frame(painter, items, 1.0)
        painter.end()

        self.assertGreater(visual.finder_hover_rect.right(), alert_rect.right())
        self.assertLess(visual.finder_hover_rect.left(), main_rect.left())

    def test_expanded_plugin_panel_is_not_overlong(self):
        visual = self._expanded_plugin_layout_visual(music_playing=False)
        visual.expanded = True
        visual.plugin_panel_open = True

        panels = [item for item in visual._build_visuals() if item.key == "plugin-panel"]
        self.assertEqual(len(panels), 1)
        panel = panels[0]

        self.assertEqual(panel.rect.height(), visual._plugin_panel_height(panel.rect.width()))
        self.assertLessEqual(panel.rect.height(), visual._u(240.0))

    def test_plugin_function_lines_are_runtime_not_settings(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        title, lines = visual._plugin_function_lines("WEATHER", "26C Sunny", "Beijing wind humidity 40%", "normal")

        self.assertIn("天气", title)
        self.assertIn("26C Sunny", lines)
        self.assertNotIn("手动城市", " ".join(lines))

    def test_running_timer_alert_prefers_timer_summary(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        title, body = visual._plugin_alert_text_parts("TIME", "20:00:05", "倒计时 0:24", "running")

        self.assertIn("计时", title)
        self.assertEqual(body, "倒计时 0:24")

    def test_weather_loading_creates_plugin_alert(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 3
        visual.plugin_samples = [
            ("TIME", "20:00", "05/27", QColor(67, 162, 255), "●", "time", "normal", "", "", 50),
            ("WEATHER", "北京", "天气刷新中", QColor(80, 210, 255), "☁", "weather", "loading", "天气刷新中", "北京", 40),
            ("AGENDA", "暂无日程", "JSON / ICS", QColor(118, 236, 160), "■", "agenda", "normal", "", "", 50),
        ]

        alerts = visual._active_plugin_alerts(1)

        self.assertEqual([alert[6] for alert in alerts], ["weather"])

    def test_plugin_alert_text_prefers_provider_alert_copy(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        title, body = visual._plugin_alert_text_parts("WEATHER", "北京", "天气刷新中", "loading", "天气刷新中", "北京")

        self.assertEqual(title, "WEATHER · 天气刷新中")
        self.assertEqual(body, "北京")

    def test_plugin_alert_priority_comes_before_status_order(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 3
        visual.plugin_samples = [
            ("TIME", "00:24", "倒计时 0:24", QColor(67, 162, 255), "●", "time", "running", "计时进行中", "倒计时 0:24", 30),
            ("WEATHER", "北京", "天气刷新中", QColor(80, 210, 255), "☁", "weather", "loading", "天气刷新中", "北京", 40),
            ("AGENDA", "4 分钟后", "准备开会", QColor(118, 236, 160), "■", "agenda", "soon", "日程将近", "4 分钟后 · 准备开会", 10),
        ]

        alerts = visual._active_plugin_alerts(2)

        self.assertEqual([alert[6] for alert in alerts], ["agenda", "time"])

    def test_plugin_alert_width_adapts_to_content(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        short_width = visual._plugin_alert_width_for("TIME", "00:24", "倒计时")
        long_width = visual._plugin_alert_width_for("AGENDA", "12 分钟后", "一个很长很长的会议提醒")

        self.assertGreater(long_width, short_width)
        self.assertLessEqual(long_width, 252.0 * IslandStackVisual.VISUAL_SCALE)

    def test_plugin_alert_rect_stays_inside_narrow_window(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual.width = lambda: 620
        visual.height = lambda: 260
        main_rect = QRectF(120.0, 82.0, 480.0, 48.0)

        rect = visual._plugin_alert_rect(main_rect, 0, 1)

        self.assertGreaterEqual(rect.left(), visual._u(8.0))
        self.assertLessEqual(rect.right(), visual.width() - visual._u(8.0))

    def test_plugin_alert_screen_bounds_do_not_exceed_local_canvas(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.width = lambda: 705
        visual.mapToGlobal = lambda point: QPoint(point.x() + 100, point.y())
        visual._current_screen_geometry = lambda: QRect(0, 0, 2000, 600)

        _left, right = visual._screen_bounds_in_local()

        self.assertLessEqual(right, visual.width() - visual._u(8.0))

    def test_time_alarm_and_countdown_side_alerts_stay_in_one_stable_row(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [
            (0, "TIME", "所爱衬所爱", "闹钟", QColor(255, 184, 0), "T", "time:alarm", "alarm", "闹钟提醒", "所爱衬所爱", 3),
            (0, "TIME", "4:58", "倒计时 4:58", QColor(255, 184, 0), "T", "time", "running", "计时进行中", "倒计时 4:58", 30),
        ]
        visual._screen_bounds_in_local = lambda: (0.0, 760.0)
        visual._main_rect_screen_pressure = lambda *_args: ""
        visual.height = lambda: 260
        main_rect = QRectF(150.0, 82.0, 460.0, 48.0)

        first = visual._plugin_alert_rect(main_rect, 0, 2)
        second = visual._plugin_alert_rect(main_rect, 1, 2)

        self.assertGreater(first.center().x(), main_rect.right())
        self.assertGreater(second.center().x(), main_rect.right())
        self.assertFalse(first.intersects(second))
        self.assertFalse(first.intersects(main_rect))
        self.assertFalse(second.intersects(main_rect))

    def test_time_alarm_and_countdown_side_alerts_do_not_split_left_right_when_cramped(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [
            (0, "TIME", "晨会", "闹钟", QColor(255, 184, 0), "T", "time:alarm", "alarm", "闹钟提醒", "晨会", 3),
            (0, "TIME", "4:58", "倒计时 4:58", QColor(255, 184, 0), "T", "time", "running", "计时进行中", "倒计时 4:58", 30),
        ]
        visual._screen_bounds_in_local = lambda: (0.0, 720.0)
        visual._main_rect_screen_pressure = lambda *_args: ""
        visual.height = lambda: 260
        main_rect = QRectF(300.0, 82.0, 350.0, 48.0)

        first = visual._plugin_alert_rect(main_rect, 0, 2)
        second = visual._plugin_alert_rect(main_rect, 1, 2)

        first_on_right = first.center().x() > main_rect.center().x()
        second_on_right = second.center().x() > main_rect.center().x()
        self.assertEqual(first_on_right, second_on_right)

    def test_standalone_canvas_reserves_room_for_side_alerts(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        width = visual._standalone_canvas_width()

        self.assertGreaterEqual(width, visual._u(1160.0))

    def test_multiple_alerts_split_full_rectangles_before_boundary_instead_of_shrinking(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [
            (0, "TIME", "0:00", "时间到", QColor(255, 184, 0), "", "time", "done", "计时结束", "时间到", 10),
            (1, "WEATHER", "雷雨", "right side content", QColor(80, 210, 255), "☁", "weather", "severe", "严重天气", "right side content", 20),
        ]
        visual._screen_bounds_in_local = lambda: (0.0, 900.0)
        visual._main_rect_screen_pressure = lambda *_args: ""
        visual.height = lambda: 260
        main_rect = QRectF(260.0, 82.0, 380.0, 48.0)

        first = visual._plugin_alert_rect(main_rect, 0, 2)
        second = visual._plugin_alert_rect(main_rect, 1, 2)

        self.assertGreater(first.width(), first.height() * 2.4)
        self.assertGreater(second.width(), second.height() * 2.4)
        self.assertFalse(first.intersects(main_rect))
        self.assertFalse(second.intersects(main_rect))
        self.assertNotEqual(first.center().x() > main_rect.center().x(), second.center().x() > main_rect.center().x())

    def test_multiple_circular_plugin_alerts_split_sides_when_no_side_can_fit_row(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual._active_plugin_alerts = lambda *_args, **_kwargs: [
            (0, "TIME", "0:00", "时间到", QColor(255, 184, 0), "", "time", "done", "计时结束", "时间到", 10),
            (1, "WEATHER", "雷雨", "北京", QColor(80, 210, 255), "☁", "weather", "severe", "严重天气", "北京", 20),
        ]
        visual._screen_bounds_in_local = lambda: (0.0, 800.0)
        visual._main_rect_screen_pressure = lambda *_args: ""
        visual.height = lambda: 260
        main_rect = QRectF(40.0, 82.0, 720.0, 48.0)

        first = visual._plugin_alert_rect(main_rect, 0, 2)
        second = visual._plugin_alert_rect(main_rect, 1, 2)

        self.assertAlmostEqual(first.width(), first.height(), delta=0.5)
        self.assertAlmostEqual(second.width(), second.height(), delta=0.5)
        self.assertFalse(first.intersects(second))
        self.assertFalse(first.intersects(main_rect))
        self.assertFalse(second.intersects(main_rect))

    def test_plugin_alert_moves_left_when_right_screen_edge_would_clip_it(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual.finder_mode = False
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda point: QPoint(point.x() + 240, point.y())
        visual._current_screen_geometry = lambda: QRect(0, 0, 800, 600)
        main_rect = QRectF(520.0, 80.0, 220.0, 48.0)

        rect = visual._plugin_alert_rect(main_rect, 0, 1)

        self.assertLessEqual(rect.right(), main_rect.left() - visual._u(9.0))
        self.assertAlmostEqual(rect.height(), 42.0 * IslandStackVisual.VISUAL_SCALE)

    def test_plugin_alert_becomes_circular_left_when_main_shrinks_against_right_edge(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual.finder_mode = False
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda point: QPoint(point.x() + 320, point.y())
        visual._current_screen_geometry = lambda: QRect(0, 0, 800, 600)
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        main_rect = QRectF(365.0, 80.0, 210.0, 48.0)

        rect = visual._plugin_alert_rect(main_rect, 0, 1)

        self.assertAlmostEqual(rect.width(), rect.height())
        self.assertLessEqual(rect.right(), main_rect.left() - visual._u(9.0))

    def test_plugin_alert_becomes_circular_right_when_main_shrinks_against_left_edge(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual.finder_mode = False
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda point: QPoint(point.x() - 460, point.y())
        visual._current_screen_geometry = lambda: QRect(0, 0, 800, 600)
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        main_rect = QRectF(365.0, 80.0, 210.0, 48.0)

        rect = visual._plugin_alert_rect(main_rect, 0, 1)

        self.assertAlmostEqual(rect.width(), rect.height())
        self.assertGreaterEqual(rect.left(), main_rect.right() + visual._u(9.0))

    def test_multiple_circular_plugin_alerts_layout_horizontally(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual.finder_mode = False
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda point: QPoint(point.x() + 320, point.y())
        visual._current_screen_geometry = lambda: QRect(0, 0, 800, 600)
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        main_rect = QRectF(365.0, 80.0, 210.0, 48.0)

        first = visual._plugin_alert_rect(main_rect, 0, 2)
        second = visual._plugin_alert_rect(main_rect, 1, 2)

        self.assertAlmostEqual(first.width(), first.height())
        self.assertAlmostEqual(second.width(), second.height())
        self.assertAlmostEqual(first.center().y(), second.center().y())
        self.assertGreaterEqual(first.left(), second.right() + visual._u(6.0))

    def test_multiple_rectangular_plugin_alerts_layout_horizontally(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 168.0
        visual.finder_mode = False
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda point: QPoint(point.x(), point.y())
        visual._current_screen_geometry = lambda: QRect(0, 0, 1200, 600)
        main_rect = QRectF(80.0, 80.0, 220.0, 48.0)

        first = visual._plugin_alert_rect(main_rect, 0, 2)
        second = visual._plugin_alert_rect(main_rect, 1, 2)

        self.assertAlmostEqual(first.center().y(), second.center().y())
        self.assertGreaterEqual(second.left(), first.right() + visual._u(6.0))

    def test_plugin_alert_drag_anchor_uses_main_center(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        main_rect = QRectF(100.0, 70.0, 300.0, 48.0)

        offset = visual._drag_main_offset_for_press(
            QPointF(430.0, 94.0),
            main_rect,
            "plugin-alert:time",
        )

        self.assertAlmostEqual(offset.x(), main_rect.width() / 2.0)
        self.assertAlmostEqual(offset.y(), main_rect.height() / 2.0)

    def test_dragging_main_motion_snaps_to_target_for_direct_follow(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.dragging = True
        visual.drag_press_key = "music"
        visual.motions = {"music": SpringRect(QRectF(12.0, 18.0, 24.0, 12.0))}
        visual.motions["music"].vx = 14.0
        visual.motions["music"].vy = -9.0
        target = StackVisual("music", IslandKind.MUSIC, QRectF(260.0, 72.0, 420.0, 48.0), False, None, 10)

        self.assertTrue(visual._snap_drag_main_motion_to_target(target))

        motion = visual.motions["music"]
        self.assertEqual(motion.rect, target.rect)
        self.assertEqual((motion.vx, motion.vy, motion.vw, motion.vh), (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(motion.opacity, 1.0)

    def test_disabling_super_topmost_releases_win32_topmost_state(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        calls = []
        visual._force_super_topmost = lambda: calls.append("force")
        visual._release_super_topmost = lambda: calls.append("release")
        visual._notify_settings_changed = lambda: calls.append("notify")
        visual.update = lambda: calls.append("update")

        visual.set_super_topmost(False)

        self.assertFalse(visual.super_topmost_enabled)
        self.assertEqual(calls, ["release", "notify", "update"])

    def test_standalone_drag_window_position_clamps_to_screen_edges(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        screen_geo = QRect(0, 0, 800, 600)
        main_left = 365.0
        main_top = 54.0
        main_w = 210.0
        main_h = 48.0

        top_left = visual._clamp_standalone_window_pos(-999, -999, main_left, main_top, main_w, main_h, screen_geo)
        bottom_right = visual._clamp_standalone_window_pos(9999, 9999, main_left, main_top, main_w, main_h, screen_geo)

        self.assertEqual(top_left.x(), int(round(screen_geo.left() + visual._u(6.0) - main_left)))
        self.assertEqual(top_left.y(), int(round(screen_geo.top() + visual._u(6.0) - main_top)))
        self.assertEqual(bottom_right.x(), int(round(screen_geo.right() - visual._u(6.0) - main_left - main_w)))
        self.assertEqual(bottom_right.y(), int(round(screen_geo.bottom() - visual._u(6.0) - main_top - main_h)))

    def test_standalone_drag_release_preserves_main_anchor_when_auto_direction_changes(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "free"
        visual.finder_mode = False
        visual.expanded = True
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.stack_direction = 1
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 720
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._standalone_width_limit_for_global_center = lambda _center, _screen_geo, target_width=None: float(target_width or 420.0)
        visual._main_size_for_kind = lambda _kind, _stack_w: (420.0, 48.0)
        visual._main_top_for_direction = lambda _kind, _main_h, direction: 72.0 if direction >= 0 else 600.0
        visual._standalone_bottom_boundary_extension_for_screen = lambda _screen: 0.0
        visual._bottom_taskbar_rect_for_screen = lambda _screen: QRectF()
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: False
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        anchor = QRectF(710.0, 1018.0, 420.0, 48.0)
        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            visual._settle_standalone_drag_release(anchor, QPoint(920, 1042))

        self.assertEqual(visual.stack_direction, -1)
        self.assertTrue(moves)
        self.assertAlmostEqual(moves[-1].y() + 600.0, anchor.top(), delta=1.0)

    def test_standalone_drag_release_clamps_main_inside_target_screen_vertical_bounds(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "free"
        visual.finder_mode = False
        visual.expanded = True
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.stack_direction = 1
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 720
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._standalone_width_limit_for_global_center = lambda _center, _screen_geo, target_width=None: float(target_width or 420.0)
        visual._main_size_for_kind = lambda _kind, _stack_w: (420.0, 48.0)
        visual._main_top_for_direction = lambda _kind, _main_h, direction: 72.0 if direction >= 0 else 600.0
        visual._standalone_bottom_boundary_extension_for_screen = lambda _screen: 0.0
        visual._bottom_taskbar_rect_for_screen = lambda _screen: QRectF()
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: False
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        anchor = QRectF(710.0, 1070.0, 420.0, 48.0)
        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            visual._settle_standalone_drag_release(anchor, QPoint(920, 1078))

        self.assertTrue(moves)
        main_bottom = moves[-1].y() + 600.0 + 48.0
        self.assertLessEqual(main_bottom, 1080.0 - visual._u(6.0))

    def test_bottom_drag_release_snaps_above_taskbar_when_autohide_enabled_without_entering_tasker(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "free"
        visual.finder_mode = False
        visual.expanded = True
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.stack_direction = 1
        visual.taskbar_above_follow_active = False
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 720
        visual._taskbar_allowed = lambda: True
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, taskbar: QRectF(taskbar)
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: {"auto_hide": True, "rect": QRectF(0.0, 1040.0, 1920.0, 40.0), "edge": "bottom"}
        visual._windows_taskbar_rect_for_screen = lambda _screen: None
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: False
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: True
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._standalone_width_limit_for_global_center = lambda _center, _screen_geo, target_width=None: float(target_width or 420.0)
        visual._main_size_for_kind = lambda _kind, _stack_w: (420.0, 48.0)
        visual._main_top_for_direction = lambda _kind, _main_h, direction: 72.0 if direction >= 0 else 600.0
        visual._expanded_stack_height = lambda _kind, _main_h, include_popups=False: 120.0
        visual.save_last_position = lambda _pos: None
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        anchor = QRectF(710.0, 1050.0, 420.0, 48.0)
        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._settle_standalone_drag_release(anchor, QPoint(920, 1078)))

        self.assertEqual(visual.location_mode, "stick")
        self.assertTrue(visual.taskbar_above_follow_active)
        self.assertTrue(moves)
        main_bottom = moves[-1].y() + 600.0 + 48.0
        self.assertAlmostEqual(main_bottom, 1040.0 - visual._stick_taskbar_above_gap(QRectF(0.0, 1040.0, 1920.0, 40.0)), delta=1.0)

    def test_stick_drag_release_away_from_bottom_returns_to_free(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "stick"
        visual.finder_mode = False
        visual.expanded = True
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.stack_direction = -1
        visual.taskbar_above_follow_active = True
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 720
        visual._taskbar_allowed = lambda: True
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, taskbar: QRectF(taskbar)
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: True
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._standalone_width_limit_for_global_center = lambda _center, _screen_geo, target_width=None: float(target_width or 420.0)
        visual._main_size_for_kind = lambda _kind, _stack_w: (420.0, 48.0)
        visual._main_top_for_direction = lambda _kind, _main_h, direction: 72.0 if direction >= 0 else 600.0
        visual._expanded_stack_height = lambda _kind, _main_h, include_popups=False: 120.0
        visual.save_last_position = lambda _pos: None
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        anchor = QRectF(710.0, 600.0, 420.0, 48.0)
        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._settle_standalone_drag_release(anchor, QPoint(920, 640)))

        self.assertEqual(visual.location_mode, "free")
        self.assertFalse(visual.taskbar_above_follow_active)
        self.assertTrue(moves)

    def test_top_drag_release_snaps_to_screen_top_without_mydockfinder(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "free"
        visual.finder_mode = False
        visual.attach_to_real_finder = False
        visual.mydockfinder_running = False
        visual.manual_finder_available = False
        visual.expanded = False
        visual.stack_direction = 1
        visual.taskbar_above_follow_active = False
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._standalone_width_limit_for_global_center = lambda _center, _screen_geo, target_width=None: float(target_width or 420.0)
        visual._main_size_for_kind = lambda _kind, _stack_w: (420.0, 48.0)
        visual._main_top_for_direction = lambda _kind, _main_h, direction: 72.0 if direction >= 0 else 190.0
        visual._hide_taskbar_drop_overlay = lambda: None
        visual._force_super_topmost = lambda: None
        visual._restore_motion_global_rects_after_move = lambda: None
        visual.save_last_position = lambda _pos: None
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        anchor = QRectF(400.0, 6.0, 420.0, 48.0)
        self.assertTrue(visual._should_snap_top_notch_for_release(anchor, QPoint(610, 2), FakeScreen()))
        self.assertTrue(visual._settle_standalone_top_notch(anchor, FakeScreen()))

        self.assertEqual(visual.location_mode, "top")
        self.assertFalse(visual.taskbar_above_follow_active)
        self.assertTrue(moves)
        self.assertAlmostEqual(moves[-1].y() + 72.0, 0.0, delta=1.0)

    def test_top_drag_release_is_disabled_when_mydockfinder_is_available(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.mydockfinder_running = True
        visual.manual_finder_available = False
        visual._u = lambda value: float(value)

        anchor = QRectF(400.0, 6.0, 420.0, 48.0)

        self.assertFalse(visual._should_snap_top_notch_for_release(anchor, QPoint(610, 2), FakeScreen()))

    def test_bottom_drag_release_enters_tasker_when_autohide_disabled(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "free"
        visual.finder_mode = False
        visual.expanded = True
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.stack_direction = 1
        visual.taskbar_above_follow_active = False
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 720
        visual._taskbar_allowed = lambda: True
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, taskbar: QRectF(taskbar)
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: False
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: False
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._standalone_width_limit_for_global_center = lambda _center, _screen_geo, target_width=None: float(target_width or 420.0)
        visual._main_size_for_kind = lambda _kind, _stack_w: (420.0, 48.0)
        visual._main_top_for_direction = lambda _kind, _main_h, direction: 72.0 if direction >= 0 else 600.0
        visual.set_location_mode = lambda mode, notify=True: setattr(visual, "location_mode", str(mode))
        dock_calls: list[str] = []
        visual._dock_to_taskbar_task_area = lambda *args, **kwargs: dock_calls.append("dock") or True
        visual.save_last_position = lambda _pos: None
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        anchor = QRectF(710.0, 1050.0, 420.0, 48.0)
        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._settle_standalone_drag_release(anchor, QPoint(920, 1078)))

        self.assertEqual(visual.location_mode, "tasker")
        self.assertFalse(visual.taskbar_above_follow_active)
        self.assertEqual(dock_calls, ["dock"])

    def test_bottom_drag_release_enters_tasker_at_release_horizontal_position_when_autohide_disabled(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "free"
        visual.finder_mode = False
        visual.expanded = True
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.stack_direction = 1
        visual.taskbar_above_follow_active = False
        visual.taskbar_position_ratio = 0.5
        visual.taskbar_dock_animation_requested = False
        visual.taskbar_follow_force_visible_until = 0.0
        visual.taskbar_autohide_visible_until = 0.0
        visual.last_taskbar_rect = QRectF()
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 720
        visual.mapToGlobal = lambda _point: QPoint(960, 1080)
        visual._taskbar_allowed = lambda: True
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, taskbar: QRectF(taskbar)
        visual._taskbar_follow_rect_for_visibility = lambda _screen, taskbar, force_visible=False: QRectF(taskbar)
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: False
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: False
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_taskbar_anchor = lambda _kind, _stack_w, _taskbar: (420.0, 40.0)
        visual._main_top_for_direction = lambda _kind, _main_h, direction: 600.0 if direction < 0 else 72.0
        visual.set_location_mode = lambda mode, notify=True: setattr(visual, "location_mode", str(mode))
        visual._force_super_topmost = lambda: None
        visual.save_last_position = lambda _pos: None
        moves: list[QPoint] = []
        visual._move_taskbar_window_if_needed = lambda pos: moves.append(QPoint(pos)) or True

        anchor = QRectF(200.0, 1050.0, 420.0, 48.0)
        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._settle_standalone_drag_release(anchor, QPoint(410, 1078)))

        expected_ratio = visual._taskbar_position_ratio_for_center(QRectF(0.0, 1040.0, 1920.0, 40.0), 420.0, anchor.center().x())
        self.assertEqual(visual.location_mode, "tasker")
        self.assertAlmostEqual(visual.taskbar_position_ratio, expected_ratio)
        self.assertTrue(moves)
        self.assertAlmostEqual(moves[-1].x() + visual.width() / 2.0, anchor.center().x(), delta=1.0)

    def test_disabling_taskbar_autohide_clears_above_taskbar_follow_runtime_state(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_auto_hide_mode = "enabled"
        visual.taskbar_above_follow_active = True
        visual.taskbar_preview_visible = True
        visual.taskbar_selected_drop_zone = QRectF(100.0, 1040.0, 420.0, 40.0)
        visual.taskbar_attach_hot_zone = QRectF(760.0, 450.0, 400.0, 160.0)
        visual.taskbar_attach_hot_zone_host_rect = QRectF(0.0, 0.0, 1920.0, 1080.0)
        visual.taskbar_attach_hot_zone_active = True
        visual.taskbar_drop_zones = [QRectF(100.0, 1040.0, 420.0, 40.0)]
        visual.taskbar_follow_last_at = 20.0
        visual.taskbar_windows_snapshot_cache = {("screen",): {"sampled_at": 20.0}}
        visual._notify_settings_changed = lambda: None
        visual.update = lambda: None

        visual.set_taskbar_auto_hide_mode("disabled", notify=False)

        self.assertEqual(visual.taskbar_auto_hide_mode, "disabled")
        self.assertEqual(visual.location_mode, "tasker")
        self.assertFalse(visual.taskbar_above_follow_active)
        self.assertFalse(visual.taskbar_preview_visible)
        self.assertTrue(visual.taskbar_selected_drop_zone.isNull())
        self.assertTrue(visual.taskbar_attach_hot_zone.isNull())
        self.assertTrue(visual.taskbar_attach_hot_zone_host_rect.isNull())
        self.assertFalse(visual.taskbar_attach_hot_zone_active)
        self.assertEqual(visual.taskbar_drop_zones, [])

    def test_release_rechecks_hot_zone_before_accepting_taskbar_drop(self):
        class FakeRelease:
            def button(self):
                return Qt.MouseButton.LeftButton

            def globalPosition(self):
                return QPointF(920.0, 1078.0)

            def position(self):
                return QPointF(470.0, 620.0)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = False
        visual.drag_candidate = True
        visual.dragging = True
        visual.drag_auto_stack_direction = -1
        visual.drag_press_key = "music"
        visual.location_mode = "free"
        visual.finder_mode = False
        visual.taskbar_preview_visible = True
        visual.taskbar_attach_hot_zone_active = True
        visual.taskbar_attach_hot_zone = QRectF(760.0, 450.0, 400.0, 160.0)
        visual.taskbar_attach_hot_zone_host_rect = QRectF(0.0, 0.0, 1920.0, 1080.0)
        visual.taskbar_drop_zones = []
        visual.taskbar_selected_drop_zone = QRectF()
        visual._taskbar_allowed = lambda: True
        visual.set_location_mode = lambda mode, notify=True: setattr(visual, "location_mode", str(mode))
        visual._current_main_target_global_rect = lambda: QRectF(710.0, 1050.0, 420.0, 48.0)
        visual._update_taskbar_preview_for_drag = lambda _pos: setattr(visual, "taskbar_attach_hot_zone_active", False)
        visual._hide_taskbar_drop_overlay = lambda: None
        visual._save_placement_state = lambda: None
        dock_calls: list[str] = []
        visual._dock_to_taskbar_task_area = lambda: dock_calls.append("dock") or True
        settle_calls: list[tuple[QRectF, QPointF]] = []
        visual._settle_standalone_drag_release = lambda anchor, pos: settle_calls.append((QRectF(anchor), QPointF(pos))) or True

        visual.mouseReleaseEvent(FakeRelease())

        self.assertEqual(dock_calls, [])
        self.assertTrue(settle_calls)
        self.assertFalse(visual.taskbar_attach_hot_zone_active)

    def test_tasker_bottom_edge_release_keeps_tasker_when_still_on_track(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        class FakeRelease:
            def button(self):
                return Qt.MouseButton.LeftButton

            def globalPosition(self):
                return QPointF(920.0, 1078.0)

            def position(self):
                return QPointF(470.0, 620.0)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = False
        visual.drag_candidate = True
        visual.dragging = True
        visual.drag_auto_stack_direction = -1
        visual.drag_press_key = "music"
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.expanded = True
        visual.auto_expand_bounds = True
        visual.expand_direction = "auto"
        visual.stack_direction = -1
        visual.taskbar_above_follow_active = False
        visual.taskbar_preview_visible = True
        visual.taskbar_attach_hot_zone_active = False
        visual.taskbar_attach_hot_zone = QRectF()
        visual.taskbar_attach_hot_zone_host_rect = QRectF()
        visual.taskbar_drop_zones = []
        visual.taskbar_selected_drop_zone = QRectF()
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 720
        visual._taskbar_allowed = lambda: True
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, taskbar: QRectF(taskbar)
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: False
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: False
        visual._current_main_target_global_rect = lambda: QRectF(710.0, 1050.0, 420.0, 48.0)
        visual._update_taskbar_preview_for_drag = lambda _pos: setattr(visual, "taskbar_attach_hot_zone_active", False)
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._standalone_width_limit_for_global_center = lambda _center, _screen_geo, target_width=None: float(target_width or 420.0)
        visual._main_size_for_kind = lambda _kind, _stack_w: (420.0, 48.0)
        visual._main_top_for_direction = lambda _kind, _main_h, direction: 72.0 if direction >= 0 else 600.0
        visual.set_location_mode = lambda mode, notify=True: setattr(visual, "location_mode", str(mode))
        visual._hide_taskbar_drop_overlay = lambda: None
        visual._save_placement_state = lambda: None
        visual.save_last_position = lambda _pos: None
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))
        settle_calls: list[str] = []
        original_settle = visual._settle_standalone_drag_release
        visual._settle_standalone_drag_release = lambda *_args, **_kwargs: settle_calls.append("settle") or original_settle(*_args, **_kwargs)

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            visual.mouseReleaseEvent(FakeRelease())

        self.assertEqual(visual.location_mode, "tasker")
        self.assertFalse(visual.taskbar_above_follow_active)
        self.assertEqual(moves, [])
        self.assertEqual(settle_calls, [])

    def test_tasker_drag_to_bottom_edge_moves_along_taskbar_track(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        class FakeMove:
            def globalPosition(self):
                return QPointF(920.0, 1078.0)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.drag_candidate = True
        visual.dragging = False
        visual.drag_threshold = 5.0
        visual.drag_press_global = QPoint(920, 1058)
        visual.drag_press_key = "music"
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual._u = lambda value: float(value)
        visual._should_attach_to_finder = lambda *_args: False
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: False
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: False
        visual.set_location_mode = lambda mode, notify=True: setattr(visual, "location_mode", str(mode))
        standalone_calls: list[QPoint] = []
        visual._move_standalone_drag = lambda pos: standalone_calls.append(QPoint(pos))
        preview_calls: list[QPoint] = []
        visual._update_taskbar_preview_for_drag = lambda pos: preview_calls.append(QPoint(pos))
        taskbar_drag_calls: list[QPoint] = []
        visual._move_taskbar_drag = lambda pos: taskbar_drag_calls.append(QPoint(pos))
        visual._pulse_stack = lambda: None

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            visual._update_drag(FakeMove())

        self.assertEqual(visual.location_mode, "tasker")
        self.assertEqual(standalone_calls, [])
        self.assertEqual(preview_calls, [])
        self.assertEqual(taskbar_drag_calls, [QPoint(920, 1078)])

    def test_tasker_main_drag_detaches_to_free_on_clear_upward_pull(self):
        class FakeMove:
            def globalPosition(self):
                return QPointF(920.0, 900.0)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.drag_candidate = True
        visual.dragging = False
        visual.drag_threshold = 5.0
        visual.drag_press_global = QPoint(920, 1058)
        visual.drag_press_key = "music"
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual._u = lambda value: float(value)
        visual._should_attach_to_finder = lambda *_args: False
        visual._taskbar_drag_should_stay_on_track = lambda _pos: False
        visual.set_location_mode = lambda mode, notify=True: setattr(visual, "location_mode", str(mode))
        standalone_calls: list[QPoint] = []
        visual._move_standalone_drag = lambda pos: standalone_calls.append(QPoint(pos))
        preview_calls: list[QPoint] = []
        visual._update_taskbar_preview_for_drag = lambda pos: preview_calls.append(QPoint(pos))
        taskbar_drag_calls: list[QPoint] = []
        visual._move_taskbar_drag = lambda pos: taskbar_drag_calls.append(QPoint(pos))
        visual._pulse_stack = lambda: None

        visual._update_drag(FakeMove())

        self.assertEqual(visual.location_mode, "free")
        self.assertEqual(standalone_calls, [QPoint(920, 900)])
        self.assertEqual(preview_calls, [QPoint(920, 900)])
        self.assertEqual(taskbar_drag_calls, [])

    def test_tasker_main_horizontal_drag_keeps_taskbar_mode_outside_track_band(self):
        class FakeMove:
            def globalPosition(self):
                return QPointF(1180.0, 992.0)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.drag_candidate = True
        visual.dragging = False
        visual.drag_threshold = 5.0
        visual.drag_press_global = QPoint(920, 1058)
        visual.drag_press_key = "music"
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual._u = lambda value: float(value)
        visual._should_attach_to_finder = lambda *_args: False
        visual._taskbar_drag_should_stay_on_track = lambda _pos: False
        visual.set_location_mode = lambda mode, notify=True: setattr(visual, "location_mode", str(mode))
        standalone_calls: list[QPoint] = []
        visual._move_standalone_drag = lambda pos: standalone_calls.append(QPoint(pos))
        preview_calls: list[QPoint] = []
        visual._update_taskbar_preview_for_drag = lambda pos: preview_calls.append(QPoint(pos))
        taskbar_drag_calls: list[QPoint] = []
        visual._move_taskbar_drag = lambda pos: taskbar_drag_calls.append(QPoint(pos))
        visual._pulse_stack = lambda: None

        visual._update_drag(FakeMove())

        self.assertEqual(visual.location_mode, "tasker")
        self.assertEqual(standalone_calls, [])
        self.assertEqual(preview_calls, [])
        self.assertEqual(taskbar_drag_calls, [QPoint(1180, 992)])

    def test_tasker_drag_stays_on_full_track_when_autohide_shell_is_thin(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.last_taskbar_rect = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1078.0, 1920.0, 2.0)
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: True
        visual._taskbar_hidden_fallback_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._taskbar_drag_should_stay_on_track(QPoint(920, 1056)))

    def test_tasker_drag_move_preserves_normal_height_when_autohide_shell_is_thin(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.expanded = False
        visual.taskbar_position_ratio = 0.5
        visual.taskbar_manual_width = 0.0
        visual.drag_press_main_offset = QPointF(210.0, 17.0)
        visual.last_taskbar_rect = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1078.0, 1920.0, 2.0)
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: True
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: True
        visual._taskbar_hidden_fallback_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_drag_track_rect_for_screen = lambda _screen, taskbar: QRectF(taskbar)
        visual._taskbar_stack_direction_for_rect = lambda taskbar, _geo: -1 if QRectF(taskbar).height() >= 30.0 else 1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_top_for_direction = lambda _kind, _main_h, direction: 600.0 if direction < 0 else 72.0
        visual._move_taskbar_window_if_needed = lambda _pos: True
        visual._force_super_topmost = lambda: None
        visual.save_last_position = lambda _pos: None
        visual._update_taskbar_material_for_screen = lambda *_args: None

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            visual._move_taskbar_drag(QPoint(920, 1056))

        self.assertEqual(visual.last_taskbar_rect, QRectF(0.0, 1040.0, 1920.0, 40.0))
        self.assertAlmostEqual(visual.taskbar_selected_drop_zone.height(), 40.0)

    def test_tasker_drag_ignores_stale_track_from_other_screen(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.last_taskbar_rect = QRectF(2200.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1078.0, 1920.0, 2.0)
        visual._taskbar_hidden_fallback_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._taskbar_drag_should_stay_on_track(QPoint(920, 1056)))

    def test_tasker_track_release_keeps_tasker_when_not_bottom_edge(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        class FakeRelease:
            def button(self):
                return Qt.MouseButton.LeftButton

            def globalPosition(self):
                return QPointF(920.0, 1060.0)

            def position(self):
                return QPointF(470.0, 620.0)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scene_suppressed = False
        visual.drag_candidate = True
        visual.dragging = True
        visual.drag_auto_stack_direction = -1
        visual.drag_press_key = "music"
        visual.location_mode = "tasker"
        visual.finder_mode = False
        visual.taskbar_above_follow_active = False
        visual.taskbar_preview_visible = True
        visual.taskbar_attach_hot_zone_active = False
        visual.taskbar_attach_hot_zone = QRectF()
        visual.taskbar_attach_hot_zone_host_rect = QRectF()
        visual.taskbar_drop_zones = []
        visual.taskbar_selected_drop_zone = QRectF()
        visual._u = lambda value: float(value)
        visual._taskbar_allowed = lambda: True
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: False
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: False
        visual._current_main_target_global_rect = lambda: QRectF(710.0, 1046.0, 420.0, 34.0)
        visual._update_taskbar_preview_for_drag = lambda _pos: setattr(visual, "taskbar_attach_hot_zone_active", False)
        visual.set_location_mode = lambda mode, notify=True: setattr(visual, "location_mode", str(mode))
        visual._hide_taskbar_drop_overlay = lambda: None
        visual._save_placement_state = lambda: None
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            visual.mouseReleaseEvent(FakeRelease())

        self.assertEqual(visual.location_mode, "tasker")
        self.assertFalse(visual.taskbar_above_follow_active)
        self.assertEqual(moves, [])

    def test_taskbar_above_gap_scales_with_taskbar_height(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)

        self.assertAlmostEqual(visual._taskbar_above_gap(QRectF(0.0, 1040.0, 1920.0, 40.0)), 4.0, delta=0.1)
        self.assertGreaterEqual(visual._taskbar_above_gap(QRectF(0.0, 1000.0, 1920.0, 80.0)), 6.0)

    def test_taskbar_above_gap_uses_screen_pixels_not_visual_scale(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertAlmostEqual(visual._taskbar_above_gap(QRectF(0.0, 1040.0, 1920.0, 40.0)), 4.0, delta=0.1)

    def test_stick_above_gap_uses_visible_taskbar_clearance(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)

        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        self.assertAlmostEqual(visual._stick_taskbar_above_gap(taskbar), visual._taskbar_above_gap(taskbar) + 4.0, delta=0.1)

    def test_stick_hidden_autohide_gap_stays_flush_with_screen_edge(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)

        self.assertAlmostEqual(
            visual._stick_taskbar_above_gap(QRectF(0.0, 1080.0, 1920.0, 40.0), hidden_autohide=True),
            0.0,
            delta=0.1,
        )

    def test_stick_follow_target_never_rounds_into_visible_taskbar(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "stick"
        visual.finder_mode = False
        visual.taskbar_above_follow_active = True
        visual.taskbar_above_position_ratio = 0.5
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 720
        visual._taskbar_follow_rect_for_current_visibility = lambda _screen, taskbar: (QRectF(taskbar), False)
        visual._taskbar_track_rect_for_screen = lambda _screen, taskbar: QRectF(taskbar)
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: True
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._standalone_width_limit_for_global_center = lambda _center, _screen_geo, target_width=None: float(target_width or 420.0)
        visual._main_size_for_kind = lambda _kind, _stack_w: (420.0, 39.1)
        visual._main_top_for_direction = lambda _kind, _main_h, direction: 600.2
        visual._hide_taskbar_drop_overlay = lambda: None
        visual._force_super_topmost = lambda: None
        visual._restore_motion_global_rects_after_move = lambda: None
        visual.save_last_position = lambda _pos: None
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        anchor = QRectF(710.0, 1000.0, 420.0, 39.1)
        self.assertTrue(visual._settle_standalone_above_taskbar(anchor, FakeScreen(), taskbar))

        self.assertTrue(moves)
        main_bottom = float(moves[-1].y()) + 600.2 + 39.1
        self.assertLessEqual(main_bottom, taskbar.top() - visual._stick_taskbar_above_gap(taskbar))

    def test_tasker_autohide_visible_follow_lifts_bottom_taskbar_for_visual_centering(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "tasker"
        visual.taskbar_position_ratio = 0.5
        visual.taskbar_manual_width = 0.0
        visual.taskbar_follow_last_key = ""
        visual.taskbar_follow_material_last_at = 0.0
        visual.taskbar_follow_last_hidden = True
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 260
        visual.mapToGlobal = lambda _point: QPoint(960, 1060)
        appbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(appbar)
        visual._taskbar_drag_track_rect_for_screen = lambda _screen, _taskbar: QRectF(appbar)
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: True
        visual._taskbar_follow_rect_for_current_visibility = lambda _screen, _taskbar, force_visible=False: (QRectF(appbar), False)
        visual._update_taskbar_material_for_screen = lambda *_args: None
        visual._taskbar_stack_direction_for_rect = lambda *_args: -1
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._main_size_for_taskbar_anchor = lambda _kind, _stack_w, _taskbar: (300.0, 40.0)
        visual._main_top_for_direction = lambda *_args: 10.0
        visual.save_last_position = lambda _pos: None
        visual._set_taskbar_follow_hidden = lambda _hidden: None
        visual._force_super_topmost = lambda: None
        moves: list[QPoint] = []
        visual._animate_taskbar_follow_to_pos = lambda pos: moves.append(QPoint(pos)) or True

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._refresh_taskbar_follow_position())

        self.assertTrue(moves)
        expected_lift = visual._taskbar_autohide_visible_follow_lift(appbar, QRect(0, 0, 1920, 1080))
        self.assertAlmostEqual(visual.taskbar_selected_drop_zone.center().y(), appbar.center().y() - expected_lift)
        self.assertAlmostEqual(moves[-1].y() + 10.0, appbar.top() - expected_lift, delta=1.0)

    def test_above_taskbar_follow_repositions_when_taskbar_top_changes(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "stick"
        visual.finder_mode = False
        visual.taskbar_above_follow_active = True
        visual.taskbar_above_position_ratio = 0.5
        visual.stack_direction = -1
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 720
        visual.mapToGlobal = lambda _point: QPoint(960, 1036)
        visual._taskbar_rect_for_screen = lambda _screen: QRectF(0.0, 1000.0, 1920.0, 80.0)
        visual._taskbar_track_rect_for_screen = lambda _screen, taskbar: QRectF(taskbar)
        visual._windows_taskbar_appbar_state_for_screen = lambda _screen: {"auto_hide": True, "rect": QRectF(0.0, 1000.0, 1920.0, 80.0), "edge": "bottom"}
        visual._windows_taskbar_rect_for_screen = lambda _screen: None
        visual._taskbar_autohide_hidden_for_screen = lambda *_args: False
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: True
        visual._current_main_target_global_rect = lambda: QRectF(710.0, 940.0, 420.0, 48.0)
        visual._current_main_global_rect = lambda: QRectF()
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._standalone_width_limit_for_global_center = lambda _center, _screen_geo, target_width=None: float(target_width or 420.0)
        visual._main_size_for_kind = lambda _kind, _stack_w: (420.0, 48.0)
        visual._main_top_for_direction = lambda _kind, _main_h, direction: 72.0 if direction >= 0 else 600.0
        visual.save_last_position = lambda _pos: None
        moves: list[QPoint] = []
        visual.move = lambda pos: moves.append(QPoint(pos))

        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=FakeScreen()):
            self.assertTrue(visual._refresh_taskbar_above_follow_position())

        self.assertTrue(moves)
        self.assertAlmostEqual(moves[-1].y() + 600.0 + 48.0, 1000.0 - visual._stick_taskbar_above_gap(QRectF(0.0, 1000.0, 1920.0, 80.0)), delta=1.0)
        self.assertEqual(visual.location_mode, "stick")

    def test_stick_hidden_autohide_follow_keeps_gap_above_screen_bottom(self):
        class FakeScreen:
            def geometry(self):
                return QRect(0, 0, 1920, 1080)

        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.location_mode = "stick"
        visual.finder_mode = False
        visual.taskbar_above_follow_active = True
        visual.taskbar_above_position_ratio = 0.5
        visual.stack_direction = -1
        visual._u = lambda value: float(value)
        visual.width = lambda: 940
        visual.height = lambda: 720
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        hidden_follow = QRectF(0.0, 1080.0, 1920.0, 40.0)
        visual._taskbar_follow_rect_for_current_visibility = lambda _screen, _taskbar: (QRectF(hidden_follow), True)
        visual._taskbar_track_rect_for_screen = lambda _screen, _taskbar: QRectF(taskbar)
        visual._taskbar_auto_hide_enabled_for_screen = lambda _screen: True
        visual._taskbar_above_gap = lambda _taskbar=None: 4.0
        visual._stick_taskbar_above_gap = lambda _taskbar=None, hidden_autohide=False: 0.0
        visual._current_main_target_global_rect = lambda: QRectF(710.0, 1032.0, 420.0, 48.0)
        visual._main_kind = lambda: IslandKind.MUSIC
        visual._stack_target_width = lambda _kind: 420.0
        visual._standalone_width_limit_for_global_center = lambda _center, _screen_geo, target_width=None: float(target_width or 420.0)
        visual._main_size_for_kind = lambda _kind, _stack_w: (420.0, 48.0)
        visual._main_top_for_direction = lambda _kind, _main_h, direction: 72.0 if direction >= 0 else 600.0
        visual._taskbar_bottom_boundary_extension_for_screen = lambda _screen, _taskbar: 40.0
        visual._hide_taskbar_drop_overlay = lambda: None
        visual._force_super_topmost = lambda: None
        visual._restore_motion_global_rects_after_move = lambda: None
        visual.save_last_position = lambda _pos: None
        moves: list[QPoint] = []
        visual._animate_taskbar_follow_to_pos = lambda pos: moves.append(QPoint(pos)) or True

        self.assertTrue(visual._settle_standalone_above_taskbar(QRectF(710.0, 1032.0, 420.0, 48.0), FakeScreen(), taskbar))

        self.assertTrue(moves)
        self.assertAlmostEqual(moves[-1].y() + 600.0 + 48.0, hidden_follow.top(), delta=1.0)

    def test_bottom_drag_clamp_extends_down_for_autohide_taskbar_space(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        screen_geo = QRect(0, 0, 1920, 1080)
        main_left = 260.0
        main_top = 600.0
        main_w = 420.0
        main_h = 48.0

        clamped = visual._clamp_standalone_window_pos(
            500.0,
            9999.0,
            main_left,
            main_top,
            main_w,
            main_h,
            screen_geo,
            bottom_extension=40.0,
        )

        self.assertAlmostEqual(clamped.y() + main_top + main_h, screen_geo.bottom() + 40.0 - visual._u(6.0), delta=1.0)

    def test_plugin_alert_click_expands_and_focuses_plugin(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 3
        visual.plugin_focus = 0
        visual.plugin_focus_from = 0
        visual.plugin_focus_target = 0
        visual.plugin_focus_changed_at = 0.0
        visual.plugin_samples = [
            ("TIME", "20:00", "05/26", QColor(67, 162, 255), "◷", "time", "normal"),
            ("WEATHER", "26°C 晴", "北京", QColor(80, 210, 255), "☁", "weather", "normal"),
            ("AGENDA", "现在", "开会", QColor(118, 236, 160), "□", "agenda", "due"),
        ]
        visual._set_expanded = lambda value: setattr(visual, "expanded", bool(value))
        visual._pulse_stack = lambda: None

        visual._activate_key("plugin-alert:agenda")

        self.assertTrue(visual.expanded)
        self.assertEqual(visual.plugin_focus_target, 2)

    def test_expanded_plugin_keeps_time_status_inside_time_panel(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = True
        visual.plugin_panel_open = True
        visual.enabled_plugin_count = 3
        visual.plugin_focus = 0
        visual.plugin_focus_from = 0
        visual.plugin_focus_target = 0
        visual.plugin_focus_changed_at = 0.0
        visual.music_playing = False
        visual.messages = []
        visual.finder_mode = False
        visual.width = lambda: 900
        visual.height = lambda: 420
        visual._current_screen_geometry = lambda: None
        visual._stack_target_width = lambda _kind: 420.0
        visual._standalone_width_limit = lambda _center_x, _screen: 420.0
        visual.mapToGlobal = lambda point: point
        visual.plugin_samples = [
            ("TIME", "00:24", "Countdown 0:24", QColor(67, 162, 255), "●", "time", "running", "Timer running", "Countdown 0:24", 30, 0.4),
            ("WEATHER", "26°C 晴", "北京", QColor(80, 210, 255), "☀", "weather", "normal", "", "", 50, 0.0),
            ("AGENDA", "暂无日程", "JSON / ICS", QColor(118, 236, 160), "■", "agenda", "normal", "", "", 50, 0.0),
        ]

        visuals = visual._build_visuals()

        self.assertEqual([item.key for item in visuals], ["plugin", "plugin-panel"])
        self.assertEqual(visual._plugin_panel_alert_progress(0), 1.0)

    def test_plugin_ring_style_is_thick_for_animation_first_language(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        style = visual._plugin_ring_style("running")

        self.assertGreaterEqual(style["width"], 3.4 * IslandStackVisual.VISUAL_SCALE)
        self.assertGreater(style["pulse"], 0.0)

    def test_plugin_function_lines_include_alert_context(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        _title, lines = visual._plugin_function_lines("TIME", "00:24", "Countdown 0:24", "running", "Timer running", "Countdown 0:24")

        self.assertIn("Countdown 0:24", lines)

    def test_plugin_action_model_time_weather_agenda(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        time_action = visual._plugin_action_model("TIME", "00:24", "倒计时 0:24", "running")
        weather_action = visual._plugin_action_model("WEATHER", "26°C 晴", "北京 · 东南风", "normal")
        agenda_action = visual._plugin_action_model("AGENDA", "4 分钟后", "准备上课", "soon")

        self.assertEqual(time_action["command"], "timer-clear")
        self.assertIn("清除", time_action["label"])
        self.assertEqual(weather_action["command"], "weather-refresh")
        self.assertIn("刷新", weather_action["label"])
        self.assertEqual(agenda_action["command"], "agenda-open")
        self.assertIn("日程", agenda_action["label"])

    def test_plugin_panel_layout_has_nav_body_action_and_reminder_rail(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        rect = QRectF(0.0, 0.0, 420.0, 188.0)

        layout = visual._plugin_panel_layout(rect)

        self.assertGreaterEqual(layout["nav"].height(), 30.0)
        self.assertGreater(layout["body"].top(), layout["nav"].bottom())
        self.assertGreater(layout["action"].top(), layout["body"].top())
        self.assertGreater(layout["reminders"].top(), layout["nav"].bottom())
        self.assertLessEqual(layout["action"].right(), rect.right())

    def test_time_plugin_panel_has_clock_countdown_and_alarm_sections(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        sections = visual._plugin_panel_sections(
            {
                "label": "TIME",
                "detail": "19:04:12",
                "summary": "05/27 Wed",
                "status": "idle",
                "alert_title": "",
                "alert_body": "",
            }
        )
        layout = visual._plugin_panel_layout(QRectF(0.0, 0.0, 420.0, 188.0))

        self.assertEqual([section["id"] for section in sections], ["clock", "countdown", "alarm"])
        self.assertIn("倒计时", sections[1]["title"])
        self.assertIn("闹钟", sections[2]["title"])
        for key in ("time_clock", "time_countdown", "time_alarm"):
            self.assertIn(key, layout)
            self.assertGreater(layout[key].width(), 48.0)

    def test_time_plugin_panel_model_defaults_to_three_reference_modules(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        model = visual._time_plugin_panel_model(
            {
                "label": "TIME",
                "detail": "22:52",
                "summary": "05/27 Wed",
                "status": "idle",
                "progress": 0.0,
            }
        )

        self.assertEqual(model["mode"], "dashboard")
        self.assertEqual(model["clock"]["location"], "中国 北京")
        self.assertEqual([item["minutes"] for item in model["countdown_presets"]], [5, 15, 10, 20])
        self.assertFalse(model["countdown_active"]["active"])
        self.assertEqual(model["alarms"], [])

    def test_time_plugin_running_model_uses_center_ring(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        model = visual._time_plugin_panel_model(
            {
                "label": "TIME",
                "detail": "22:52",
                "summary": "倒计时 4:59",
                "status": "running",
                "progress": 0.45,
                "alert_body": "倒计时 4:59",
            }
        )

        self.assertTrue(model["countdown_active"]["active"])
        self.assertEqual(model["countdown_active"]["text"], "4:59")
        self.assertAlmostEqual(model["countdown_active"]["progress"], 0.45)

    def test_time_reference_palette_translates_solaris_tokens(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._system_dark_mode = lambda: True

        palette = visual._time_reference_palette(visual._plugin_theme_colors(), QColor(255, 204, 0))

        self.assertEqual(palette["surface"].getRgb()[:3], (19, 19, 19))
        self.assertEqual(palette["module"].getRgb()[:3], (28, 27, 27))
        self.assertEqual(palette["handle"].getRgb()[:3], (42, 42, 42))
        self.assertEqual(palette["accent"].getRgb()[:3], (255, 184, 0))
        self.assertEqual(palette["active"].getRgb()[:3], (2, 201, 83))
        self.assertEqual(palette["text"].getRgb()[:3], (229, 226, 225))

    def test_time_reference_palette_uses_light_alarm_panel_colors(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._system_dark_mode = lambda: False

        palette = visual._time_reference_palette(visual._plugin_theme_colors(), QColor(255, 204, 0))

        self.assertGreater(palette["surface"].red(), 220)
        self.assertGreater(palette["module"].red(), 245)
        self.assertLess(palette["text"].red(), 60)
        self.assertGreater(palette["divider"].alpha(), 120)

    def test_time_countdown_presets_paint_as_two_by_two_grid(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_time_mode = "dashboard"
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_structured_plugin_panel(
            painter,
            QRectF(20.0, 20.0, 420.0, 188.0),
            {
                "label": "TIME",
                "detail": "22:52",
                "summary": "05/27 Wed",
                "status": "idle",
                "color": QColor(255, 204, 0),
                "progress": 0.0,
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        rect5 = visual.plugin_action_rects["timer-start-5"]
        rect15 = visual.plugin_action_rects["timer-start-15"]
        rect10 = visual.plugin_action_rects["timer-start-10"]
        rect20 = visual.plugin_action_rects["timer-start-20"]
        self.assertAlmostEqual(rect5.top(), rect15.top(), delta=2.0)
        self.assertAlmostEqual(rect10.top(), rect20.top(), delta=2.0)
        self.assertGreater(rect10.top(), rect5.top() + 8.0)

    def test_time_countdown_presets_use_reference_pill_ratio(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        image = QImage(280, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_countdown_module(
            painter,
            QRectF(20.0, 20.0, 230.0, 100.0),
            {
                "countdown_active": {"active": False, "text": "", "progress": 0.0, "status": "idle"},
                "countdown_presets": [
                    {"minutes": 5, "label": "5"},
                    {"minutes": 15, "label": "15"},
                    {"minutes": 10, "label": "10"},
                    {"minutes": 20, "label": "20"},
                ],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        button = visual.plugin_action_rects["timer-start-5"]
        self.assertGreater(button.width(), button.height() * 1.8)

    def test_time_countdown_double_layout_presets_are_full_capsules_with_minutes(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        texts = []
        visual._paint_static_elided_text = lambda _painter, _rect, text, *_args, **_kwargs: texts.append(str(text))
        image = QImage(300, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_countdown_module(
            painter,
            QRectF(20.0, 20.0, 210.0, 100.0),
            {
                "time_layout_mode": "double",
                "countdown_active": {"active": False, "text": "", "progress": 0.0, "status": "idle"},
                "countdown_presets": [
                    {"minutes": 5, "label": "5"},
                    {"minutes": 15, "label": "15"},
                    {"minutes": 10, "label": "10"},
                    {"minutes": 20, "label": "20"},
                ],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        button = visual.plugin_action_rects["timer-start-5"]
        self.assertGreater(button.width(), button.height() * 1.8)
        self.assertIn("5分钟", texts)

    def test_time_countdown_stretch_presets_are_four_large_rings(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_countdown_module(
            painter,
            QRectF(20.0, 20.0, 380.0, 150.0),
            {
                "time_layout_mode": "stretch",
                "countdown_active": {"active": False, "text": "", "progress": 0.0, "status": "idle"},
                "countdown_presets": [
                    {"minutes": 5, "label": "5"},
                    {"minutes": 15, "label": "15"},
                    {"minutes": 10, "label": "10"},
                    {"minutes": 20, "label": "20"},
                ],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        buttons = [visual.plugin_action_rects[f"timer-start-{minutes}"] for minutes in (5, 15, 10, 20)]
        self.assertTrue(all(button.width() > visual._u(66.0) for button in buttons))
        self.assertTrue(all(abs(button.width() - button.height()) <= visual._u(8.0) for button in buttons))
        self.assertLess(buttons[0].left(), buttons[1].left())
        self.assertLess(buttons[1].left(), buttons[2].left())
        self.assertLess(buttons[2].left(), buttons[3].left())
        self.assertGreater(buttons[-1].right() - buttons[0].left(), visual._u(280.0))

    def test_time_panel_default_and_stretch_layouts_are_distinct(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        default_layout = visual._time_plugin_panel_layout(QRectF(0.0, 0.0, 560.0, 188.0))
        stretch_layout = visual._time_plugin_panel_layout(QRectF(0.0, 0.0, 880.0, 220.0))

        self.assertEqual(default_layout["time_layout_mode"], "triple")
        self.assertEqual(stretch_layout["time_layout_mode"], "stretch")
        self.assertGreater(default_layout["time_clock"].width(), 120.0)
        self.assertGreater(stretch_layout["time_countdown"].width(), default_layout["time_countdown"].width() + 40.0)
        self.assertGreater(stretch_layout["time_alarm"].width(), stretch_layout["time_countdown"].width())

    def test_time_panel_uses_double_layout_before_triple_modules_crush(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        layout = visual._time_plugin_panel_layout(QRectF(0.0, 0.0, 380.0, 156.0))

        self.assertEqual(layout["time_layout_mode"], "double")
        self.assertEqual(layout["time_visible_module"], "countdown")
        self.assertGreater(layout["time_clock"].width(), 120.0)
        self.assertGreater(layout["time_countdown"].width(), 120.0)
        self.assertEqual(layout["time_alarm"].width(), 0.0)
        self.assertIn("time_pager", layout)

    def test_time_dashboard_layout_keeps_only_title_not_split_strip(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        layout = visual._time_plugin_panel_layout(QRectF(0.0, 0.0, 560.0, 188.0))

        self.assertLessEqual(layout["body"].top() - layout["time_title"].bottom(), visual._u(8.0))
        self.assertLess(layout["time_title"].height(), visual._u(24.0))
        self.assertGreater(layout["time_countdown"].top(), layout["time_title"].bottom())

    def test_plugin_panel_height_adapts_for_narrow_time_and_editor(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"

        narrow_height = visual._plugin_panel_height(260.0)
        wide_height = visual._plugin_panel_height(620.0)
        visual.plugin_time_mode = "alarm_editor"
        editor_height = visual._plugin_panel_height(420.0)

        self.assertLess(narrow_height, wide_height)
        self.assertGreater(editor_height, narrow_height)
        self.assertGreaterEqual(editor_height, visual._u(168.0))

    def test_time_panel_wide_height_preserves_reference_module_depth(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        height = visual._plugin_panel_height(620.0)
        layout = visual._time_plugin_panel_layout(QRectF(0.0, 0.0, 620.0, height))

        self.assertGreaterEqual(height, visual._u(216.0))
        self.assertGreaterEqual(layout["time_countdown"].height(), visual._u(82.0))
        self.assertGreaterEqual(layout["time_alarm"].height(), visual._u(82.0))

    def test_time_panel_default_layout_is_taller_and_dense(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"

        height = visual._plugin_panel_height(560.0)
        layout = visual._time_plugin_panel_layout(QRectF(0.0, 0.0, 560.0, height))

        self.assertGreaterEqual(height, visual._u(236.0))
        self.assertEqual(layout["time_layout_mode"], "triple")
        self.assertGreaterEqual(layout["time_countdown"].height(), visual._u(140.0))
        self.assertLessEqual(height - layout["time_alarm"].bottom(), visual._u(10.0))

    def test_time_panel_layout_uses_height_without_large_bottom_gap(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        rect = QRectF(0.0, 0.0, 620.0, visual._plugin_panel_height(620.0))
        layout = visual._time_plugin_panel_layout(rect)

        self.assertLessEqual(rect.bottom() - layout["time_countdown"].bottom(), visual._u(12.0))
        self.assertGreaterEqual(layout["time_countdown"].height(), visual._u(120.0))

    def test_time_panel_stretch_layout_gives_timer_more_depth(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        layout = visual._time_plugin_panel_layout(QRectF(0.0, 0.0, 980.0, 236.0))

        self.assertEqual(layout["time_layout_mode"], "stretch")
        self.assertGreaterEqual(layout["time_countdown"].height(), visual._u(128.0))
        self.assertGreater(layout["time_countdown"].width(), visual._u(260.0))
        self.assertGreater(layout["time_alarm"].width(), visual._u(260.0))

    def test_time_countdown_running_ring_is_centered_like_reference(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        rect = QRectF(120.0, 40.0, 142.0, 88.0)

        ring = visual._time_countdown_ring_rect(rect)

        self.assertAlmostEqual(ring.center().x(), rect.center().x(), delta=1.0)
        self.assertAlmostEqual(ring.center().y(), rect.center().y() + visual._u(4.0), delta=2.0)
        self.assertLessEqual(ring.width(), rect.height() - visual._u(18.0))

    def test_time_countdown_expanded_ring_uses_double_weight(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        widths = visual._time_countdown_ring_widths()

        self.assertGreaterEqual(widths["width"], visual._u(7.6))
        self.assertGreaterEqual(widths["track_width"], visual._u(6.4))

    def test_time_panel_narrow_active_module_defaults_to_countdown(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertEqual(visual._time_visible_module_for_width(260.0), "countdown")

    def test_time_panel_wheel_pages_double_layout_right_module(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"
        visual.plugin_panel_last_width = 380.0
        visual.plugin_time_visible_module = "countdown"
        visual.update = lambda: None

        class FakeDelta:
            def __init__(self, y):
                self._y = y

            def y(self):
                return self._y

        class FakeWheel:
            def __init__(self, y):
                self.accepted = False
                self._delta = FakeDelta(y)

            def angleDelta(self):
                return self._delta

            def accept(self):
                self.accepted = True

        event = FakeWheel(-120)

        self.assertTrue(visual._handle_time_panel_module_wheel(event))
        self.assertEqual(visual.plugin_time_visible_module, "alarm")
        self.assertTrue(event.accepted)

        second = FakeWheel(-120)
        self.assertTrue(visual._handle_time_panel_module_wheel(second))
        self.assertEqual(visual.plugin_time_visible_module, "countdown")

    def test_time_alarm_save_caps_rows_at_eight(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_rows = [
            {"time": f"06:{index:02d}", "label": f"Alarm {index}", "enabled": True, "frequency": "daily"}
            for index in range(8)
        ]
        visual.update = lambda: None

        visual.plugin_alarm_editor = {"hour": 7, "minute": 45, "note": "New", "frequency": "每天", "frequency_unit": "daily", "weekdays": []}
        visual._activate_plugin_action("alarm-editor-save")

        self.assertEqual(len(visual.plugin_alarm_rows), 8)
        self.assertEqual(visual.plugin_alarm_rows[0]["time"], "07:45")

    def test_time_alarm_module_paints_scrollable_rows_and_yellow_add_button(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_rows = [
            {"time": f"06:{index:02d}", "label": f"Alarm {index}", "enabled": index % 2 == 0, "frequency": "daily"}
            for index in range(8)
        ]
        model = {"alarms": [dict(row) for row in visual.plugin_alarm_rows]}
        image = QImage(320, 180, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 240.0, 120.0)

        visual._paint_time_alarm_module(painter, rect, model, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        visible_edits = [key for key in visual.plugin_action_rects if re.fullmatch(r"alarm-edit-\d+", key)]
        self.assertLess(len(visible_edits), 8)
        self.assertIn("alarm-list", visual.plugin_action_rects)
        self.assertEqual(visual.plugin_alarm_visible_range[0], 0)
        self.assertEqual(visual.plugin_alarm_visible_range[1], 4)
        add = visual.plugin_action_rects["alarm-add-open"]
        self.assertGreaterEqual(add.height(), visual._u(27.0))
        pixel = image.pixelColor(QPoint(int(add.left() + visual._u(10.0)), int(add.center().y())))
        self.assertGreater(pixel.red(), 210)
        self.assertGreater(pixel.green(), 140)
        self.assertLess(pixel.blue(), 80)
        self.assertLess(visual.plugin_alarm_list_rect.bottom(), add.bottom())
        self.assertGreater(visual.plugin_alarm_list_max_scroll, 4.0)

    def test_alarm_due_confirmation_replaces_scroll_list_and_add_button(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_rows = [
            {"time": f"06:{index:02d}", "label": f"Alarm {index}", "enabled": True, "frequency": "daily"}
            for index in range(6)
        ]
        module_rect = QRectF(20.0, 20.0, 280.0, 150.0)
        image = QImage(360, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(
            painter,
            module_rect,
            {
                "alarms": [dict(row) for row in visual.plugin_alarm_rows],
                "alarm_due_pending": {"time": "07:30", "label": "晨会"},
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        due = visual.plugin_action_rects["alarm-due-confirm"]
        self.assertNotIn("alarm-list", visual.plugin_action_rects)
        self.assertNotIn("alarm-add-open", visual.plugin_action_rects)
        self.assertGreaterEqual(due.height(), module_rect.height() - visual._u(48.0))
        self.assertGreaterEqual(due.height(), module_rect.height())
        self.assertEqual(getattr(visual, "plugin_alarm_visible_range", None), (0, 0))

    def test_alarm_due_confirmation_paints_full_module_block(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        module_rect = QRectF(20.0, 20.0, 280.0, 150.0)
        image = QImage(360, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(
            painter,
            module_rect,
            {
                "alarms": [],
                "alarm_due_pending": {"time": "07:30", "label": "晨会"},
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        header_pixel = image.pixelColor(QPoint(int(module_rect.right() - visual._u(18.0)), int(module_rect.top() + visual._u(14.0))))
        self.assertGreater(header_pixel.red(), 210)
        self.assertGreater(header_pixel.green(), 150)
        self.assertLess(header_pixel.blue(), 90)

    def test_time_alarm_add_button_text_is_spaced_and_bold(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_rows = [{"time": "06:43", "label": "起床", "enabled": True, "frequency": "daily"}]
        calls = []
        visual._paint_static_elided_text = lambda _painter, _rect, text, size, _color, bold, *_args, **_kwargs: calls.append((str(text), int(size), bool(bold)))
        image = QImage(320, 180, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(painter, QRectF(20.0, 20.0, 240.0, 120.0), {"alarms": visual.plugin_alarm_rows}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertIn(("添 加 闹 钟", 10, True), calls)

    def test_time_alarm_edit_button_is_removed_and_row_body_opens_editor(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.plugin_time_mode = "dashboard"
        visual._main_kind = lambda: IslandKind.PLUGIN
        visual.plugin_action_rects = {}
        visual.plugin_alarm_rows = [{"time": "06:43", "label": "起床", "enabled": True, "frequency": "daily"}]
        image = QImage(320, 180, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(painter, QRectF(20.0, 20.0, 240.0, 120.0), {"alarms": visual.plugin_alarm_rows}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertNotIn("alarm-edit-button-0", visual.plugin_action_rects)
        edit = visual.plugin_action_rects["alarm-edit-0"]
        toggle = visual.plugin_action_rects["alarm-toggle-0"]
        self.assertLess(edit.right(), toggle.left())
        self.assertEqual(visual._hit_test(edit.center()), "plugin-action:alarm-edit-0")

    def test_time_alarm_list_long_notes_scroll_without_scrolling_time(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_rows = [{"time": "15:32", "label": "吃点东西搞点", "enabled": True, "frequency": "daily"}]
        calls = []
        visual._paint_scrolling_text = lambda _painter, _rect, text, size, _color, bold, *_args, **_kwargs: calls.append((str(text), int(size), bool(bold)))
        image = QImage(320, 180, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(painter, QRectF(20.0, 20.0, 240.0, 120.0), {"alarms": visual.plugin_alarm_rows}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertIn(("吃点东西搞点", 10, True), calls)
        self.assertFalse(any("15:32" in text for text, _size, _bold in calls))

    def test_time_alarm_list_wheel_scrolls_until_bounds_then_pages_module(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"
        visual.plugin_panel_last_width = 380.0
        visual.plugin_time_visible_module = "alarm"
        visual.plugin_alarm_list_scroll_index = 0.0
        visual.plugin_alarm_list_rect = QRectF(100.0, 50.0, 180.0, 70.0)
        visual.plugin_alarm_visible_range = (0, 4)
        visual.plugin_alarm_rows = [
            {"time": f"06:{index:02d}", "label": f"Alarm {index}", "enabled": True, "frequency": "daily"}
            for index in range(8)
        ]
        visual.update = lambda: None

        class FakeDelta:
            def __init__(self, y):
                self._y = y

            def y(self):
                return self._y

        class FakeWheel:
            def __init__(self, y, pos):
                self.accepted = False
                self._delta = FakeDelta(y)
                self._pos = pos

            def angleDelta(self):
                return self._delta

            def position(self):
                return self._pos

            def accept(self):
                self.accepted = True

        down = FakeWheel(-120, QPointF(130.0, 70.0))
        self.assertTrue(visual._handle_time_panel_module_wheel(down))
        self.assertEqual(visual.plugin_time_visible_module, "alarm")
        self.assertEqual(visual.plugin_alarm_list_scroll_index, 1.0)
        self.assertTrue(down.accepted)

        visual.plugin_alarm_list_scroll_index = 4.0
        visual.plugin_alarm_list_bottom_spacer_rows = 1.0
        bottom_down = FakeWheel(-120, QPointF(130.0, 70.0))
        self.assertTrue(visual._handle_time_panel_module_wheel(bottom_down))
        self.assertGreater(visual.plugin_alarm_list_scroll_index, 4.0)
        self.assertEqual(visual.plugin_time_visible_module, "alarm")

        visual.plugin_alarm_list_scroll_index = 5.0
        true_bottom_down = FakeWheel(-120, QPointF(130.0, 70.0))
        self.assertTrue(visual._handle_time_panel_module_wheel(true_bottom_down))
        self.assertEqual(visual.plugin_alarm_list_scroll_index, 5.0)
        self.assertEqual(visual.plugin_time_visible_module, "countdown")

        visual.plugin_time_visible_module = "alarm"
        visual.plugin_alarm_list_scroll_index = 0
        outside = FakeWheel(-120, QPointF(20.0, 20.0))
        self.assertTrue(visual._handle_time_panel_module_wheel(outside))
        self.assertEqual(visual.plugin_time_visible_module, "countdown")

    def test_time_alarm_list_wheel_supports_continuous_fractional_scroll(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"
        visual.plugin_panel_last_width = 380.0
        visual.plugin_time_visible_module = "alarm"
        visual.plugin_alarm_list_scroll_index = 0.0
        visual.plugin_alarm_list_rect = QRectF(100.0, 50.0, 180.0, 70.0)
        visual.plugin_alarm_visible_range = (0, 4)
        visual.plugin_alarm_rows = [
            {"time": f"06:{index:02d}", "label": f"Alarm {index}", "enabled": True, "frequency": "daily"}
            for index in range(8)
        ]
        visual.update = lambda: None

        class FakeDelta:
            def y(self):
                return -60

        class FakeWheel:
            accepted = False

            def angleDelta(self):
                return FakeDelta()

            def position(self):
                return QPointF(130.0, 70.0)

            def accept(self):
                self.accepted = True

        event = FakeWheel()
        self.assertTrue(visual._handle_time_panel_module_wheel(event))
        self.assertGreater(visual.plugin_alarm_list_scroll_index, 0.0)
        self.assertLess(visual.plugin_alarm_list_scroll_index, 1.0)
        self.assertEqual(visual.plugin_time_visible_module, "alarm")

    def test_alarm_list_can_scroll_into_bottom_spacer_when_row_count_matches_view(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"
        visual.plugin_panel_last_width = 380.0
        visual.plugin_time_visible_module = "alarm"
        visual.plugin_alarm_list_scroll_index = 0.0
        visual.plugin_alarm_list_bottom_spacer_rows = 1.0
        visual.plugin_alarm_list_rect = QRectF(100.0, 50.0, 180.0, 70.0)
        visual.plugin_alarm_visible_range = (0, 4)
        visual.plugin_alarm_rows = [
            {"time": f"06:{index:02d}", "label": f"Alarm {index}", "enabled": True, "frequency": "daily"}
            for index in range(4)
        ]
        visual.update = lambda: None

        class FakeDelta:
            def y(self):
                return -120

        class FakeWheel:
            def angleDelta(self):
                return FakeDelta()

            def position(self):
                return QPointF(130.0, 70.0)

            def accept(self):
                pass

        self.assertTrue(visual._handle_time_panel_module_wheel(FakeWheel()))

        self.assertGreater(visual.plugin_alarm_list_scroll_index, 0.0)
        self.assertEqual(visual.plugin_time_visible_module, "alarm")

    def test_time_alarm_list_wheel_scrolls_in_stretch_layout(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"
        visual.plugin_panel_last_width = 980.0
        visual.plugin_time_visible_module = "countdown"
        visual.plugin_alarm_list_scroll_index = 0.0
        visual.plugin_alarm_list_rect = QRectF(100.0, 50.0, 260.0, 100.0)
        visual.plugin_alarm_visible_range = (0, 4)
        visual.plugin_alarm_rows = [
            {"time": f"06:{index:02d}", "label": f"Alarm {index}", "enabled": True, "frequency": "daily"}
            for index in range(8)
        ]
        visual.update = lambda: None

        class FakeDelta:
            def y(self):
                return -120

        class FakeWheel:
            accepted = False

            def angleDelta(self):
                return FakeDelta()

            def position(self):
                return QPointF(130.0, 70.0)

            def accept(self):
                self.accepted = True

        event = FakeWheel()

        self.assertTrue(visual._handle_time_alarm_list_wheel(event))
        self.assertEqual(visual.plugin_alarm_list_scroll_index, 1.0)
        self.assertEqual(visual.plugin_time_visible_module, "countdown")
        self.assertTrue(event.accepted)

    def test_time_panel_module_wheel_starts_vertical_transition_animation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"
        visual.plugin_panel_last_width = 380.0
        visual.plugin_time_visible_module = "countdown"
        visual.update = lambda: None

        class FakeDelta:
            def y(self):
                return -120

        class FakeWheel:
            def angleDelta(self):
                return FakeDelta()

            def position(self):
                return QPointF(20.0, 20.0)

            def accept(self):
                pass

        self.assertTrue(visual._handle_time_panel_module_wheel(FakeWheel()))

        self.assertEqual(visual.plugin_time_visible_module, "alarm")
        self.assertEqual(visual.plugin_time_module_animation["from"], "countdown")
        self.assertEqual(visual.plugin_time_module_animation["to"], "alarm")

    def test_time_module_shell_uses_dark_fill_without_yellow_overlay(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        image = QImage(220, 130, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 160.0, 84.0)

        visual._paint_time_module_shell(painter, rect, visual._plugin_theme_colors(), active=True)
        painter.end()

        pixel = image.pixelColor(rect.center().toPoint())
        self.assertLess(pixel.red() - pixel.blue(), 20)
        self.assertLess(pixel.green() - pixel.blue(), 20)

    def test_time_countdown_idle_does_not_register_pause_resume_or_cancel(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        image = QImage(280, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_countdown_module(
            painter,
            QRectF(20.0, 20.0, 150.0, 100.0),
            {
                "countdown_active": {"active": False, "text": "", "progress": 0.0, "status": "idle"},
                "countdown_presets": [
                    {"minutes": 5, "label": "5"},
                    {"minutes": 15, "label": "15"},
                    {"minutes": 10, "label": "10"},
                    {"minutes": 20, "label": "20"},
                ],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        self.assertIn("timer-start-5", visual.plugin_action_rects)
        for command in ("timer-pause", "timer-resume", "timer-clear"):
            self.assertNotIn(command, visual.plugin_action_rects)

    def test_time_countdown_running_default_ring_only_registers_pause(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        image = QImage(280, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 150.0, 100.0)

        visual._paint_time_countdown_module(
            painter,
            rect,
            {
                "countdown_active": {"active": True, "text": "4:59", "progress": 0.45, "status": "running"},
                "countdown_presets": [],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        self.assertIn("timer-pause", visual.plugin_action_rects)
        self.assertNotIn("timer-clear", visual.plugin_action_rects)
        self.assertLess(abs(visual.plugin_action_rects["timer-pause"].center().x() - rect.center().x()), 8.0)

    def test_time_countdown_paused_default_shows_resume_and_cancel(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        image = QImage(280, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_countdown_module(
            painter,
            QRectF(20.0, 20.0, 150.0, 100.0),
            {
                "countdown_active": {"active": True, "text": "4:59", "progress": 0.45, "status": "paused"},
                "countdown_presets": [],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        self.assertIn("timer-resume", visual.plugin_action_rects)
        self.assertIn("timer-clear", visual.plugin_action_rects)

    def test_time_countdown_paused_ring_body_is_not_clickable(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual._main_kind = lambda: IslandKind.PLUGIN
        visual.plugin_action_rects = {}
        visual.motions = {}
        image = QImage(280, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 150.0, 100.0)

        visual._paint_time_countdown_module(
            painter,
            rect,
            {
                "countdown_active": {"active": True, "text": "4:59", "progress": 0.45, "status": "paused"},
                "countdown_presets": [],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()
        ring = visual._time_countdown_ring_rect(rect)

        self.assertEqual(visual._hit_test(ring.center()), "")
        self.assertEqual(visual._hit_test(visual.plugin_action_rects["timer-resume"].center()), "plugin-action:timer-resume")
        self.assertEqual(visual._hit_test(visual.plugin_action_rects["timer-clear"].center()), "plugin-action:timer-clear")

    def test_time_countdown_done_ring_click_clears_to_presets(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual._main_kind = lambda: IslandKind.PLUGIN
        visual.plugin_action_rects = {}
        image = QImage(280, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 150.0, 100.0)

        visual._paint_time_countdown_module(
            painter,
            rect,
            {
                "countdown_active": {"active": True, "text": "0:00", "progress": 1.0, "status": "done"},
                "countdown_presets": [],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()
        ring = visual._time_countdown_ring_rect(rect)

        self.assertNotIn("timer-pause", visual.plugin_action_rects)
        self.assertEqual(visual._hit_test(ring.center()), "plugin-action:timer-clear")

    def test_time_countdown_ring_click_pauses_through_hit_test_dispatch(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual._main_kind = lambda: IslandKind.PLUGIN
        visual.plugin_action_rects = {}
        visual._pulse_stack = lambda: None
        now = datetime.now().astimezone()
        settings = {
            "v2_timer_started_at": now.isoformat(timespec="seconds"),
            "v2_timer_end_at": (now + timedelta(minutes=5)).isoformat(timespec="seconds"),
        }
        visual.plugin_countdown_entries = [
            {
                "minutes": 5,
                "started_at": settings["v2_timer_started_at"],
                "end_at": settings["v2_timer_end_at"],
                "status": "running",
            }
        ]
        visual.settings_getter = lambda key, default=None: settings.get(key, default)
        visual.settings_setter = lambda key, value: settings.__setitem__(key, value)
        visual.refresh_plugin_data = lambda: None
        image = QImage(280, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 20.0, 150.0, 100.0)

        visual._paint_time_countdown_module(
            painter,
            rect,
            {
                "countdown_active": {"active": True, "text": "4:59", "progress": 0.45, "status": "running"},
                "countdown_presets": [],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()
        key = visual._hit_test(visual.plugin_action_rects["timer-pause"].center())

        self.assertEqual(key, "plugin-action:timer-pause")
        visual._activate_key(key)
        active = visual._time_countdown_active_model({"status": "idle"})
        entries = visual._time_countdown_entries({"countdown_active": active})

        self.assertEqual(entries[0]["status"], "paused")
        self.assertEqual(settings["v2_timer_end_at"], "")

    def test_time_countdown_cancel_clears_stale_entries(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_timer_paused_remaining_seconds = 120
        visual.plugin_countdown_entries = [{"text": "2:00", "status": "paused", "progress": 0.5}]
        settings = {"v2_timer_started_at": "old", "v2_timer_end_at": "old"}
        visual.settings_setter = lambda key, value: settings.__setitem__(key, value)
        visual.refresh_plugin_data = lambda: None

        visual._activate_plugin_action("timer-clear")

        self.assertEqual(visual.plugin_countdown_entries, [])
        self.assertEqual(settings["v2_timer_started_at"], "")
        self.assertEqual(settings["v2_timer_end_at"], "")

    def test_time_countdown_entries_keep_two_most_recent(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_countdown_entries = [
            {"text": "14:59", "progress": 0.10, "status": "running"},
            {"text": "9:59", "progress": 0.20, "status": "running"},
            {"text": "4:59", "progress": 0.45, "status": "running"},
        ]

        entries = visual._time_countdown_entries({"countdown_active": {"active": False}})

        self.assertEqual([entry["text"] for entry in entries], ["4:59", "9:59"])

    def test_time_countdown_paused_model_uses_remaining_seconds(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_timer_paused_remaining_seconds = 249
        visual.plugin_timer_paused_progress = 0.42

        active = visual._time_countdown_active_model({"status": "idle", "progress": 0.0})

        self.assertTrue(active["active"])
        self.assertEqual(active["status"], "paused")
        self.assertEqual(active["text"], "4:09")
        self.assertAlmostEqual(active["progress"], 0.42)

    def test_time_countdown_pause_resume_and_cancel_update_timer_settings(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        now = datetime.now().astimezone()
        settings = {
            "v2_timer_started_at": now.isoformat(timespec="seconds"),
            "v2_timer_end_at": (now + timedelta(minutes=5)).isoformat(timespec="seconds"),
        }
        calls = []
        visual.settings_getter = lambda key, default=None: settings.get(key, default)

        def set_setting(key, value):
            settings[key] = value
            calls.append((key, value))

        visual.settings_setter = set_setting
        visual.refresh_plugin_data = lambda: calls.append(("refresh", "plugins"))
        visual.update = lambda: None

        visual._activate_plugin_action("timer-pause")

        self.assertGreater(visual.plugin_timer_paused_remaining_seconds, 0)
        self.assertEqual(settings["v2_timer_end_at"], "")

        visual._activate_plugin_action("timer-resume")

        self.assertEqual(visual.plugin_timer_paused_remaining_seconds, 0)
        self.assertNotEqual(settings["v2_timer_end_at"], "")

        visual._activate_plugin_action("timer-clear")

        self.assertEqual(settings["v2_timer_end_at"], "")
        self.assertEqual(visual.plugin_timer_paused_remaining_seconds, 0)

    def test_time_clock_hand_angles_use_displayed_time(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        angles = visual._time_clock_hand_angles("15:22:30")

        self.assertAlmostEqual(angles["hour"], 101.25, places=2)
        self.assertAlmostEqual(angles["minute"], 135.0, places=2)
        self.assertAlmostEqual(angles["second"], 180.0, places=2)

    def test_alarm_picker_display_values_show_neighbor_rows(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency": "每周", "weekdays": "二、四"}

        hour_values = visual._alarm_picker_display_values("hour")
        frequency_values = visual._alarm_picker_display_values("frequency")

        hour_center = len(hour_values) // 2
        frequency_center = len(frequency_values) // 2
        self.assertEqual(len(hour_values), 7)
        self.assertEqual([item["text"] for item in hour_values[hour_center - 1 : hour_center + 2]], ["14", "15", "16"])
        self.assertFalse(hour_values[hour_center - 1]["current"])
        self.assertTrue(hour_values[hour_center]["current"])
        self.assertLess(hour_values[hour_center - 1]["opacity"], hour_values[hour_center]["opacity"])
        self.assertEqual(len(frequency_values), 7)
        self.assertEqual([item["text"] for item in frequency_values[frequency_center - 1 : frequency_center + 2]], ["仅一次", "每周", "每天"])

    def test_time_countdown_preset_action_starts_timer(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        calls = []
        visual.settings_setter = lambda key, value: calls.append((key, value))
        visual.refresh_plugin_data = lambda: calls.append(("refresh", "plugins"))

        visual._activate_plugin_action("timer-start-5")

        keys = [key for key, _value in calls if key != "refresh"]
        self.assertIn("v2_timer_started_at", keys)
        self.assertIn("v2_timer_end_at", keys)
        self.assertEqual(calls[-1], ("refresh", "plugins"))

    def test_time_countdown_preset_action_keeps_two_recent_timers(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_countdown_entries = []
        visual.settings_setter = lambda _key, _value: None
        visual.refresh_plugin_data = lambda: None

        visual._activate_plugin_action("timer-start-5")
        visual._activate_plugin_action("timer-start-10")
        visual._activate_plugin_action("timer-start-20")

        self.assertEqual(len(visual.plugin_countdown_entries), 2)
        self.assertEqual([entry["minutes"] for entry in visual.plugin_countdown_entries], [10, 20])

    def test_alarm_add_opens_inline_editor(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        visual._activate_plugin_action("alarm-add-open")

        self.assertEqual(visual.plugin_time_mode, "alarm_editor")
        editor = visual._time_plugin_panel_model({"label": "TIME", "detail": "22:52", "summary": "", "status": "idle"})
        self.assertEqual(editor["mode"], "alarm_editor")
        self.assertIn("hour", editor["editor"])

    def test_alarm_editor_wheel_updates_hour_minute_and_frequency(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency": "每周", "weekdays": "二、四"}

        visual._adjust_alarm_editor_field("hour", 1)
        visual._adjust_alarm_editor_field("minute", -2)
        visual._adjust_alarm_editor_field("frequency", 1)

        self.assertEqual(visual.plugin_alarm_editor["hour"], 16)
        self.assertEqual(visual.plugin_alarm_editor["minute"], 20)
        self.assertEqual(visual.plugin_alarm_editor["frequency"], "每天")

    def test_alarm_editor_note_accepts_keyboard_text_and_backspace(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency": "每周", "weekdays": "二、四"}
        visual.plugin_alarm_editor_focus = "note"
        visual.update = lambda: None

        visual._handle_alarm_editor_key("早")
        visual._handle_alarm_editor_key("课")
        visual._handle_alarm_editor_key("\b")

        self.assertEqual(visual.plugin_alarm_editor["note"], "早")

    def test_alarm_editor_note_accepts_input_method_commit_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency": "每周", "weekdays": "二、四"}
        visual.plugin_alarm_editor_focus = "note"
        visual.update = lambda: None

        class FakeInputMethodEvent:
            accepted = False

            def commitString(self):
                return "早课"

            def accept(self):
                self.accepted = True

        self.assertIn("inputMethodEvent", IslandStackVisual.__dict__)
        event = FakeInputMethodEvent()
        IslandStackVisual.inputMethodEvent(visual, event)

        self.assertEqual(visual.plugin_alarm_editor["note"], "早课")
        self.assertTrue(event.accepted)

    def test_alarm_editor_note_is_limited_to_ten_chars(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {"hour": 7, "minute": 30, "note": "", "frequency": "每天", "weekdays": "二、四"}
        visual.plugin_alarm_editor_focus = "note"
        visual.update = lambda: None

        for char in "一二三四五六七八九十十一":
            visual._handle_alarm_editor_key(char)

        self.assertEqual(visual.plugin_alarm_editor["note"], "一二三四五六七八九十")

    def test_alarm_editor_note_over_limit_sets_hint_without_changing_note(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {"hour": 7, "minute": 30, "note": "一二三四五六七八九十", "frequency": "每天", "weekdays": "二、四"}
        visual.plugin_alarm_editor_focus = "note"
        visual.update = lambda: None

        with patch("local.island_stack_visual.time.monotonic", return_value=44.0):
            self.assertTrue(visual._handle_alarm_editor_key("余"))

        self.assertEqual(visual.plugin_alarm_editor["note"], "一二三四五六七八九十")
        self.assertEqual(visual.plugin_alarm_editor_note_hint, "最多10字")
        self.assertGreater(visual.plugin_alarm_editor_note_hint_until, 44.0)

    def test_alarm_editor_note_focus_paints_caret(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor = {"hour": 7, "minute": 30, "note": "早课", "frequency_unit": "daily", "editor_page": "note"}
        visual.plugin_alarm_editor_focus = "note"
        calls = []
        visual._paint_alarm_editor_caret = lambda _painter, _rect, field, _ref: calls.append(field)
        image = QImage(520, 160, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_editor(painter, QRectF(20.0, 20.0, 400.0, 120.0), {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertIn("note", calls)

    def test_alarm_editor_long_note_scrolls_inside_note_field(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor = {"hour": 7, "minute": 30, "note": "一二三四五六", "frequency_unit": "daily", "editor_page": "note"}
        visual.plugin_alarm_editor_focus = "note"
        calls = []
        visual._paint_scrolling_text = lambda _painter, _rect, text, size, _color, bold, *_args, **_kwargs: calls.append((str(text), int(size), bool(bold)))
        image = QImage(520, 160, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_editor(painter, QRectF(20.0, 20.0, 400.0, 120.0), {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertIn(("一二三四五六", 15, True), calls)

    def test_alarm_note_display_budget_is_five_chars_and_long_notes_scroll(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertEqual(visual._time_alarm_note_display_text("提醒一二三四五六"), "提醒一二三四五六")
        self.assertFalse(visual._time_alarm_note_should_scroll("五字内!", QRectF(0.0, 0.0, 300.0, 32.0), 10, True, 5))
        self.assertTrue(visual._time_alarm_note_should_scroll("超过五字了啊", QRectF(0.0, 0.0, 300.0, 32.0), 10, True, 5))

    def test_alarm_recurrence_label_handles_week_month_and_year(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertEqual(visual._alarm_recurrence_label({"frequency_unit": "weekly", "weekdays": [2, 4, 6]}), "每周")
        self.assertEqual(visual._alarm_recurrence_value_label({"frequency_unit": "weekly", "weekdays": [2, 4, 6]}), "2 4 6")
        self.assertEqual(visual._alarm_recurrence_value_label({"frequency_unit": "weekly", "weekdays": [1, 2, 3, 4, 5, 6, 7]}), "整周")
        self.assertEqual(visual._alarm_recurrence_label({"frequency_unit": "monthly", "month_day": 29}), "每非明年二月")
        self.assertEqual(visual._alarm_recurrence_label({"frequency_unit": "monthly", "month_day": 31}), "每大月")
        self.assertEqual(visual._alarm_recurrence_value_label({"frequency_unit": "monthly", "month_day": 28}), "28号")
        self.assertEqual(visual._alarm_recurrence_label({"frequency_unit": "yearly", "year_month": 4, "year_day": 28}), "每年")
        self.assertEqual(visual._alarm_recurrence_value_label({"frequency_unit": "yearly", "year_month": 4, "year_day": 28}), "4月28日")

    def test_alarm_editor_save_uses_note_as_alarm_label(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_rows = []
        visual.plugin_alarm_editor = {"hour": 7, "minute": 30, "note": "早课", "frequency": "每天", "weekdays": "每天"}
        visual.update = lambda: None

        visual._activate_plugin_action("alarm-editor-save")

        self.assertEqual(visual.plugin_alarm_rows[0]["time"], "07:30")
        self.assertEqual(visual.plugin_alarm_rows[0]["label"], "早课")
        self.assertEqual(visual.plugin_time_mode, "dashboard")

    def test_alarm_editor_save_persists_recurrence_fields(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_rows = []
        visual.plugin_alarm_editor = {
            "hour": 15,
            "minute": 22,
            "note": "喝水",
            "frequency_unit": "weekly",
            "weekdays": [2, 4, 6],
        }
        visual.update = lambda: None

        visual._activate_plugin_action("alarm-editor-save")

        row = visual.plugin_alarm_rows[0]
        self.assertEqual(row["frequency"], "每周")
        self.assertEqual(row["weekdays"], [2, 4, 6])
        self.assertEqual(row["label"], "喝水")

    def test_alarm_editor_reference_layout_uses_single_row_fields(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        layout = visual._time_alarm_editor_layout(QRectF(0.0, 0.0, 480.0, 142.0))

        self.assertEqual([name for name in ("time", "note", "frequency", "weekdays") if name in layout], ["time", "note", "frequency", "weekdays"])
        tops = [layout[name].top() for name in ("time", "note", "frequency", "weekdays")]
        self.assertLess(max(tops) - min(tops), 1.0)
        self.assertLess(layout["title"].top(), layout["time"].top())
        self.assertGreater(layout["time_label"].top(), layout["time"].bottom())
        self.assertGreater(layout["note"].left(), layout["time"].right())
        self.assertGreater(layout["frequency"].left(), layout["note"].right())
        self.assertGreater(layout["weekdays"].left(), layout["frequency"].right())
        self.assertGreater(layout["time"].width(), layout["frequency"].width())

    def test_alarm_editor_reference_actions_are_bottom_wide_buttons(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        rect = QRectF(0.0, 0.0, 500.0, 168.0)

        layout = visual._time_alarm_editor_layout(rect)

        self.assertGreater(layout["save"].top(), layout["time_label"].bottom())
        self.assertGreater(layout["cancel"].top(), layout["time_label"].bottom())
        self.assertGreaterEqual(layout["save"].width(), visual._u(118.0))
        self.assertGreaterEqual(layout["cancel"].width(), visual._u(118.0))
        self.assertGreaterEqual(layout["save"].height(), visual._u(30.0))
        self.assertLess(layout["cancel"].right(), layout["save"].left())
        self.assertLessEqual(layout["save"].bottom(), rect.bottom() - visual._u(7.0))

    def test_alarm_editor_reference_width_uses_paged_narrow_layout(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        layout = visual._time_alarm_editor_layout(QRectF(0.0, 0.0, 420.0, 156.0))

        self.assertEqual(layout["mode"], "narrow")
        self.assertIn("main_note_dot", layout)
        self.assertLess(layout["main_frequency"].left(), layout["main_note_dot"].left())
        self.assertGreater(layout["note"].width(), layout["main_time"].width() * 2.4)

    def test_alarm_editor_narrow_actions_are_bottom_wide_buttons(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        rect = QRectF(0.0, 0.0, 420.0, 190.0)

        layout = visual._time_alarm_editor_layout(rect)

        self.assertEqual(layout["mode"], "narrow")
        self.assertGreater(layout["save"].top(), layout["time_label"].bottom())
        self.assertGreaterEqual(layout["save"].width(), visual._u(118.0))
        self.assertGreaterEqual(layout["cancel"].width(), visual._u(118.0))
        self.assertAlmostEqual(layout["save"].top(), layout["cancel"].top(), delta=1.0)
        self.assertLessEqual(layout["save"].bottom(), rect.bottom() - visual._u(7.0))

    def test_alarm_editor_narrow_layout_uses_available_height(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        rect = QRectF(0.0, 0.0, 420.0, 156.0)

        layout = visual._time_alarm_editor_layout(rect)

        self.assertGreaterEqual(layout["main_time"].height(), visual._u(68.0))
        self.assertGreaterEqual(layout["time_label"].bottom(), rect.top() + rect.height() * 0.72)
        self.assertLessEqual(layout["time_label"].bottom(), rect.bottom() - visual._u(18.0))

    def test_alarm_editor_narrow_layout_has_main_and_note_pages(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        layout = visual._time_alarm_editor_layout(QRectF(0.0, 0.0, 320.0, 156.0))

        self.assertEqual(layout["mode"], "narrow")
        self.assertIn("main_page", layout)
        self.assertIn("note_page", layout)
        self.assertIn("main_frequency", layout)
        self.assertLess(layout["main_frequency"].left(), layout["main_note_dot"].left())
        self.assertGreater(layout["note"].width(), layout["time"].width())

    def test_alarm_editor_narrow_paint_registers_paged_fields(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "weekly", "weekdays": [2, 4, 6]}
        model = {"editor": visual.plugin_alarm_editor}
        image = QImage(360, 190, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_editor(painter, QRectF(12.0, 12.0, 320.0, 156.0), model, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertIn("hour", visual.plugin_alarm_editor_rects)
        self.assertIn("frequency_unit", visual.plugin_alarm_editor_rects)
        self.assertIn("alarm-editor-page-note", visual.plugin_action_rects)
        self.assertIn("alarm-editor-open-weekdays", visual.plugin_action_rects)

    def test_alarm_editor_daily_frequency_reflows_center_without_value_field(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "daily", "editor_page": "main"}
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 14.0, 420.0, 190.0)

        visual._paint_time_alarm_editor(painter, rect, {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        layout = visual._time_alarm_editor_layout(rect)
        frequency = visual.plugin_alarm_editor_rects["frequency_unit"]
        slot_left = layout["main_time"].right() + visual._u(16.0)
        slot_right = layout["main_note_dot"].left() - visual._u(8.0)
        slot_center = (slot_left + slot_right) / 2.0
        self.assertAlmostEqual(frequency.center().x(), slot_center, delta=visual._u(4.0))
        self.assertGreater(frequency.width(), layout["main_frequency"].width())

    def test_alarm_editor_frequency_unit_reflow_records_animation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_editor = {"frequency_unit": "weekly", "hour": 15, "minute": 22, "note": ""}
        visual.update = lambda: None

        with patch("local.island_stack_visual.time.monotonic", return_value=123.0):
            visual._adjust_alarm_editor_field("frequency_unit", -1)

        self.assertEqual(visual.plugin_alarm_editor["frequency_unit"], "daily")
        self.assertEqual(visual.plugin_alarm_editor_reflow_animation["from"], "weekly")
        self.assertEqual(visual.plugin_alarm_editor_reflow_animation["to"], "daily")
        self.assertEqual(visual.plugin_alarm_editor_reflow_animation["started_at"], 123.0)

    def test_alarm_editor_paint_registers_figure_two_fields_without_handle_strip(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        model = {"editor": {"hour": 15, "minute": 22, "note": "", "frequency": "每周", "weekdays": "二、四"}}
        image = QImage(540, 170, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 14.0, 500.0, 142.0)

        visual._paint_time_alarm_editor(painter, rect, model, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        layout = visual._time_alarm_editor_layout(rect)
        self.assertIn("hour", visual.plugin_alarm_editor_rects)
        self.assertIn("minute", visual.plugin_alarm_editor_rects)
        self.assertIn("frequency", visual.plugin_alarm_editor_rects)
        self.assertIn("weekdays", visual.plugin_alarm_editor_rects)
        self.assertEqual(visual.plugin_action_rects["alarm-editor-note"], layout["note"])
        self.assertGreaterEqual(visual.plugin_alarm_editor_rects["hour"].left(), layout["time"].left())
        self.assertLessEqual(visual.plugin_alarm_editor_rects["minute"].right(), layout["time"].right())
        self.assertLess(layout["title"].height(), visual._u(24.0))

    def test_alarm_editor_stretch_uses_dynamic_frequency_fields(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {
            "hour": 15,
            "minute": 22,
            "note": "Tea",
            "frequency_unit": "monthly",
            "month_day": 31,
            "editor_page": "main",
            "detail_mode": "",
        }
        image = QImage(800, 230, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_editor(
            painter,
            QRectF(20.0, 16.0, 720.0, 190.0),
            {"editor": visual.plugin_alarm_editor},
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        self.assertIn("frequency_unit", visual.plugin_alarm_editor_rects)
        self.assertIn("month_day", visual.plugin_alarm_editor_rects)
        self.assertIn("alarm-editor-focus-frequency_unit", visual.plugin_action_rects)
        self.assertIn("alarm-editor-open-month-day", visual.plugin_action_rects)

    def test_alarm_editor_paints_visible_confirm_button(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        model = {"editor": {"hour": 19, "minute": 0, "note": "起床", "frequency": "每天", "weekdays": "二、四"}}
        image = QImage(540, 170, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 14.0, 500.0, 142.0)

        visual._paint_time_alarm_editor(painter, rect, model, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        save = visual.plugin_action_rects["alarm-editor-save"]
        # The center contains the dark “确定” glyph; sample the button fill.
        pixel = image.pixelColor(QPoint(round(save.left() + save.width() * 0.2), round(save.center().y())))
        self.assertGreater(pixel.red(), 210)
        self.assertGreater(pixel.green(), 150)
        self.assertLess(pixel.blue(), 60)

    def test_alarm_editor_adjustment_records_spin_animation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency": "每周", "weekdays": "二、四"}

        with patch("local.island_stack_visual.time.monotonic", return_value=12.5):
            visual._adjust_alarm_editor_field("hour", 1)

        animation = visual.plugin_alarm_editor_spin_animations["hour"]
        self.assertEqual(animation["direction"], 1)
        self.assertEqual(animation["started_at"], 12.5)

    def test_alarm_picker_display_values_include_adjustment_offsets(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_editor = {"hour": 16, "minute": 22, "note": "", "frequency": "每周", "weekdays": "二、四"}
        visual.plugin_alarm_editor_spin_animations = {"hour": {"direction": 1, "started_at": 20.0}}

        with patch("local.island_stack_visual.time.monotonic", return_value=20.08):
            values = visual._alarm_picker_display_values("hour")

        self.assertTrue(any(abs(float(item.get("offset", 0.0))) > 0.01 for item in values))

    def test_alarm_picker_weekdays_uses_whole_week_label(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_editor = {"frequency_unit": "weekly", "weekdays": [1, 2, 3, 4, 5, 6, 7]}

        values = visual._alarm_picker_display_values("weekdays")

        center = len(values) // 2
        self.assertEqual(values[center]["text"], "整周")

    def test_alarm_picker_field_uses_larger_legible_type(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_editor = {"frequency_unit": "daily", "hour": 15, "minute": 22, "note": ""}
        visual.plugin_alarm_editor_rects = {}
        calls = []
        original_font = visual._font

        def record_font(size, weight=QFont.Weight.Normal, shrink=True):
            calls.append(float(size))
            return original_font(size, weight, shrink)

        visual._font = record_font
        image = QImage(260, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_alarm_picker_field(painter, QRectF(20.0, 20.0, 160.0, 74.0), "frequency_unit", "每天", "频次", visual._plugin_theme_colors(), QColor(255, 204, 0))
        painter.end()

        self.assertGreaterEqual(max(calls), 14.0)
        self.assertGreaterEqual(min(calls), 10.0)

    def test_alarm_editor_accepts_direct_numeric_input_for_time_fields(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {"hour": 0, "minute": 0, "note": "", "frequency": "每周", "weekdays": "二、四"}
        visual.update = lambda: None

        visual._activate_plugin_action("alarm-editor-focus-hour")
        visual._handle_alarm_editor_key("2")
        visual._handle_alarm_editor_key("3")
        visual._activate_plugin_action("alarm-editor-focus-minute")
        visual._handle_alarm_editor_key("4")
        visual._handle_alarm_editor_key("5")

        self.assertEqual(visual.plugin_alarm_editor["hour"], 23)
        self.assertEqual(visual.plugin_alarm_editor["minute"], 45)

    def test_alarm_editor_accepts_direct_numeric_input_for_recurrence_fields(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {
            "hour": 15,
            "minute": 22,
            "note": "",
            "frequency_unit": "monthly",
            "month_day": 1,
            "year_month": 1,
            "year_day": 1,
        }
        visual.plugin_alarm_editor_input_buffers = {}
        visual._alarm_year_boundary_year = lambda: 2027
        visual.update = lambda: None

        visual._activate_plugin_action("alarm-editor-focus-month_day")
        visual._handle_alarm_editor_key("3")
        visual._handle_alarm_editor_key("1")
        visual._activate_plugin_action("alarm-editor-focus-year_month")
        visual._handle_alarm_editor_key("0")
        visual._handle_alarm_editor_key("2")
        visual._activate_plugin_action("alarm-editor-focus-year_day")
        visual._handle_alarm_editor_key("3")
        visual._handle_alarm_editor_key("0")

        self.assertEqual(visual.plugin_alarm_editor["month_day"], 31)
        self.assertEqual(visual.plugin_alarm_editor["year_month"], 2)
        self.assertEqual(visual.plugin_alarm_editor["year_day"], 29)

    def test_weekly_detail_mode_toggles_selected_weekdays(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_alarm_editor = {"frequency_unit": "weekly", "weekdays": [2, 4], "hour": 15, "minute": 22, "note": ""}
        visual.update = lambda: None

        visual._activate_plugin_action("alarm-editor-open-weekdays")
        visual._activate_plugin_action("alarm-editor-toggle-weekday-6")

        self.assertEqual(visual.plugin_alarm_editor["detail_mode"], "weekly")
        self.assertEqual(visual.plugin_alarm_editor["weekdays"], [2, 4, 6])

    def test_monthly_and_yearly_day_adjustment_clamps_to_calendar_boundaries(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_editor = {"frequency_unit": "monthly", "month_day": 31}

        visual._adjust_alarm_editor_field("month_day", 1)
        self.assertEqual(visual.plugin_alarm_editor["month_day"], 1)

        visual.plugin_alarm_editor = {"frequency_unit": "yearly", "year_month": 2, "year_day": 30}
        visual._normalize_alarm_editor_recurrence(visual.plugin_alarm_editor)
        self.assertEqual(visual.plugin_alarm_editor["year_day"], 29)

    def test_yearly_feb_twenty_nine_is_preserved_as_leap_year_alarm(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._alarm_year_boundary_year = lambda: 2027
        editor = {"frequency_unit": "yearly", "year_month": 2, "year_day": 29}

        normalized = visual._normalize_alarm_editor_recurrence(editor)

        self.assertEqual(normalized["year_day"], 29)
        self.assertEqual(visual._alarm_recurrence_label(normalized), "每闰年")
        self.assertEqual(visual._alarm_recurrence_value_label(normalized), "2月29日")

    def test_yearly_month_adjustment_clamps_day_for_target_month(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_editor = {"frequency_unit": "yearly", "year_month": 3, "year_day": 31}

        visual._adjust_alarm_editor_field("year_month", -1)

        self.assertEqual(visual.plugin_alarm_editor["year_month"], 2)
        self.assertLessEqual(visual.plugin_alarm_editor["year_day"], 29)

    def test_alarm_editor_narrow_detail_paint_registers_month_and_year_fields(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        image = QImage(360, 190, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(12.0, 12.0, 320.0, 156.0)

        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "monthly", "month_day": 29, "detail_mode": "monthly"}
        visual._paint_time_alarm_editor(painter, rect, {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        self.assertIn("month_day", visual.plugin_alarm_editor_rects)

        visual.plugin_alarm_editor_rects = {}
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "yearly", "year_month": 2, "year_day": 28, "detail_mode": "yearly"}
        visual._paint_time_alarm_editor(painter, rect, {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertIn("year_month", visual.plugin_alarm_editor_rects)
        self.assertIn("year_day", visual.plugin_alarm_editor_rects)

    def test_alarm_editor_detail_mode_is_standalone_without_editor_actions(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "weekly", "weekdays": [2, 4, 6], "detail_mode": "weekly"}
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_editor(painter, QRectF(20.0, 14.0, 420.0, 190.0), {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertNotIn("alarm-editor-save", visual.plugin_action_rects)
        self.assertNotIn("alarm-editor-cancel", visual.plugin_action_rects)
        self.assertNotIn("alarm-editor-page-note", visual.plugin_action_rects)

    def test_alarm_editor_detail_page_has_top_right_confirm_to_return(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "yearly", "year_month": 2, "year_day": 29, "detail_mode": "yearly"}
        visual.update = lambda: None
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 14.0, 420.0, 190.0)

        visual._paint_time_alarm_editor(painter, rect, {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        confirm = visual.plugin_action_rects["alarm-editor-detail-confirm"]
        self.assertGreater(confirm.right(), rect.right() - visual._u(48.0))
        self.assertLess(confirm.top(), rect.top() + visual._u(28.0))

        visual._activate_plugin_action("alarm-editor-detail-confirm")

        self.assertEqual(visual.plugin_alarm_editor["detail_mode"], "")
        self.assertEqual(visual.plugin_alarm_editor["editor_page"], "main")

    def test_alarm_editor_recurrence_value_click_opens_dedicated_page(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.music_playing = False
        visual.persistent_plugin = True
        visual.enabled_plugin_count = 3
        visual.messages = []
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "weekly", "weekdays": [2, 4, 6], "editor_page": "main", "detail_mode": ""}
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_editor(painter, QRectF(20.0, 14.0, 420.0, 190.0), {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        open_rect = visual.plugin_action_rects["alarm-editor-open-weekdays"]
        self.assertEqual(visual._hit_test(open_rect.center()), "plugin-action:alarm-editor-open-weekdays")

    def test_alarm_editor_detail_input_fields_paint_caret_when_focused(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor_focus = "month_day"
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "monthly", "month_day": 28, "detail_mode": "monthly"}
        caret_fields = []
        visual._paint_alarm_editor_caret = lambda _painter, _rect, field, _ref: caret_fields.append(field)
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_editor(painter, QRectF(20.0, 14.0, 420.0, 190.0), {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertIn("month_day", caret_fields)

    def test_alarm_editor_weekly_detail_uses_left_aligned_weekday_picker(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "weekly", "weekdays": [2, 4, 6], "detail_mode": "weekly"}
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 14.0, 420.0, 190.0)

        visual._paint_time_alarm_editor(painter, rect, {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        chips = [visual.plugin_action_rects[f"alarm-editor-toggle-weekday-{day}"] for day in range(1, 8)]
        self.assertEqual(len(chips), 7)
        self.assertLess(chips[0].left(), rect.left() + visual._u(56.0))
        for previous, current in zip(chips, chips[1:]):
            self.assertGreater(current.left(), previous.right())
            self.assertAlmostEqual(current.top(), chips[0].top(), delta=1.0)

    def test_alarm_editor_month_and_year_detail_match_input_units(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        image = QImage(460, 230, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(20.0, 14.0, 420.0, 198.0)

        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "monthly", "month_day": 31, "detail_mode": "monthly"}
        visual._paint_time_alarm_editor(painter, rect, {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        month_day = visual.plugin_alarm_editor_rects["month_day"]
        self.assertGreater(month_day.width(), visual._u(92.0))
        self.assertIn("alarm-editor-focus-month_day", visual.plugin_action_rects)

        visual.plugin_action_rects = {}
        visual.plugin_alarm_editor_rects = {}
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "yearly", "year_month": 2, "year_day": 28, "detail_mode": "yearly"}
        visual._paint_time_alarm_editor(painter, rect, {"editor": visual.plugin_alarm_editor}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        month = visual.plugin_alarm_editor_rects["year_month"]
        day = visual.plugin_alarm_editor_rects["year_day"]
        self.assertGreater(month.width(), visual._u(78.0))
        self.assertGreater(day.width(), visual._u(78.0))
        self.assertGreater(day.left(), month.right())
        self.assertIn("alarm-editor-focus-year_month", visual.plugin_action_rects)
        self.assertIn("alarm-editor-focus-year_day", visual.plugin_action_rects)

    def test_alarm_editor_narrow_page_wheel_switches_pages_with_animation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "alarm_editor"
        visual.plugin_panel_last_width = 420.0
        visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency_unit": "weekly", "weekdays": [2, 4, 6], "editor_page": "main"}
        visual.plugin_alarm_editor_rects = {"hour": QRectF(20.0, 40.0, 80.0, 68.0)}
        visual.update = lambda: None

        class FakeDelta:
            def __init__(self, y):
                self._y = y

            def y(self):
                return self._y

        class FakeWheel:
            def __init__(self, y):
                self.accepted = False
                self._delta = FakeDelta(y)

            def position(self):
                return QPointF(400.0, 120.0)

            def angleDelta(self):
                return self._delta

            def accept(self):
                self.accepted = True

        with patch("local.island_stack_visual.time.monotonic", return_value=91.0):
            event = FakeWheel(-120)
            self.assertTrue(visual._handle_time_alarm_editor_wheel(event))

        self.assertEqual(visual.plugin_alarm_editor["editor_page"], "note")
        self.assertTrue(event.accepted)
        self.assertEqual(visual.plugin_alarm_editor_page_animation["from"], "main")
        self.assertEqual(visual.plugin_alarm_editor_page_animation["to"], "note")
        self.assertEqual(visual.plugin_alarm_editor_page_animation["started_at"], 91.0)

    def test_alarm_edit_opens_editor_prefilled_and_save_updates_same_row(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_rows = [
            {"time": "06:45", "label": "Gym", "enabled": False, "frequency": "workdays", "weekdays": "Mon-Fri"},
            {"time": "09:30", "label": "Standup", "enabled": True, "frequency": "daily", "weekdays": "Every day"},
        ]
        visual.update = lambda: None

        visual._activate_plugin_action("alarm-edit-0")

        editor = visual._time_alarm_editor_state()
        self.assertEqual(visual.plugin_time_mode, "alarm_editor")
        self.assertEqual(editor["hour"], 6)
        self.assertEqual(editor["minute"], 45)
        self.assertEqual(editor["note"], "Gym")
        self.assertEqual(editor["frequency"], "workdays")
        self.assertEqual(editor["weekdays"], "Mon-Fri")
        self.assertEqual(editor["edit_index"], 0)

        editor["hour"] = 7
        editor["minute"] = 5
        editor["note"] = "Stretch"
        visual.plugin_alarm_editor = editor
        visual._activate_plugin_action("alarm-editor-save")

        self.assertEqual(len(visual.plugin_alarm_rows), 2)
        self.assertEqual(visual.plugin_alarm_rows[0]["time"], "07:05")
        self.assertEqual(visual.plugin_alarm_rows[0]["label"], "Stretch")
        self.assertFalse(visual.plugin_alarm_rows[0]["enabled"])
        self.assertEqual(visual.plugin_alarm_rows[1]["label"], "Standup")

    def test_alarm_rows_load_from_persisted_settings_without_fake_defaults(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        rows = [
            {
                "time": "06:45",
                "label": "Gym",
                "enabled": True,
                "frequency_unit": "weekly",
                "weekdays": [1, 3, 5],
            }
        ]
        settings = {"v2_alarm_rows": json.dumps(rows, ensure_ascii=False)}
        visual.settings_getter = lambda key, default=None: settings.get(key, default)

        loaded = visual._time_alarm_rows()

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["time"], "06:45")
        self.assertEqual(loaded[0]["label"], "Gym")
        self.assertEqual(loaded[0]["weekdays"], [1, 3, 5])

        settings["v2_alarm_rows"] = ""
        visual.plugin_alarm_rows = [{"time": "15:22", "label": "示例", "enabled": True}]
        self.assertEqual(visual._time_alarm_rows(), [])

    def test_alarm_editor_save_persists_rows_for_provider(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        settings = {"v2_alarm_rows": ""}
        visual.settings_getter = lambda key, default=None: settings.get(key, default)
        visual.settings_setter = lambda key, value: settings.__setitem__(key, value)
        visual.plugin_alarm_rows = []
        visual.plugin_alarm_editor = {
            "hour": 7,
            "minute": 30,
            "note": "早课",
            "frequency_unit": "daily",
            "weekdays": [],
            "month_day": 28,
            "year_month": 4,
            "year_day": 28,
            "edit_index": None,
        }
        visual.update = lambda: None

        visual._activate_plugin_action("alarm-editor-save")

        payload = json.loads(settings["v2_alarm_rows"])
        self.assertEqual(payload[0]["time"], "07:30")
        self.assertEqual(payload[0]["label"], "早课")
        self.assertEqual(payload[0]["frequency_unit"], "daily")

    def test_alarm_toggle_and_delete_persist_rows_for_provider(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        rows = [{"time": "06:45", "label": "Gym", "enabled": True, "frequency_unit": "daily"}]
        settings = {"v2_alarm_rows": json.dumps(rows, ensure_ascii=False)}
        visual.settings_getter = lambda key, default=None: settings.get(key, default)
        visual.settings_setter = lambda key, value: settings.__setitem__(key, value)
        visual.update = lambda: None

        visual._activate_plugin_action("alarm-toggle-0")

        payload = json.loads(settings["v2_alarm_rows"])
        self.assertFalse(payload[0]["enabled"])

        visual._activate_plugin_action("alarm-delete-0")

        self.assertEqual(json.loads(settings["v2_alarm_rows"]), [])

    def test_alarm_reminder_due_respects_yearly_next_leap_day(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        now = datetime(2027, 3, 1, 8, 0, tzinfo=timezone.utc)

        due = visual._time_reminder_due_from_alarm(
            {
                "time": "09:00",
                "enabled": True,
                "frequency_unit": "yearly",
                "year_month": 2,
                "year_day": 29,
            },
            now,
        )

        self.assertEqual(due, datetime(2028, 2, 29, 9, 0, tzinfo=timezone.utc))

    def test_alarm_module_registers_row_edit_and_separate_toggle_targets(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        image = QImage(260, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(
            painter,
            QRectF(20.0, 20.0, 210.0, 100.0),
            {
                "alarms": [
                    {"time": "06:45", "label": "Gym", "enabled": False, "frequency": "workdays", "weekdays": "Mon-Fri"},
                ],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        edit_rect = visual.plugin_action_rects["alarm-edit-0"]
        toggle_rect = visual.plugin_action_rects["alarm-toggle-0"]
        self.assertLess(edit_rect.right(), toggle_rect.left())
        self.assertNotIn("alarm-edit-button-0", visual.plugin_action_rects)

    def test_alarm_module_hit_test_prioritizes_row_actions_over_list_backdrop(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.plugin_time_mode = "dashboard"
        visual.plugin_action_rects = {}
        visual._main_kind = lambda: IslandKind.PLUGIN
        image = QImage(260, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(
            painter,
            QRectF(20.0, 20.0, 210.0, 100.0),
            {
                "alarms": [
                    {"time": "06:45", "label": "Gym", "enabled": False, "frequency": "workdays", "weekdays": "Mon-Fri"},
                ],
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        edit_rect = visual.plugin_action_rects["alarm-edit-0"]
        toggle_rect = visual.plugin_action_rects["alarm-toggle-0"]
        self.assertEqual(visual._hit_test(edit_rect.center()), "plugin-action:alarm-edit-0")
        self.assertEqual(visual._hit_test(toggle_rect.center()), "plugin-action:alarm-toggle-0")

    def test_alarm_row_long_press_replaces_switch_with_delete_button(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_alarm_rows = [
            {"time": "06:45", "label": "Gym", "enabled": False, "frequency": "daily"},
            {"time": "07:30", "label": "Tea", "enabled": True, "frequency": "daily"},
        ]
        visual.update = lambda: None

        self.assertTrue(visual._handle_alarm_row_long_press("alarm-edit-0"))

        image = QImage(260, 150, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        visual._paint_time_alarm_module(painter, QRectF(20.0, 20.0, 210.0, 100.0), {"alarms": visual.plugin_alarm_rows}, QColor(255, 204, 0), visual._plugin_theme_colors())
        painter.end()

        self.assertEqual(visual.plugin_alarm_delete_index, 0)
        self.assertIn("alarm-delete-0", visual.plugin_action_rects)
        self.assertNotIn("alarm-toggle-0", visual.plugin_action_rects)
        delete = visual.plugin_action_rects["alarm-delete-0"]
        pixel = image.pixelColor(QPoint(int(delete.left() + visual._u(5.0)), int(delete.center().y())))
        self.assertGreater(pixel.red(), 160)
        self.assertLess(pixel.green(), 90)
        self.assertLess(pixel.blue(), 90)

        visual._activate_plugin_action("alarm-delete-0")

        self.assertEqual(len(visual.plugin_alarm_rows), 1)
        self.assertEqual(visual.plugin_alarm_rows[0]["time"], "07:30")
        self.assertIsNone(visual.plugin_alarm_delete_index)

    def test_alarm_list_scroll_clears_delete_mode(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_time_mode = "dashboard"
        visual.plugin_panel_last_width = 380.0
        visual.plugin_time_visible_module = "alarm"
        visual.plugin_alarm_delete_index = 0
        visual.plugin_alarm_list_scroll_index = 0.0
        visual.plugin_alarm_list_rect = QRectF(100.0, 50.0, 180.0, 70.0)
        visual.plugin_alarm_visible_range = (0, 4)
        visual.plugin_alarm_rows = [
            {"time": f"06:{index:02d}", "label": f"Alarm {index}", "enabled": True, "frequency": "daily"}
            for index in range(8)
        ]
        visual.update = lambda: None

        class FakeDelta:
            def y(self):
                return -120

        class FakeWheel:
            def angleDelta(self):
                return FakeDelta()

            def position(self):
                return QPointF(130.0, 70.0)

            def accept(self):
                pass

        self.assertTrue(visual._handle_time_panel_module_wheel(FakeWheel()))

        self.assertIsNone(visual.plugin_alarm_delete_index)

    def test_alarm_due_pending_is_captured_until_confirmed(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_due_pending = {}

        alarm_model = visual._time_plugin_panel_model(
            {
                "label": "TIME",
                "detail": "07:30",
                "summary": "07:30",
                "status": "alarm",
                "alert_title": "闹钟提醒",
                "alert_body": "晨会",
            }
        )
        idle_model = visual._time_plugin_panel_model(
            {"label": "TIME", "detail": "07:31", "summary": "05/29 Fri", "status": "idle"}
        )

        self.assertEqual(alarm_model["alarm_due_pending"]["label"], "晨会")
        self.assertEqual(idle_model["alarm_due_pending"]["label"], "晨会")

        visual.update = lambda: None
        visual._activate_plugin_action("alarm-due-confirm")

        self.assertEqual(getattr(visual, "plugin_alarm_due_pending", {}), {})

    def test_confirmed_alarm_due_is_not_recaptured_from_same_provider_alarm(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_due_pending = {}
        visual.update = lambda: None
        alarm_payload = {
            "label": "TIME",
            "detail": "07:31",
            "summary": "07:30",
            "status": "alarm",
            "alert_title": "闹钟提醒",
            "alert_body": "晨会",
        }

        first = visual._time_plugin_panel_model(alarm_payload)
        visual._activate_plugin_action("alarm-due-confirm")
        second = visual._time_plugin_panel_model(alarm_payload)
        new_alarm = visual._time_plugin_panel_model({**alarm_payload, "summary": "07:45", "alert_body": "站会"})

        self.assertEqual(first["alarm_due_pending"]["label"], "晨会")
        self.assertNotIn("alarm_due_pending", second)
        self.assertEqual(new_alarm["alarm_due_pending"]["label"], "站会")

    def test_alarm_due_pending_keeps_alarm_note_when_countdown_starts(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_due_pending = {"time": "07:30", "label": "晨会", "key": "07:30|晨会"}
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []

        model = visual._time_plugin_panel_model(
            {
                "label": "TIME",
                "detail": "00:52",
                "summary": "倒计时 4:58",
                "status": "alarm",
                "alert_title": "闹钟提醒",
                "alert_body": "倒计时 4:58",
                "progress": 0.16,
            }
        )

        self.assertEqual(model["alarm_due_pending"]["label"], "晨会")
        self.assertEqual(model["alarm_due_pending"]["time"], "07:30")
        self.assertEqual(getattr(visual, "plugin_alarm_due_pending", {})["label"], "晨会")

    def test_alarm_status_with_countdown_text_does_not_create_generic_alarm_side_alert(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_due_pending = {}
        visual.plugin_alarm_due_acknowledged_key = "07:30|晨会"
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []

        candidates = visual._time_side_alert_candidates(
            0,
            "TIME",
            "00:52",
            "倒计时 4:58",
            QColor(255, 184, 0),
            "T",
            "time",
            "alarm",
            "闹钟提醒",
            "倒计时 4:58",
            3,
        )

        self.assertNotIn("time:alarm", [candidate[8] for candidate in candidates])
        self.assertEqual([candidate[8] for candidate in candidates], ["time"])
        self.assertEqual(candidates[0][11], "4:58")

    def test_alarm_module_paints_due_confirmation_until_user_confirms(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        module_rect = QRectF(20.0, 20.0, 260.0, 132.0)
        image = QImage(340, 190, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(
            painter,
            module_rect,
            {
                "alarms": [{"time": "07:30", "label": "晨会", "enabled": True, "frequency": "daily"}],
                "alarm_due_pending": {"time": "07:30", "label": "晨会"},
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        self.assertIn("alarm-due-confirm", visual.plugin_action_rects)
        self.assertNotIn("alarm-add-open", visual.plugin_action_rects)
        self.assertNotIn("alarm-list", visual.plugin_action_rects)
        self.assertFalse(any(key.startswith(("alarm-toggle-", "alarm-edit-", "alarm-delete-")) for key in visual.plugin_action_rects))
        due = visual.plugin_action_rects["alarm-due-confirm"]
        self.assertGreaterEqual(due.width(), module_rect.width() - visual._u(24.0))
        self.assertGreaterEqual(due.height(), module_rect.height() - visual._u(48.0))
        self.assertTrue(due.contains(QPointF(module_rect.left() + visual._u(4.0), module_rect.top() + visual._u(4.0))))
        self.assertTrue(due.contains(QPointF(module_rect.right() - visual._u(4.0), module_rect.bottom() - visual._u(4.0))))

    def test_alarm_due_confirmation_hit_test_covers_whole_module(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.plugin_time_mode = "dashboard"
        visual._main_kind = lambda: IslandKind.PLUGIN
        visual.plugin_action_rects = {}
        module_rect = QRectF(20.0, 20.0, 260.0, 132.0)
        image = QImage(340, 190, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_alarm_module(
            painter,
            module_rect,
            {
                "alarms": [{"time": "07:30", "label": "晨会", "enabled": True, "frequency": "daily"}],
                "alarm_due_pending": {"time": "07:30", "label": "晨会"},
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        for point in (
            QPointF(module_rect.left() + visual._u(5.0), module_rect.top() + visual._u(5.0)),
            QPointF(module_rect.center()),
            QPointF(module_rect.right() - visual._u(5.0), module_rect.bottom() - visual._u(5.0)),
        ):
            self.assertEqual(visual._hit_test(point), "plugin-action:alarm-due-confirm")

    def test_pending_alarm_due_keeps_side_alert_until_confirmed(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_due_pending = {"time": "07:30", "label": "晨会", "key": "07:30|晨会"}
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []

        candidates = visual._time_side_alert_candidates(
            0,
            "TIME",
            "07:31",
            "05/29 Fri",
            QColor(255, 184, 0),
            "T",
            "time",
            "idle",
            "",
            "",
            50,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][8], "time:alarm")
        self.assertEqual(candidates[0][9], "alarm")
        self.assertEqual(candidates[0][11], "晨会")

    def test_done_alarm_note_candidate_uses_due_alarm_style(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []

        candidates = visual._time_side_alert_candidates(
            0,
            "TIME",
            "07:30",
            "05/29 Fri",
            QColor(255, 184, 0),
            "T",
            "time",
            "done",
            "闹钟提醒",
            "晨会",
            3,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][8], "time:alarm")
        self.assertEqual(candidates[0][9], "alarm")
        self.assertEqual(candidates[0][11], "晨会")

    def test_done_countdown_and_due_alarm_can_show_as_separate_time_side_alerts(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.plugin_samples = [
            ("TIME", "07:30", "计时结束", QColor(255, 184, 0), "T", "time", "alarm", "闹钟提醒", "晨会", 3, 1.0),
        ]

        alerts = visual._active_plugin_alerts(limit=2)

        self.assertEqual([alert[6] for alert in alerts], ["time:alarm", "time"])
        self.assertEqual([alert[7] for alert in alerts], ["alarm", "done"])
        self.assertEqual(alerts[0][9], "晨会")
        self.assertEqual(alerts[1][9], "时间到")

    def test_alarm_side_alert_stays_after_countdown_replaces_alarm_payload_summary(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.plugin_alarm_due_pending = {}
        visual.plugin_alarm_due_acknowledged_key = ""
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []
        visual.plugin_samples = [
            ("TIME", "07:31", "倒计时 4:58", QColor(255, 184, 0), "T", "time", "alarm", "闹钟提醒", "晨会", 3, 1.0),
        ]

        initial_alerts = visual._active_plugin_alerts(limit=2)
        visual.plugin_samples = [
            ("TIME", "07:32", "倒计时 4:12", QColor(255, 184, 0), "T", "time", "running", "计时进行中", "倒计时 4:12", 30, 0.2),
        ]
        next_alerts = visual._active_plugin_alerts(limit=2)

        self.assertEqual([alert[6] for alert in initial_alerts], ["time:alarm", "time"])
        self.assertEqual([alert[6] for alert in next_alerts], ["time:alarm", "time"])
        self.assertEqual(next_alerts[0][9], "晨会")
        self.assertEqual(next_alerts[1][9], "4:12")

    def test_default_alarm_side_alert_is_not_swallowed_by_running_countdown(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.plugin_alarm_due_pending = {}
        visual.plugin_alarm_due_acknowledged_key = ""
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []
        visual.plugin_samples = [
            ("TIME", "07:31", "倒计时 4:58", QColor(255, 184, 0), "T", "time", "alarm", "闹钟提醒", "闹钟", 3, 1.0),
        ]

        alerts = visual._active_plugin_alerts(limit=2)

        self.assertEqual([alert[6] for alert in alerts], ["time:alarm", "time"])
        self.assertEqual(alerts[0][9], "闹钟")
        self.assertEqual(alerts[1][9], "4:58")

    def test_due_alarm_bare_time_summary_does_not_create_countdown_side_alert(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.plugin_samples = [
            ("TIME", "00:21", "00:21", QColor(255, 184, 0), "T", "time", "alarm", "闹钟提醒", "看大夫", 3, 1.0),
        ]

        alerts = visual._active_plugin_alerts(limit=2)

        self.assertEqual([alert[6] for alert in alerts], ["time:alarm"])
        self.assertEqual(alerts[0][9], "看大夫")

    def test_new_done_alarm_note_replaces_pending_alarm_side_alert(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alarm_due_pending = {"time": "07:30", "label": "旧提醒", "key": "07:30|旧提醒"}
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []

        candidates = visual._time_side_alert_candidates(
            0,
            "TIME",
            "07:31",
            "05/29 Fri",
            QColor(255, 184, 0),
            "T",
            "time",
            "done",
            "闹钟提醒",
            "新提醒",
            3,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][8], "time:alarm")
        self.assertEqual(candidates[0][11], "新提醒")

    def test_time_panel_paint_registers_reference_actions_without_reminder_rail(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_action_rects = {}
        visual.plugin_time_mode = "dashboard"
        visual.plugin_alarm_rows = [{"time": "06:45", "label": "Gym", "enabled": True, "frequency_unit": "daily"}]
        visual._paint_plugin_panel_reminders = lambda _painter, _rect: (_ for _ in ()).throw(AssertionError("old reminder rail should not paint TIME"))
        image = QImage(460, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_structured_plugin_panel(
            painter,
            QRectF(20.0, 20.0, 420.0, 188.0),
            {
                "label": "TIME",
                "detail": "22:52",
                "summary": "05/27 Wed",
                "status": "idle",
                "color": QColor(255, 204, 0),
                "progress": 0.0,
            },
            QColor(255, 204, 0),
            visual._plugin_theme_colors(),
        )
        painter.end()

        for command in ("timer-start-5", "timer-start-15", "timer-start-10", "timer-start-20", "alarm-toggle-0", "alarm-add-open"):
            self.assertIn(command, visual.plugin_action_rects)

    def test_weather_and_agenda_panel_sections_are_functional(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        weather = visual._plugin_panel_sections(
            {
                "label": "WEATHER",
                "detail": "26°C 晴",
                "summary": "北京 · 东南风12km/h · 湿度 40%",
                "status": "normal",
                "alert_title": "",
                "alert_body": "",
            }
        )
        agenda = visual._plugin_panel_sections(
            {
                "label": "AGENDA",
                "detail": "28 分钟后",
                "summary": "准备上课",
                "status": "soon",
                "alert_title": "日程将近",
                "alert_body": "28 分钟后 · 准备上课",
            }
        )

        self.assertEqual([section["id"] for section in weather], ["current", "metrics", "source"])
        self.assertIn("风", weather[1]["body"])
        self.assertEqual([section["id"] for section in agenda], ["next", "todo", "reminder"])
        self.assertIn("课表", agenda[1]["body"])

    def test_plugin_panel_reminder_models_include_active_alerts(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = True
        visual.enabled_plugin_count = 3
        visual.plugin_panel_open = True
        visual.plugin_samples = [
            ("TIME", "00:24", "倒计时 0:24", QColor(67, 162, 255), "●", "time", "running", "计时进行中", "倒计时 0:24", 30, 0.4),
            ("WEATHER", "雷雨", "北京", QColor(80, 210, 255), "☁", "weather", "severe", "严重天气提醒", "北京 · 雷雨", 2, 1.0),
            ("AGENDA", "4 分钟后", "准备上课", QColor(118, 236, 160), "■", "agenda", "soon", "日程将近", "4 分钟后 · 准备上课", 10, 0.6),
        ]

        reminders = visual._plugin_panel_reminder_models(limit=3)

        self.assertEqual([item["plugin_id"] for item in reminders], ["weather", "agenda", "time"])
        self.assertIn("雷雨", reminders[0]["body"])

    def test_time_statuses_show_side_alerts_until_plugin_panel_expands(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 2
        visual.plugin_samples = [
            ("TIME", "00:24", "倒计时 0:24", QColor(67, 162, 255), "●", "time", "running", "计时进行中", "倒计时 0:24", 30, 0.42),
            ("WEATHER", "北京", "天气刷新中", QColor(80, 210, 255), "☁", "weather", "loading", "天气刷新中", "北京", 40, 0.0),
        ]

        alerts = visual._active_plugin_alerts(limit=3, include_expanded=True)

        self.assertEqual([alert[6] for alert in alerts], ["time", "weather"])

        visual.expanded = True
        visual.plugin_panel_open = True
        visual._main_kind = lambda: IslandKind.PLUGIN
        side_alerts = visual._active_plugin_alerts(limit=3)
        reminders = visual._plugin_panel_reminder_models(limit=3)

        self.assertEqual([alert[6] for alert in side_alerts], [])
        self.assertEqual([item["plugin_id"] for item in reminders], ["time", "weather"])

    def test_time_reminder_icon_paths_use_local_svg_assets(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        running = visual._time_reminder_icon_path("running", "countdown")
        done = visual._time_reminder_icon_path("done", "alarm")

        self.assertIn("assets", running)
        self.assertIn("logos", running)
        self.assertIn("assets", done)
        self.assertIn("logos", done)
        self.assertTrue(running.endswith("倒计时黄.svg"))
        self.assertTrue(done.endswith("闹钟黑.svg"))

    def test_time_reminder_display_text_caps_long_notes(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        text = visual._time_reminder_display_text("prepare materials and stretch the note across the island")

        self.assertLessEqual(len(text), 18)
        self.assertTrue(text.endswith("..."))

    def test_time_alert_painter_does_not_use_generic_status_dot(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enabled_plugin_count = 1
        visual.scroll_phase = 0.0
        visual.plugin_samples = [
            ("TIME", "04:20", "Countdown 4:20", QColor(255, 184, 0), "", "time", "running", "Timer running", "Countdown 4:20", 10, 0.4),
        ]
        visual._paint_plugin_status_dot = lambda *_args: (_ for _ in ()).throw(AssertionError("TIME alert must not paint generic status dot"))
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin_alert(painter, QRectF(12.0, 12.0, 220.0, 42.0), "plugin-alert:time")
        painter.end()

    def test_time_alert_width_is_capped_for_long_side_content(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        width = visual._plugin_alert_width_for("TIME", "07:30", "alarm note that is far too long for the island")
        agenda_width = visual._plugin_alert_width_for("AGENDA", "07:30", "alarm note that is far too long for the island")

        self.assertLessEqual(width, 168.0 * IslandStackVisual.VISUAL_SCALE)
        self.assertGreater(agenda_width, width)

    def test_time_alert_rect_ignores_generic_width_growth_for_time(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.plugin_alert_width = 240.0
        visual.persistent_plugin = True
        visual.expanded = False
        visual.enabled_plugin_count = 1
        visual.plugin_samples = [
            ("TIME", "07:30", "alarm note that is far too long for the island", QColor(255, 184, 0), "", "time", "running", "alarm note that is far too long for the island", "alarm 07:30", 10, 0.4),
        ]
        visual._screen_bounds_in_local = lambda: (0.0, 620.0)
        visual._main_rect_screen_pressure = lambda *_args: ""
        visual.height = lambda: 260

        rect = visual._plugin_alert_rect(QRectF(160.0, 82.0, 180.0, 48.0), 0, 1)

        self.assertLessEqual(rect.width(), 168.0 * IslandStackVisual.VISUAL_SCALE)

    def test_time_side_countdown_alert_uses_static_text_not_scrolling(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        calls = []
        visual._paint_scrolling_text = lambda _painter, _rect, text, *_args, **_kwargs: calls.append(str(text))
        image = QImage(240, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 156.0, 42.0),
            "TIME",
            "10:11:08",
            "Countdown 4:47",
            QColor(255, 184, 0),
            "running",
            "Timer running",
            "Countdown 4:47",
            0.4,
        )
        painter.end()

        self.assertEqual(calls, [])

    def test_time_side_countdown_alert_only_paints_icon_and_value_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        static_texts = []
        visual._paint_static_elided_text = lambda _painter, _rect, text, *_args, **_kwargs: static_texts.append(str(text))
        image = QImage(240, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 118.0, 42.0),
            "TIME",
            "10:11:08",
            "Countdown 4:59",
            QColor(255, 184, 0),
            "running",
            "Timer running",
            "Countdown 4:59",
            0.4,
        )
        painter.end()

        self.assertEqual(static_texts, ["4:59"])

    def test_time_side_alert_text_is_two_sizes_smaller_but_bold(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        static_calls = []
        done_calls = []
        scroll_calls = []
        visual._paint_static_elided_text = lambda _painter, _rect, text, size, _color, bold, *_args, **_kwargs: static_calls.append((str(text), int(size), bool(bold)))
        visual._paint_time_done_alert_text = lambda _painter, _rect, text, _color, base_size=23, boost=2.0: done_calls.append((str(text), int(base_size), float(boost)))
        visual._paint_scrolling_text = lambda _painter, _rect, text, size, _color, bold, *_args, **_kwargs: scroll_calls.append((str(text), int(size), bool(bold)))
        image = QImage(260, 90, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 156.0, 42.0),
            "TIME",
            "10:11:08",
            "Countdown 4:59",
            QColor(255, 184, 0),
            "running",
            "Timer running",
            "Countdown 4:59",
            0.4,
        )
        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 50.0, 118.0, 42.0),
            "TIME",
            "0:00",
            "Countdown 0:00",
            QColor(255, 184, 0),
            "done",
            "Timer done",
            "Countdown 0:00",
            1.0,
        )
        painter.end()

        self.assertIn(("4:59", 20, True), static_calls)
        self.assertIn(("时间到", 20, True), static_calls)
        self.assertEqual(done_calls, [])
        self.assertEqual(scroll_calls, [])

    def test_alarm_note_with_done_status_still_uses_alarm_side_alert_style(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        done_texts = []
        scroll_texts = []
        visual._paint_time_done_alert_text = lambda _painter, _rect, text, *_args, **_kwargs: done_texts.append(str(text))
        static_texts = []
        visual._paint_static_elided_text = lambda _painter, _rect, text, *_args, **_kwargs: static_texts.append(str(text))
        visual._paint_alarm_scrolling_text = lambda _painter, _rect, text, *_args, **_kwargs: scroll_texts.append(str(text))
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 168.0, 42.0),
            "TIME",
            "07:30",
            "计时结束",
            QColor(255, 184, 0),
            "done",
            "计时结束",
            "所爱衬所爱",
            1.0,
        )
        painter.end()

        self.assertEqual(static_texts, ["所爱衬所爱"])
        self.assertEqual(scroll_texts, [])
        self.assertEqual(done_texts, [])

    def test_alarm_side_alert_long_note_uses_unified_static_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        calls = []
        alarm_scrolls = []
        visual._paint_scrolling_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("alarm side alert should not use generic scrolling text"))
        visual._paint_alarm_scrolling_text = lambda _painter, _rect, text, *_args, **_kwargs: alarm_scrolls.append(str(text))
        visual._paint_static_elided_text = lambda _painter, _rect, text, size, color, bold, *_args, **_kwargs: calls.append((str(text), int(size), QColor(color), bool(bold)))
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 168.0, 42.0),
            "TIME",
            "07:30",
            "alarm 07:30",
            QColor(255, 184, 0),
            "running",
            "alarm note that is far too long",
            "alarm 07:30",
            0.4,
        )
        painter.end()

        self.assertEqual(len(calls), 1)
        self.assertIn("alarm", calls[0][0].lower())
        self.assertEqual(calls[0][1], 20)
        self.assertTrue(calls[0][3])
        self.assertEqual(alarm_scrolls, [])

    def test_plugin_alert_exit_rect_shrinks_towards_main_island(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        main = StackVisual("music", IslandKind.MUSIC, QRectF(160.0, 82.0, 420.0, 48.0), False, None, 10)
        right_alert = QRectF(600.0, 85.0, 156.0, 42.0)
        left_alert = QRectF(0.0, 85.0, 126.0, 42.0)

        right_target = visual._plugin_alert_exit_rect_towards_main(right_alert, [main])
        left_target = visual._plugin_alert_exit_rect_towards_main(left_alert, [main])

        self.assertLessEqual(right_target.width(), visual._u(12.0))
        self.assertLess(right_target.center().x(), right_alert.center().x())
        self.assertAlmostEqual(right_target.left(), main.rect.right() + visual._u(4.0), delta=1.0)
        self.assertLessEqual(left_target.width(), visual._u(12.0))
        self.assertGreater(left_target.center().x(), left_alert.center().x())
        self.assertAlmostEqual(left_target.right(), main.rect.left() - visual._u(4.0), delta=1.0)

    def test_time_side_alert_renderer_has_no_nested_value_chip(self):
        source = inspect.getsource(IslandStackVisual._paint_time_plugin_alert)

        self.assertNotIn("drawRoundedRect(value_rect", source)
        self.assertNotIn("drawRoundedRect(due_rect", source)
        self.assertNotIn("mini_icon", source)

    def test_time_done_side_alert_uses_same_icon_and_text_pill_as_waiting_state(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        symbols = []
        visual._paint_time_symbol = lambda _painter, _rect, kind, *_args, **_kwargs: symbols.append(str(kind))
        static_texts = []
        done_texts = []
        visual._paint_static_elided_text = lambda _painter, _rect, text, *_args, **_kwargs: static_texts.append(str(text))
        visual._paint_time_done_alert_text = lambda _painter, _rect, text, *_args, **_kwargs: done_texts.append(str(text))
        image = QImage(240, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 118.0, 42.0),
            "TIME",
            "0:00",
            "Countdown 0:00",
            QColor(255, 184, 0),
            "done",
            "Timer done",
            "Countdown 0:00",
            1.0,
        )
        painter.end()

        self.assertEqual(symbols, ["countdown"])
        self.assertEqual(static_texts, ["\u65f6\u95f4\u5230"])
        self.assertEqual(done_texts, [])

    def test_time_side_alarm_note_uses_same_static_text_channel_as_countdown(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        calls = []
        visual._paint_scrolling_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("alarm side alert should not use generic scrolling text"))
        visual._paint_alarm_scrolling_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("alarm side alert should not use focus-character scrolling"))
        visual._paint_static_elided_text = lambda _painter, _rect, text, *_args, **_kwargs: calls.append(str(text))
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 168.0, 42.0),
            "TIME",
            "07:30",
            "alarm 07:30",
            QColor(255, 184, 0),
            "running",
            "alarm note that is far too long",
            "alarm 07:30",
            0.4,
        )
        painter.end()

        self.assertEqual(len(calls), 1)
        self.assertIn("alarm", calls[0].lower())
        self.assertNotIn("07:30", calls)

    def test_alarm_side_alert_uses_note_or_default_alarm_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        self.assertEqual(visual._time_alarm_alert_text("下午茶时间很长"), "下午茶时间很长")
        self.assertEqual(visual._time_alarm_alert_text(""), "闹钟")

    def test_alarm_side_display_setting_can_show_time_instead_of_note(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = [
            {"time": "23:59", "label": "Yoga", "enabled": True, "frequency_unit": "daily"},
        ]
        visual.settings_getter = lambda key, default=None: "time" if key == "v2_time_alarm_side_display" else default

        candidates = visual._time_side_alert_candidates(
            0,
            "TIME",
            "10:11",
            "05/29 Fri",
            QColor(255, 184, 0),
            "T",
            "time",
            "idle",
            "",
            "",
            50,
        )

        alarm_candidates = [candidate for candidate in candidates if candidate[8] == "time:alarm"]
        self.assertEqual(len(alarm_candidates), 1)
        self.assertEqual(alarm_candidates[0][4], "23:59")
        self.assertEqual(alarm_candidates[0][11], "23:59")

    def test_alarm_side_alert_width_fits_at_least_five_chars(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)

        width = visual._time_plugin_alert_width_for("07:30", "闹钟")

        self.assertGreaterEqual(width, visual._u(150.0))

    def test_alarm_done_side_alert_uses_static_note_not_focus_character_scroll(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        static_texts = []
        visual._paint_static_elided_text = lambda _painter, _rect, text, *_args, **_kwargs: static_texts.append(str(text))
        visual._paint_time_done_alert_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("due alarm should not use countdown done text"))
        visual._paint_alarm_scrolling_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("due alarm should not use focus-character scrolling"))
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 168.0, 42.0),
            "TIME",
            "07:30",
            "alarm 07:30",
            QColor(255, 184, 0),
            "alarm",
            "闹钟提醒",
            "下午茶",
            1.0,
        )
        painter.end()

        self.assertEqual(static_texts, ["下午茶"])

    def test_time_compact_countdown_is_static_but_long_alarm_note_scrolls(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        visual._time_compact_segments = lambda *_args, **_kwargs: [
            {"kind": "time", "text": "10:11", "status": "normal"},
            {"kind": "countdown", "text": "4:47", "status": "running"},
            {"kind": "alarm", "text": "alarm note that is far too long", "status": "alarm"},
        ]
        calls = []
        visual._paint_scrolling_text = lambda _painter, _rect, text, *_args, **_kwargs: calls.append(str(text))
        image = QImage(320, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_compact_cell(painter, QRectF(8.0, 8.0, 286.0, 36.0), "10:11", "", QColor(255, 184, 0), True, "running", 0.4)
        painter.end()

        self.assertEqual(len(calls), 1)
        self.assertIn("alarm", calls[0].lower())
        self.assertNotIn("4:47", calls)

    def test_time_compact_layout_reserves_seconds_width_and_fixed_reminder_chips(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.show_seconds = True
        segments = [
            {"kind": "time", "text": "10:11:08", "status": "normal"},
            {"kind": "countdown", "text": "4:59", "status": "running"},
            {"kind": "alarm", "text": "Gym", "status": "alarm"},
        ]

        layout = visual._time_compact_cell_layout(QRectF(8.0, 8.0, 250.0, 36.0), segments)

        self.assertGreaterEqual(layout[0]["rect"].width(), visual._u(82.0))
        self.assertAlmostEqual(layout[1]["rect"].width(), visual._u(66.0), delta=2.0)
        self.assertAlmostEqual(layout[2]["rect"].width(), visual._u(74.0), delta=2.0)
        self.assertFalse(layout[1]["icon_only"])
        self.assertFalse(layout[2]["icon_only"])

    def test_time_compact_layout_expands_long_countdown_value(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.font_shrink_scale = 1.0
        visual.ui_font_family = IslandStackVisual.best_available_font_family()
        segments = [
            {"kind": "time", "text": "10:11", "status": "normal"},
            {"kind": "countdown", "text": "1:02:03", "status": "running"},
        ]

        layout = visual._time_compact_cell_layout(QRectF(8.0, 8.0, 360.0, 36.0), segments)

        countdown_rect = next(item["rect"] for item in layout if item["kind"] == "countdown")
        font_metrics = QFontMetrics(visual._font(10, QFont.Weight.Bold))
        text_rect_width = countdown_rect.width() - visual._u(6.0 + 20.0 + 4.0 + 7.0)
        available_width = max(1, int(text_rect_width - visual._u(4.0)))
        self.assertEqual(
            font_metrics.elidedText("1:02:03", Qt.TextElideMode.ElideRight, available_width),
            "1:02:03",
        )

    def test_time_compact_layout_collapses_reminders_to_circles_when_space_is_tight(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.show_seconds = True
        segments = [
            {"kind": "time", "text": "10:11:08", "status": "normal"},
            {"kind": "countdown", "text": "4:59", "status": "running"},
            {"kind": "alarm", "text": "Gym", "status": "alarm"},
        ]

        layout = visual._time_compact_cell_layout(QRectF(8.0, 8.0, 174.0, 36.0), segments)

        self.assertEqual([item["kind"] for item in layout], ["time", "countdown", "alarm"])
        self.assertTrue(layout[1]["icon_only"])
        self.assertTrue(layout[2]["icon_only"])
        self.assertAlmostEqual(layout[1]["rect"].width(), layout[1]["rect"].height(), delta=1.0)
        self.assertAlmostEqual(layout[2]["rect"].width(), layout[2]["rect"].height(), delta=1.0)

    def test_time_compact_future_alarm_segment_uses_waiting_state_not_due_alarm_state(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = [
            {"time": "23:59", "label": "拉伸", "enabled": True, "frequency_unit": "daily"},
        ]
        now = datetime(2026, 5, 31, 10, 11, 8).astimezone()

        segments = visual._time_compact_segments("10:11", "05/31 Sun", "idle", now=now)

        alarm = next(segment for segment in segments if segment["kind"] == "alarm")
        self.assertEqual(alarm["status"], "running")

    def test_time_compact_idle_date_summary_does_not_create_countdown_segment(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []
        now = datetime(2026, 5, 28, 5, 7, 12).astimezone()

        segments = visual._time_compact_segments("05:07", "05/28 Thu", "idle", now=now)

        self.assertEqual(segments, [{"kind": "time", "text": "05:07", "due_at": now}])

    def test_time_compact_idle_cell_does_not_paint_fake_countdown_icon(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.show_seconds = False
        visual.plugin_countdown_entries = []
        visual.plugin_alarm_rows = []
        symbol_calls = []
        visual._paint_time_symbol = lambda *_args: symbol_calls.append(_args)
        image = QImage(240, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_compact_cell(painter, QRectF(8.0, 8.0, 210.0, 36.0), "05:07", "05/28 Thu", QColor(255, 184, 0), True, "idle", 0.0)
        painter.end()

        self.assertEqual(symbol_calls, [])

    def test_time_side_alert_running_icon_uses_larger_reference_scale(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        symbols = []
        visual._paint_time_symbol = lambda _painter, rect, kind, color, *_args: symbols.append((QRectF(rect), kind, QColor(color)))
        image = QImage(240, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 156.0, 42.0),
            "TIME",
            "10:11:08",
            "Countdown 4:47",
            QColor(255, 184, 0),
            "running",
            "Timer running",
            "Countdown 4:47",
            0.4,
        )
        painter.end()

        self.assertEqual(symbols[0][1], "countdown")
        self.assertGreaterEqual(symbols[0][0].width(), visual._u(30.0))

    def test_time_side_alert_circular_waiting_icon_is_centered_without_text(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        symbols = []
        static_texts = []
        alarm_scrolls = []
        visual._paint_time_symbol = lambda _painter, rect, kind, color, *_args: symbols.append((QRectF(rect), kind, QColor(color)))
        visual._paint_static_elided_text = lambda _painter, _rect, text, *_args, **_kwargs: static_texts.append(str(text))
        visual._paint_alarm_scrolling_text = lambda _painter, _rect, text, *_args, **_kwargs: alarm_scrolls.append(str(text))
        visual._paint_plugin_progress_ring = lambda *_args, **_kwargs: None
        image = QImage(90, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRectF(12.0, 12.0, 42.0, 42.0)

        visual._paint_time_plugin_alert(
            painter,
            rect,
            "TIME",
            "07:30",
            "晨会",
            QColor(255, 184, 0),
            "running",
            "闹钟",
            "晨会",
            0.4,
            "alarm",
        )
        painter.end()

        self.assertEqual(symbols[0][1], "alarm")
        self.assertAlmostEqual(symbols[0][0].center().x(), rect.center().x(), delta=0.5)
        self.assertAlmostEqual(symbols[0][0].center().y(), rect.center().y(), delta=0.5)
        self.assertEqual(static_texts, [])
        self.assertEqual(alarm_scrolls, [])

    def test_time_side_alert_circular_done_uses_yellow_background_with_black_countdown_icon(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        symbols = []
        visual._paint_time_symbol = lambda _painter, rect, kind, color, *_args: symbols.append((QRectF(rect), kind, QColor(color)))
        visual._paint_static_elided_text = lambda *_args: (_ for _ in ()).throw(AssertionError("circular done fallback should be icon-only"))
        image = QImage(90, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 42.0, 42.0),
            "TIME",
            "0:00",
            "Countdown 0:00",
            QColor(255, 184, 0),
            "done",
            "Timer done",
            "Countdown 0:00",
            1.0,
        )
        painter.end()

        self.assertEqual(symbols[0][1], "countdown")
        self.assertLess(symbols[0][2].red(), 70)
        self.assertLess(symbols[0][2].green(), 70)
        self.assertLess(symbols[0][2].blue(), 70)

    def test_time_rectangular_side_alert_does_not_paint_ring_animation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.scroll_phase = 0.0
        visual._paint_plugin_progress_ring = lambda *_args: (_ for _ in ()).throw(AssertionError("rectangular side alert should not paint progress ring"))
        image = QImage(240, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_time_plugin_alert(
            painter,
            QRectF(12.0, 12.0, 156.0, 42.0),
            "TIME",
            "10:11:08",
            "Countdown 4:47",
            QColor(255, 184, 0),
            "running",
            "Timer running",
            "Countdown 4:47",
            0.4,
        )
        painter.end()

    def test_generic_rectangular_side_alert_does_not_paint_ring_animation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enabled_plugin_count = 1
        visual.scroll_phase = 0.0
        visual.plugin_samples = [
            ("WEATHER", "雷雨", "北京", QColor(80, 210, 255), "☁", "weather", "severe", "严重天气提醒", "北京 · 雷雨", 2, 1.0),
        ]
        visual._paint_plugin_progress_ring = lambda *_args: (_ for _ in ()).throw(AssertionError("rectangular side alert should not paint progress ring"))
        image = QImage(260, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin_alert(painter, QRectF(12.0, 12.0, 180.0, 42.0), "plugin-alert:weather")
        painter.end()

    def test_narrow_non_circular_side_alert_does_not_paint_ring_animation(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enabled_plugin_count = 1
        visual.scroll_phase = 0.0
        visual.plugin_samples = [
            ("WEATHER", "雷雨", "北京", QColor(80, 210, 255), "☁", "weather", "severe", "严重天气提醒", "北京 · 雷雨", 2, 1.0),
        ]
        visual._paint_plugin_progress_ring = lambda *_args: (_ for _ in ()).throw(AssertionError("only circular fallback should paint progress ring"))
        image = QImage(120, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin_alert(painter, QRectF(12.0, 12.0, 48.0, 42.0), "plugin-alert:weather")
        painter.end()

    def test_circular_boundary_side_alert_keeps_ring_animation_for_all_plugins(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.enabled_plugin_count = 2
        visual.scroll_phase = 0.0
        visual.plugin_samples = [
            ("TIME", "10:11:08", "Countdown 4:47", QColor(255, 184, 0), "", "time", "running", "Timer running", "Countdown 4:47", 10, 0.4),
            ("WEATHER", "雷雨", "北京", QColor(80, 210, 255), "☁", "weather", "severe", "严重天气提醒", "北京 · 雷雨", 2, 1.0),
        ]
        calls = []
        visual._paint_plugin_progress_ring = lambda *_args: calls.append(_args)
        image = QImage(140, 120, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin_alert(painter, QRectF(10.0, 10.0, 42.0, 42.0), "plugin-alert:time")
        visual._paint_plugin_alert(painter, QRectF(64.0, 10.0, 42.0, 42.0), "plugin-alert:weather")
        painter.end()

        self.assertGreaterEqual(len(calls), 2)

    def test_generic_plugin_alert_renderer_has_no_time_redundant_branch(self):
        source = inspect.getsource(IslandStackVisual._paint_plugin_alert)

        self.assertNotIn("is_time_done", source)
        self.assertNotIn("_time_reminder_icon_path", source)

    def test_generic_plugin_focus_cell_has_no_time_redundant_branch(self):
        source = inspect.getsource(IslandStackVisual._paint_plugin_focus_cell)

        self.assertNotIn("is_time_done", source)
        self.assertNotIn("_time_reminder_icon_path", source)

    def test_time_focus_cell_uses_summary_and_progress_for_full_countdown(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        calls = []
        visual._paint_time_compact_cell = lambda _painter, _rect, detail, summary, _color, _active, status, progress: calls.append(
            (str(detail), str(summary), str(status), float(progress))
        )
        image = QImage(300, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin_focus_cell(
            painter,
            QRectF(8.0, 8.0, 250.0, 36.0),
            "TIME",
            "10:11:08",
            QColor(255, 184, 0),
            "T",
            1.0,
            False,
            "running",
            summary="倒计时 1:02:03",
            progress=0.62,
        )
        painter.end()

        self.assertEqual(calls, [("10:11:08", "倒计时 1:02:03", "running", 0.62)])

    def test_time_focused_plugin_strip_keeps_full_countdown_when_seconds_hidden(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.expanded = True
        visual.show_seconds = False
        visual.enabled_plugin_count = 1
        visual.plugin_focus = 0
        visual.plugin_focus_from = 0
        visual.plugin_focus_target = 0
        visual.plugin_focus_changed_at = 0.0
        visual.hovered_key = "plugin"
        visual.plugin_panel_open = True
        visual.plugin_samples = [
            ("TIME", "10:11:08", "倒计时 1:02:03", QColor(255, 184, 0), "T", "time", "running", "计时进行中", "倒计时 1:02:03", 30, 0.62),
        ]
        visual._plugin_panel_visible = lambda: True
        visual._active_plugin_focus = lambda _now: 0
        visual._plugin_focus_weights = lambda *_args, **_kwargs: [1.0]
        visual._plugin_item_rects = lambda rect, _now, expand_active=True: [(0, QRectF(rect))]
        visual._paint_plugin_solid_shell = lambda *_args, **_kwargs: None
        calls = []
        visual._paint_plugin_focus_cell = lambda _painter, _rect, label, detail, _color, _icon, _focus, _icon_mode, status, summary="", progress=0.0: calls.append(
            (str(label), str(detail), str(summary), str(status), float(progress))
        )
        image = QImage(340, 80, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin(painter, QRectF(20.0, 20.0, 260.0, 48.0), False)
        painter.end()

        self.assertEqual(calls, [("TIME", "10:11:08", "倒计时 1:02:03", "running", 0.62)])

    def test_time_plugin_panel_focus_model_keeps_full_countdown_when_seconds_hidden(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.show_seconds = False
        visual.enabled_plugin_count = 1
        visual.plugin_samples = [
            ("TIME", "10:11:08", "倒计时 1:02:03", QColor(255, 184, 0), "T", "time", "running", "计时进行中", "倒计时 1:02:03", 30, 0.62),
        ]
        visual._active_plugin_focus = lambda _now: 0

        model = visual._plugin_panel_focus_model()

        self.assertEqual(model["summary"], "倒计时 1:02:03")
        self.assertEqual(model["progress"], 0.62)

    def test_time_compact_segments_align_seconds_and_sort_by_due_time(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        now = datetime(2026, 5, 28, 10, 11, 8).astimezone()
        visual.show_seconds = False
        visual.plugin_countdown_entries = [
            {
                "text": "4:59",
                "end_at": (now + timedelta(minutes=5)).isoformat(timespec="seconds"),
                "status": "running",
            }
        ]
        visual.plugin_alarm_rows = [
            {"time": "10:13", "label": "Note", "enabled": True, "frequency": "daily"},
        ]

        segments = visual._time_compact_segments("10:11:08", "Countdown 4:59", "running", now=now)

        self.assertEqual(segments[0]["kind"], "time")
        self.assertEqual(segments[0]["text"], "10:11")
        self.assertEqual([segment["kind"] for segment in segments[1:]], ["alarm", "countdown"])
        self.assertEqual(segments[1]["text"], "Note")

        visual.show_seconds = True
        self.assertEqual(visual._time_compact_segments("10:11:08", "", "idle", now=now)[0]["text"], "10:11:08")

    def test_time_plugin_compact_cell_uses_time_specific_segments(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        called = []
        visual._paint_time_compact_cell = lambda *args: called.append(args)
        image = QImage(240, 70, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)

        visual._paint_plugin_compact_cell(painter, QRectF(8.0, 8.0, 210.0, 36.0), "TIME", "10:11", "Countdown 4:59", QColor(255, 184, 0), True, "running", 0.4)
        painter.end()

        self.assertTrue(called)

    def test_plugin_action_click_refreshes_weather(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        calls = []
        visual.settings_setter = lambda key, value: calls.append((key, value))
        visual.refresh_plugin_data = lambda: calls.append(("refresh", "plugins"))

        visual._activate_plugin_action("weather-refresh")

        self.assertEqual(calls[-1], ("refresh", "plugins"))
        self.assertTrue(any(key == "v2_weather_refresh_nonce" for key, _value in calls if key != "refresh"))


if __name__ == "__main__":
    unittest.main()
