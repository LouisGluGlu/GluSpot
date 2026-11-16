from dataclasses import replace
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.models import TimedLyricLine, TimedLyricUnit
from local.apple_lyric_style import (
    AppleKnobs, blur_layers, displayed_focus, first_unit_start,
    line_appearance, row_timing, scroll_spring_phase, unit_progress,
)
from local.spring_motion import WIDE_NARROW_SPRING
from local.settings_store import DEFAULT_SETTINGS, SettingsManager
from tests.apple_lyric_fixture import LINES, make_surface, render


class AppleClockTests(unittest.TestCase):
    def test_knob_names_match_sample(self):
        self.assertTrue(set(("block_gap_ratio", "translation_gap_ratio", "other_scale", "blur_px",
            "unread_alpha", "feather_chars", "scroll_ms", "lead_ms")).issubset(AppleKnobs.__dataclass_fields__))

    def test_next_first_unit_owns_scroll_start_not_line_stamp_or_previous_end(self):
        starts = (first_unit_start(.4, LINES[0].units, "unit"), first_unit_start(3.0, LINES[1].units, "unit"))
        knobs = AppleKnobs()
        self.assertEqual(starts, (.4, 3.8))
        self.assertEqual(WIDE_NARROW_SPRING.damping_ratio, 1.0)
        self.assertEqual(displayed_focus(starts, 3.449, knobs), 0)
        # Lead stays 350 ms. Travel is the wide/narrow spring, not smoothstep.
        self.assertAlmostEqual(displayed_focus(starts, 3.625, knobs), scroll_spring_phase(0.175))
        self.assertAlmostEqual(displayed_focus(starts, 3.8, knobs), scroll_spring_phase(0.35))
        self.assertLess(displayed_focus(starts, 3.8, knobs), 1.0)

    def test_long_gap_does_not_scroll_at_previous_line_end(self):
        starts = (.4, 3.8, 9.7, 12.6)
        for position in (6.45, 7.2, 9.349):
            self.assertEqual(displayed_focus(starts, position, AppleKnobs()), 1)
        self.assertGreater(displayed_focus(starts, 9.36, AppleKnobs()), 1)

    def test_unit_wipe_uses_audio_time_while_visual_focus_leads(self):
        plan = row_timing("夜色把窗照得慢", LINES[0].units, "unit", start=.4, end=3.8)
        unit = plan.display_units[-1]
        self.assertEqual((unit.start_seconds, unit.end_seconds), (2.2, 4.55))
        self.assertAlmostEqual(unit_progress(unit, 3.35), (3.35 - 2.2) / (4.55 - 2.2))
        for lead in (0, 350, 1200):
            knobs = replace(AppleKnobs(), lead_ms=lead)
            displayed_focus((.4, 3.8), 3.625, knobs)
            self.assertAlmostEqual(unit_progress(unit, 3.625), (3.625 - 2.2) / 2.35)
        self.assertEqual(unit_progress(unit, 8), 1)

    def test_line_timing_is_equal_characters_and_estimated(self):
        plan = row_timing("Ab慢e\u0301", kind="line", start=10, end=14)
        self.assertEqual(plan.quality, "estimated")
        self.assertEqual(tuple(unit.text for unit in plan.display_units), ("A", "b", "慢", "e\u0301"))
        self.assertEqual(tuple((unit.start_seconds, unit.end_seconds) for unit in plan.display_units),
                         ((10, 11), (11, 12), (12, 13), (13, 14)))
        self.assertTrue(all(unit.estimated and unit.timing_origin == "line" for unit in plan.display_units))
        self.assertEqual(first_unit_start(10, LINES[0].units, "line"), 10)

    def test_missing_end_is_display_estimate_and_source_remains_unknown(self):
        source = (TimedLyricUnit(1, None, "slow "), TimedLyricUnit(3, 4, "word"))
        plan = row_timing("slow word", source, "unit", start=1, end=6)
        self.assertIsNone(source[0].end_seconds)
        self.assertEqual(plan.display_units[0].end_seconds, 3)
        self.assertTrue(plan.display_units[0].estimated)
        self.assertEqual(plan.display_units[0].timing_origin, "missing-end")
        self.assertFalse(plan.display_units[1].estimated)
        unknown = row_timing("tail", (TimedLyricUnit(4, None, "tail"),), "unit", start=4, end=None)
        self.assertIsNone(unknown.display_units[0].end_seconds)
        self.assertEqual(unit_progress(unknown.display_units[0], 20), 0)

    def test_knobs_control_clock_scale_blur_and_alpha(self):
        base = AppleKnobs()
        starts = (.4, 3.8)
        self.assertNotEqual(displayed_focus(starts, 3.6, base), displayed_focus(starts, 3.6, replace(base, lead_ms=0)))
        self.assertEqual(displayed_focus(starts, 3.6, replace(base, scroll_ms=700)), displayed_focus(starts, 3.6, base))
        self.assertEqual(line_appearance(1, replace(base, other_scale=.7)).scale, .7)
        self.assertEqual(line_appearance(1, replace(base, blur_px=7)).blur, 7)
        self.assertNotEqual(blur_layers(4, base), blur_layers(4, replace(base, blur_px=7)))
        self.assertEqual(line_appearance(0, base).blur, 0)
        self.assertAlmostEqual(line_appearance(1, base).read_alpha, .25)
        self.assertAlmostEqual(line_appearance(0, base).unread_alpha, .55)
        self.assertAlmostEqual(line_appearance(0, base).translation_alpha, .45)


class AppleProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.surfaces = []

    def tearDown(self):
        for owner, surface in self.surfaces:
            surface.shutdown()
            owner.close()

    def surface(self, *args, **kwargs):
        pair = make_surface(*args, **kwargs)
        self.surfaces.append(pair)
        return pair

    def test_default_missing_and_invalid_persisted_values_are_off(self):
        key = "v2_music_apple_lyrics_enabled"
        self.assertIs(DEFAULT_SETTINGS[key], False)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            for raw in ({}, {key: "false"}, {key: 1}):
                path.write_text(json.dumps(raw))
                settings = SettingsManager(settings_path=path)
                self.assertIs(settings.get(key), False)
                settings._save_timer.stop()
            settings.set(key, True)
            settings.save_now()
            restored = SettingsManager(settings_path=path)
            self.assertIs(restored.get(key), True)
            restored.set(key, "false")
            self.assertIs(restored.get(key), True)
            restored._save_timer.stop()

    def test_missing_and_false_keep_old_paint_layout_wipe_and_scroll(self):
        for position in (3.35, 3.625, 7.2):
            _missing, original = self.surface(None, position)
            _disabled, off = self.surface(False, position)
            with patch.object(original, "_paint_apple_lyric_column", side_effect=AssertionError("opt-in only")), \
                    patch.object(off, "_paint_apple_lyric_column", side_effect=AssertionError("opt-in only")):
                first, second = render(original), render(off)
            self.assertEqual(bytes(first.constBits()), bytes(second.constBits()))
            self.assertEqual(original._song_layout_key, off._song_layout_key)
            self.assertEqual(original._lyric_motion.last_position, off._lyric_motion.last_position)
            self.assertEqual(original._lyric_motion.scroll_index, off._lyric_motion.scroll_index)
            self.assertEqual(original._logical_line_spring.positions, off._logical_line_spring.positions)
            self.assertEqual(original._lyric_word_hit_rects, off._lyric_word_hit_rects)
            if position == 7.2:
                # Existing preparation adds interlude dots at 6.45; off keeps
                # the original active-row/scroll contract for that marker.
                self.assertEqual(off._lyric_motion.active_index, 2)

    def test_retired_switch_keeps_the_existing_painter_in_both_window_modes(self):
        for fullscreen in (False, True):
            owner, surface = self.surface(True, 3.625, fullscreen=fullscreen)
            owner.media_position_source = "netease-cef"
            owner._effective_lyric_offset_seconds = lambda: .2
            owner.media_position_seconds = 3.425
            with patch.object(surface, "_paint_apple_lyric_column", side_effect=AssertionError("retired")), \
                    patch.object(surface, "_paint_wrapped_lifted_lyric", wraps=surface._paint_wrapped_lifted_lyric) as lifted:
                render(surface)
            self.assertFalse(surface._apple_lyrics_enabled())
            self.assertTrue(lifted.called)
            self.assertTrue(callable(surface._paint_lyric_scatter_glow))
            self.assertTrue(surface._lyric_hit_rects)
            self.assertEqual(surface._lyric_lead_seconds("netease-cef"), .28)

    def test_existing_painter_uses_apple_line_spacing_without_the_blur_column(self):
        _owner, surface = self.surface(True, 7.2)
        with patch.object(surface, "_paint_apple_lyric_column", side_effect=AssertionError("retired")):
            render(surface)
        self.assertIsNone(surface._obo_advanced_focus_index)
        self.assertTrue(surface._logical_line_spring.positions)
        pixel = surface._surface_font(surface.LYRIC_PIXEL_SIZE * surface._surface_scale()).pixelSize()
        self.assertGreater(pixel, 0)

    def test_line_fallback_estimate_stays_on_the_shared_clock(self):
        plan = row_timing("桥下的灯一盏盏亮", kind="line", start=9.7, end=12.6)
        self.assertEqual(plan.quality, "estimated")
        durations = [unit.end_seconds - unit.start_seconds for unit in plan.display_units]
        self.assertTrue(all(abs(value - (12.6 - 9.7) / 8) < 1e-8 for value in durations))
        self.assertTrue(all(unit.estimated for unit in plan.display_units))

    def test_word_click_uses_transformed_apple_positions_and_original_seek_time(self):
        owner, surface = self.surface(True, 3.625)
        owner._effective_lyric_offset_seconds = lambda **kwargs: .1
        render(surface)
        target = next((key, value) for key, value in surface._lyric_word_hit_rects.items() if key[0] == 1)
        key, (box, seconds) = target
        point = box.center()
        self.assertEqual(surface._lyric_word_target_at(point), (key[0], key[1], seconds))
        from tests.test_music_fullscreen import _PointerEvent
        surface.mousePressEvent(_PointerEvent(point))
        surface.mouseReleaseEvent(_PointerEvent(point))
        self.assertEqual(owner.seek_calls, [seconds - .1])

    def test_loading_and_instrumental_keep_existing_skeleton_policy(self):
        for enabled in (False, True):
            owner, surface = self.surface(enabled)
            owner.timed_lyrics = []
            owner.lyrics = ["加载歌词中"]
            surface._update_lyric_loading_state(100)
            surface._update_lyric_loading_state(102)
            self.assertTrue(surface._lyric_loading_visible(102))
            surface._update_lyric_loading_state(200)
            self.assertTrue(surface._lyric_loading_visible(200))
            with patch.object(surface, "_paint_lyric_loading", wraps=surface._paint_lyric_loading) as loading, \
                    patch.object(surface, "_paint_apple_lyric_column", side_effect=AssertionError("loading")):
                render(surface, 202)
                self.assertTrue(loading.called)
            owner.lyrics = ["纯音乐，请欣赏"]
            surface._update_lyric_loading_state(203)
            self.assertFalse(surface._lyric_loading_visible(203))
            self.assertEqual(surface._lyric_content_state(), "no_lyrics")

    def test_cover_lyrics_uses_the_existing_painter(self):
        _owner, surface = self.surface(True, 3.625)
        surface.set_content_layout("cover_lyrics", animate=False)
        with patch.object(surface, "_paint_apple_lyric_column", side_effect=AssertionError("retired")), \
                patch.object(surface, "_paint_wrapped_lifted_lyric", wraps=surface._paint_wrapped_lifted_lyric) as lifted:
            render(surface)
        self.assertTrue(lifted.called)
        self.assertTrue(surface._lyric_word_hit_rects)

    def test_wrapped_units_and_unicode_seek_match_scaled_positions(self):
        text = "A long 😀 lyric with enough words to wrap across several visual lines slowly"
        lines = (replace(LINES[0], text=text, units=(TimedLyricUnit(.4, 4.55, text),)), *LINES[1:])
        _owner, surface = self.surface(True, 3.625, lines=lines, size=(960, 540))
        with patch.object(surface, "devicePixelRatioF", return_value=2.0):
            render(surface, dpr=2)
        fragments = [(key, box, seconds) for key, (box, seconds) in surface._lyric_word_hit_rects.items() if key[0] == 0]
        self.assertGreater(len(fragments), 1)
        for key, box, seconds in fragments:
            self.assertEqual(surface._lyric_word_target_at(box.center()), (key[0], key[1], seconds))
        self.assertGreater(surface._song_layouts[0][0].lineCount(), 1)

    def test_wipe_raster_is_linear_soft_and_translation_stays_static(self):
        _owner, surface = self.surface(True)
        font = surface._surface_font(40, QFont.Weight.Bold)
        item = surface._wrapped_lyric_layout("MMMM", font, 400)
        plan = row_timing("MMMM", (TimedLyricUnit(0, 4, "MMMM"),), "unit", start=0, end=4)
        appearance = line_appearance(0, surface._apple_lyric_knobs)
        def raster(t, translation=False):
            image = QImage(440, 100, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                surface._paint_apple_layer(painter, "MMMM", font, 400, item, QPointF(20, 20),
                    QColor("white"), appearance, t, plan=plan, offsets=tuple(range(5)), translation=translation)
            return image
        images = [raster(t) for t in (0, 1, 2, 3, 4, 8)]
        def alpha_sum(image):
            return sum(image.pixelColor(x, y).alpha() for y in range(image.height()) for x in range(image.width()))
        amounts = [alpha_sum(image) for image in images]
        self.assertEqual(amounts[4], amounts[5])
        self.assertTrue(all(a < b for a, b in zip(amounts[:4], amounts[1:5])))
        self.assertAlmostEqual(amounts[0] / amounts[4], .55, delta=.01)
        first, later = raster(1, True), raster(3, True)
        self.assertEqual(bytes(first.constBits()), bytes(later.constBits()))
        old = bytes(images[2].constBits())
        surface._apple_lyric_knobs = replace(surface._apple_lyric_knobs, feather_chars=0)
        sharp = raster(2)
        self.assertNotEqual(old, bytes(sharp.constBits()))

    def test_translation_toggle_retains_primary_layout_and_anchor(self):
        owner, surface = self.surface(True)
        owner.media_is_playing = False
        render(surface)
        primary = surface._song_layouts
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            owner.enable_dual_line_lyrics = False
            surface.on_translation_visibility_changed(False)
        for now in (100.05, 100.16, 100.33):
            with patch.object(surface, "_paint_apple_lyric_column", side_effect=AssertionError("retired")):
                render(surface, now)
            self.assertEqual(surface._song_layouts, primary)

    def test_line_spacing_follows_the_apple_block_gap(self):
        _owner, surface = self.surface(False, 7.2)
        surface._lyric_resize_probe = True
        scratch = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
        from local.music_lyric_resize import LyricResizeProbeReady
        try:
            with QPainter(scratch) as painter, self.assertRaises(LyricResizeProbeReady):
                surface._paint_lyric_column_ink(painter, QRectF(surface.rect()))
            args, _kwargs = surface._lyric_resize_probe_inputs
            self.assertEqual(args[3], args[1].pixelSize() * surface._apple_lyric_knobs.block_gap_ratio)
        finally:
            surface._lyric_resize_probe = False

    def test_existing_scroll_spring_is_critically_damped_and_adds_no_timers(self):
        from local.music_lyric_motion import LogicalLineSpringChain
        ratio = LogicalLineSpringChain.DAMPING / (2.0 * math.sqrt(LogicalLineSpringChain.STIFFNESS))
        self.assertAlmostEqual(ratio, 1.0, places=6)
        self.assertEqual(LogicalLineSpringChain.STIFFNESS, 220.0)
        owner, surface = self.surface(True)
        render(surface)
        count = len(surface.findChildren(QTimer))
        owner.media_is_playing = False
        surface._advance_lyric_animation(101)
        self.assertEqual(len(surface.findChildren(QTimer)), count)

    def test_resize_capture_reuses_layout_and_probe_reports_apple_spacing(self):
        _owner, surface = self.surface(True)
        render(surface)
        with patch.object(surface, "isVisible", return_value=True), \
                patch.object(surface, "_song_lyric_layouts", side_effect=AssertionError("shape during capture")):
            surface._enter_lyric_resize()
        self.assertTrue(surface._lyric_resize_frozen)
        self.assertFalse(surface._lyric_hit_rects)
        surface._cancel_lyric_resize()
        from local.music_lyric_resize import LyricResizeProbeReady
        surface._lyric_resize_probe = True
        scratch = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
        try:
            with QPainter(scratch) as painter, self.assertRaises(LyricResizeProbeReady):
                surface._paint_lyric_column_ink(painter, QRectF(surface.rect()))
            args, kwargs = surface._lyric_resize_probe_inputs
            self.assertEqual(args[3], args[1].pixelSize() * surface._apple_lyric_knobs.block_gap_ratio)
            self.assertNotEqual(
                kwargs.get("translation_gap"),
                args[1].pixelSize() * surface._apple_lyric_knobs.block_gap_ratio,
            )
        finally:
            surface._lyric_resize_probe = False

    def test_narrow_mounted_music_column_uses_same_apple_painter(self):
        from api.models import ColorToken, IslandVisualData, TrackData
        from api.providers import EmptyIslandDataProvider
        from local.island_stack_visual import IslandStackVisual
        from _demos.narrow3.narrow_window import NarrowStudyWindow
        track = TrackData("Lyric fixture", "Fixture", (), ColorToken(40, 60, 80),
            ColorToken(20, 30, 40), (), source_app="fixture-player", is_playing=False,
            duration_seconds=16, position_seconds=3.625, timed_lyrics=LINES, track_id="apple-narrow")
        owner = IslandStackVisual(data_provider=EmptyIslandDataProvider(IslandVisualData(track, (), ())))
        owner.timer.stop()
        owner.settings_getter = lambda key, default=None: True if key == "v2_music_apple_lyrics_enabled" else default
        surface = NarrowStudyWindow(owner, "B", "left", live=True)
        try:
            from PySide6.QtTest import QTest
            owner._apply_visual_data(owner.data_provider.get_visual_data())
            for _ in range(100):
                if surface._display_lyrics()[1]:
                    break
                QTest.qWait(10)
            self.assertTrue(surface._display_lyrics()[1])
            for timer in surface.findChildren(QTimer):
                timer.stop()
            surface.resize(390, 720)
            surface.narrow = True
            surface.mode = "lyrics"
            # Prepared provider input and the same mounted painter as live
            # narrow, avoiding the unrelated shell's animated veil cache.
            surface._display_lyrics_cache_key = None
            image = QImage(surface.size(), QImage.Format.Format_ARGB32_Premultiplied)
            with patch.object(surface, "_paint_apple_lyric_column", side_effect=AssertionError("retired")):
                with QPainter(image) as painter:
                    surface._paint_wide_lyrics_mounted(painter)
            self.assertTrue(surface._lyric_hit_rects)
        finally:
            surface.shutdown()
            surface.study_timer.stop()
            owner._application_quitting = True
            owner.close()

    def test_toggle_retires_hits_without_resetting_audio_clock(self):
        owner, surface = self.surface(True)
        render(surface)
        position = surface._lyric_motion.last_position
        owner.preferences["v2_music_apple_lyrics_enabled"] = False
        surface.on_apple_lyric_style_changed()
        self.assertFalse(surface._lyric_hit_rects)
        render(surface)
        self.assertEqual(surface._lyric_motion.last_position, position)
        self.assertTrue(surface._logical_line_spring.positions)

    def test_setting_switch_uses_existing_lyrics_page_and_refreshes_surface(self):
        from local.settings_window import SettingsWindow
        from tests.test_mac_preferences_shell import FakeIslandWidget, FakeSettingsManager
        from unittest.mock import Mock
        island, settings = FakeIslandWidget(), FakeSettingsManager()
        island.music_fullscreen_overlay = Mock()
        window = SettingsWindow(island, settings)
        try:
            page = next(page["page"] for page in window._settings_pages if page["title"] == "歌词")
            self.assertFalse(hasattr(window, "apple_lyrics_switch"))
            self.assertFalse(window._settings_search_matches("Apple 风格歌词"))
            # The broadcast style is a look, so it lives with the other styles.
            appearance = next(page["page"] for page in window._settings_pages if page["title"] == "外观")
            self.assertTrue(appearance.isAncestorOf(window.broadcast_style))
            self.assertFalse(page.isAncestorOf(window.broadcast_style))
        finally:
            window.close()
            window.deleteLater()
            island.deleteLater()
            self.app.processEvents()

    def test_theme_does_not_repaint_the_lyric_page_and_sea_keeps_dark_buttons(self):
        for fullscreen in (False, True):
            owner, surface = self.surface(True, 4.2, fullscreen=fullscreen)
            owner.preferences["v2_theme_mode"] = "light"
            # "System" keeps the album-flow page; solid and sea paint their own.
            owner.preferences["v2_music_ambience_style"] = "system"
            render(surface)
            self.assertTrue(surface._control_glass_is_light())
            self.assertNotEqual(surface._background_cache_key[0], "light-page")
            light_page = surface._background_cache.pixelColor(4, 4).getRgb()[:3]
            owner.preferences["v2_theme_mode"] = "dark"
            render(surface)
            self.assertFalse(surface._control_glass_is_light())
            self.assertEqual(surface._background_cache.pixelColor(4, 4).getRgb()[:3], light_page)
            owner.preferences["v2_theme_mode"] = "light"
            owner.preferences["v2_music_ambience_style"] = "sea_level"
            render(surface)
            # On the sea field the buttons follow the words' ink.
            self.assertEqual(surface._control_glass_is_light(), surface._sea_word_ink() == "black")
            # Sea level paints its own vignette field on the lyric page.
            self.assertIsNotNone(surface._sea_plan)
            self.assertNotEqual(surface._background_cache.pixelColor(4, 4).getRgb()[:3], light_page)


if __name__ == "__main__":
    unittest.main()
