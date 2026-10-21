"""Song-page glass samples its scene and follows the existing theme policy."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.music_surface_window import (MusicSurfaceWindow, paint_audio_output_status,
                                       paint_music_source_icon, paint_personal_dj_icon,
                                       paint_presentation_glyph)
from tests.test_music_surface_transition import _TransitionOwner


class MusicSurfaceGlassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.mode = "dark"
        self.owner.settings_getter = lambda key, default=None: self.mode if key == "v2_theme_mode" else default
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.window._reset_control_idle(now=100.)
        self.window._background_cache = QImage(240, 135, QImage.Format.Format_RGB32)
        self.window._background_cache.fill(QColor("#526a81"))

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def image(self):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        return image

    def test_material_maps_local_source_and_follows_explicit_theme_even_when_active(self):
        target = QRectF(200, 100, 80, 40)
        for mode, light in (("dark", False), ("light", True)):
            self.mode = mode
            image = self.image()
            with QPainter(image) as painter, \
                    patch("local.music_surface_window.time.monotonic", return_value=100.), \
                    patch("local.music_surface_window.paint_liquid_glass") as paint:
                self.window._paint_surface_glass(painter, target, active=True)
            self.assertEqual(paint.call_args.kwargs["light"], light)
            self.assertTrue(paint.call_args.kwargs["refractive"])
            self.assertEqual(paint.call_args.kwargs["backdrop_rect"], QRectF(50, 25, 20, 10))
            self.assertIsNone(paint.call_args.kwargs.get("base_color"))

    def test_idle_scene_freezes_for_prewarm_and_hide_then_refreshes_after_motion(self):
        idle = self.window.WINDOWED_CONTROL_IDLE_SECONDS
        lead_in = 100. + max(.3, idle - 2.) + .1
        with patch("local.music_surface_window.time.monotonic", return_value=lead_in):
            frozen = self.window._surface_glass_backdrop().cacheKey()
        self.window._background_cache.fill(QColor("red"))
        with patch("local.music_surface_window.time.monotonic", return_value=100. + idle + .1):
            self.assertEqual(self.window._surface_glass_backdrop().cacheKey(), frozen)
        self.window._reset_control_idle(now=110.)
        with patch("local.music_surface_window.time.monotonic", return_value=110.):
            self.assertNotEqual(self.window._surface_glass_backdrop().cacheKey(), frozen)

    def test_transport_stays_bare_glyphs_during_feedback_with_broad_hit_slots(self):
        panel = QRectF(50, 400, 330, 64)
        self.window._transport_feedback["next"] = (100., 1., None, 1.)
        image = self.image()
        with QPainter(image) as painter, \
                patch("local.music_surface_window.time.monotonic", return_value=100.05), \
                patch.object(self.window, "_paint_surface_glass") as glass, \
                patch.object(self.window, "_paint_transport_icon") as glyph:
            self.window._paint_media_controls(painter, panel, QColor("white"), QColor("gray"))
        glass.assert_not_called()
        self.assertEqual({call.args[2] for call in glyph.call_args_list},
                         {"previous", "toggle_play_pause", "next"})
        self.assertEqual(set(self.window._media_button_rects), {"previous", "toggle_play_pause", "next"})
        next_icon = next(call.args[1] for call in glyph.call_args_list if call.args[2] == "next")
        self.assertGreater(self.window._media_button_rects["next"].width(), next_icon.width())

    def test_foreground_contrast_tracks_real_scene_and_theme(self):
        for mode in ("dark", "light"):
            self.mode = mode
            for color in (QColor("#111122"), QColor("#f0f1f2")):
                self.window._background_cache.fill(color)
                self.window._control_color_transitions.clear()
                ink = self.window._surface_glass_ink(QRectF(0, 0, 44, 44), live=True)
                self.assertGreaterEqual(self.window._contrast_ratio(ink, color), 7.)

    def test_whole_scene_area_overrules_minority_patch_beneath_each_button_in_both_themes(self):
        for mode in ("dark", "light"):
            self.mode = mode
            for majority, minority, expected in (("black", "white", "white"), ("white", "black", "black")):
                with self.subTest(mode=mode, majority=majority):
                    self.window._control_color_transitions.clear()
                    self.window._background_cache.fill(QColor(majority))
                    with QPainter(self.window._background_cache) as painter:
                        painter.fillRect(0, 0, 60, 135, QColor(minority))
                    # Both target rectangles lie inside the opposite-valued
                    # quarter, where independent local sampling used to split.
                    for target in (QRectF(0, 0, 44, 44), QRectF(50, 250, 44, 44), QRectF(850, 450, 44, 44)):
                        for active, live in ((False, False), (True, False), (False, True)):
                            self.assertEqual(self.window._surface_glass_ink(target, active=active, live=live), QColor(expected))

    def test_uneven_checkerboard_decides_by_area_rather_than_mean_brightness(self):
        image = QImage(32, 18, QImage.Format.Format_RGB32)
        dark, bright = QColor("#333333"), QColor("white")
        # Every third column is bright: arithmetic RGB averaging exceeds the
        # black/white crossover, but two thirds of the visible area is dark.
        for y in range(image.height()):
            for x in range(image.width()):
                image.setPixelColor(x, y, bright if (x + y) % 3 == 0 else dark)
        self.window._background_cache = image
        self.assertEqual(self.window._scene_control_ink(), QColor("white"))
        for y in range(image.height()):
            for x in range(image.width()):
                image.setPixelColor(x, y, dark if (x + y) % 3 == 0 else bright)
        self.window._control_color_transitions.clear()
        self.assertEqual(self.window._scene_control_ink(), QColor("black"))

    def test_scene_sampling_is_bounded_and_reused_for_all_control_targets(self):
        with patch.object(self.window, "_scene_control_ink_areas", wraps=self.window._scene_control_ink_areas) as vote:
            first = self.window._scene_control_ink()
            self.assertEqual(vote.call_count, 1)
            self.assertEqual(vote.call_args.args[2:], (32, 18))
            for target in (QRectF(0, 0, 44, 44), QRectF(850, 450, 44, 44)):
                self.assertEqual(self.window._surface_glass_ink(target, live=True), first)
            self.assertEqual(vote.call_count, 1)
            self.window._background_cache.fill(QColor("white"))
            self.window._scene_control_ink()
            self.assertEqual(vote.call_count, 2)
            self.window._background_cache = QImage(7, 5, QImage.Format.Format_RGB32)
            self.window._background_cache.fill(QColor("black"))
            self.window._scene_control_ink()
            self.assertEqual(vote.call_count, 3)
            self.assertEqual(vote.call_args.args[2:], (7, 5))
            self.assertTrue(all(call.args[2] * call.args[3] <= 32 * 18
                                for call in vote.call_args_list))

    def test_shared_decided_ink_smoothly_retargets_between_songs_and_mid_transition(self):
        self.window._background_cache.fill(QColor("black"))
        with patch("local.music_surface_window.time.monotonic", return_value=100.):
            self.assertEqual(self.window._scene_control_ink(), QColor("white"))
        self.window._background_cache.fill(QColor("white"))
        with patch("local.music_surface_window.time.monotonic", return_value=101.):
            self.assertEqual(self.window._scene_control_ink(), QColor("white"))
        with patch("local.music_surface_window.time.monotonic", return_value=101.18):
            middle = self.window._scene_control_ink()
            self.assertTrue(110 <= middle.red() <= 145)
            self.assertEqual(middle.red(), middle.green())
            self.assertEqual(middle.green(), middle.blue())
            self.assertEqual(self.window._surface_glass_ink(QRectF(850, 450, 44, 44), active=True), middle)
        self.window._background_cache.fill(QColor("black"))
        with patch("local.music_surface_window.time.monotonic", return_value=101.18):
            self.assertEqual(self.window._scene_control_ink(), middle)
        with patch("local.music_surface_window.time.monotonic", return_value=101.54):
            self.assertEqual(self.window._scene_control_ink(), QColor("white"))

    def test_fm_gradient_keeps_extracted_accent_and_uses_shared_decided_endpoint(self):
        target = QRectF(800, 10, 44, 44)
        for background, expected in (("black", "white"), ("white", "black")):
            with self.subTest(background=background):
                self.window._control_color_transitions.clear()
                self.window._background_cache.fill(QColor(background))
                with patch.object(self.window, "_future_lyric_color", return_value=QColor("#248ace")):
                    stops = self.window._personal_fm_stops(target)
                self.assertEqual(stops, ((0., QColor("#248ace")), (.5, QColor(expected)), (1., QColor("#248ace"))))
                with patch.object(self.window, "_future_lyric_color", return_value=QColor("#be4c2b")), \
                        patch("local.music_surface_window.time.monotonic", return_value=200.):
                    self.assertEqual(self.window._personal_fm_stops(target)[0][1], QColor("#248ace"))
                with patch.object(self.window, "_future_lyric_color", return_value=QColor("#be4c2b")), \
                        patch("local.music_surface_window.time.monotonic", return_value=200.36):
                    self.assertEqual(self.window._personal_fm_stops(target)[0][1], QColor("#be4c2b"))

    def test_accent_opacity_is_stable_and_interpolates_without_fade_cache_churn(self):
        source, target = QColor(36, 138, 206, 242), QColor(190, 76, 43, 128)
        for instant in (100., 100.08, 100.36):
            self.assertEqual(self.window._interpolated_control_color("fm-accent", source, instant), source)
        self.assertEqual(self.window._interpolated_control_color("fm-accent", target, 101.), source)
        middle = self.window._interpolated_control_color("fm-accent", target, 101.18)
        self.assertEqual(middle.alpha(), 185)
        self.assertEqual(self.window._interpolated_control_color("fm-accent", target, 101.36), target)
        self.assertEqual(self.window._interpolated_control_color("fm-accent", target, 102.), target)

    def test_drawn_function_transport_dj_and_transition_glyphs_share_one_decided_color(self):
        self.window._background_cache.fill(QColor("white"))
        self.window._owned_playback_mode = "shuffle"
        self.window._owned_personal_dj_enabled = True
        self.window._owned_dj_speaking = False
        for mode in ("dark", "light"):
            self.mode = mode
            for fullscreen in (False, True):
                self.window._fullscreen = fullscreen
                # Exercise the real direct drawing path for each theme and
                # presentation; material reuse must not change glyph ink.
                self.window._control_layer_cache.clear()
                self.window._control_layer_cache_bytes = 0
                with self.subTest(mode=mode, fullscreen=fullscreen), \
                        patch("local.music_surface_window.time.monotonic", return_value=100.), \
                        patch.object(self.window, "_audio_output_available", return_value=True), \
                        patch.object(self.window, "_library_back_available", return_value=True), \
                        patch.object(self.window, "_future_lyric_color", return_value=QColor("magenta")), \
                        patch.object(self.window, "_personal_dj_reveal_at", return_value=1.), \
                        patch.object(self.window, "_personal_dj_activity_at", return_value=0.), \
                        patch("local.music_surface_window.paint_music_source_icon", wraps=paint_music_source_icon) as source, \
                        patch("local.music_surface_window.paint_audio_output_status", wraps=paint_audio_output_status) as output, \
                        patch("local.music_surface_window.paint_presentation_glyph", wraps=paint_presentation_glyph) as presentation, \
                        patch("local.music_surface_window.paint_personal_dj_icon", wraps=paint_personal_dj_icon) as dj, \
                        patch.object(self.window, "_paint_transport_icon", wraps=self.window._paint_transport_icon) as transport:
                    def paint_controls():
                        image = self.image()
                        with QPainter(image) as painter:
                            rect = QRectF(self.window.rect())
                            self.window._paint_library_back(painter, rect)
                            self.window._paint_window_controls(painter, rect)
                            self.window._paint_media_controls(painter, QRectF(20, 400, 330, 60), QColor("red"), QColor("green"))
                            self.window._paint_personal_dj_control(painter, rect)
                            self.window._paint_transition_mode_glyph(painter)
                        return image

                    cold = paint_controls()
                    self.assertTrue(source.called and output.called and presentation.called and dj.called and transport.called)
                    self.assertTrue(any(cold.constBits()))
                    cache_before = {key: image.cacheKey() for key, image in self.window._control_layer_cache.items()}
                    calls_before = (source.call_count, output.call_count, transport.call_count)
                    self.assertEqual(paint_controls(), cold)
                    self.assertEqual((source.call_count, output.call_count, transport.call_count),
                                     tuple(2 * count for count in calls_before))
                    self.assertEqual({key: image.cacheKey() for key, image in self.window._control_layer_cache.items()}, cache_before)
                    self.assertLessEqual(len(cache_before), 48)
                    self.assertLessEqual(self.window._control_layer_cache_bytes, self.window.CONTROL_LAYER_CACHE_MAX_BYTES)
                    self.assertTrue(all(call.kwargs["tint"] == QColor("black") for call in source.call_args_list))
                    self.assertTrue(all(call.args[3] == QColor("black") for call in output.call_args_list))
                    self.assertTrue(all(call.args[3] == QColor("black") for call in presentation.call_args_list))
                    self.assertTrue(all(call.args[2] == QColor("black") for call in dj.call_args_list))
                    self.assertTrue(all(call.args[3] == QColor("black") for call in transport.call_args_list))

    def test_waking_controls_reuse_frozen_scene_until_reveal_finishes(self):
        idle = self.window.WINDOWED_CONTROL_IDLE_SECONDS
        lead_in = 100. + max(.3, idle - 2.) + .1
        with patch("local.music_surface_window.time.monotonic", return_value=lead_in):
            frozen = self.window._surface_glass_backdrop().cacheKey()
        self.window._background_cache.fill(QColor("red"))
        duration = self.window.FULLSCREEN_CONTROL_TRANSITION_SECONDS
        wake_at = 100. + idle + duration + .1
        with patch("local.music_surface_window.time.monotonic", return_value=wake_at), \
                patch.object(self.window, "isActiveWindow", return_value=True):
            self.assertEqual(self.window._control_reveal_at(wake_at), 0.)
            self.window._note_pointer_moved(wake_at)
        self.window._background_cache.fill(QColor("green"))
        with patch("local.music_surface_window.time.monotonic", return_value=wake_at + duration * .5):
            self.assertEqual(self.window._surface_glass_backdrop().cacheKey(), frozen)
        with patch("local.music_surface_window.time.monotonic", return_value=wake_at + duration + .01):
            self.assertEqual(self.window._surface_glass_backdrop().cacheKey(),
                             self.window._background_cache.cacheKey())

    def test_live_dj_material_bypasses_shared_idle_scene(self):
        idle = self.window.WINDOWED_CONTROL_IDLE_SECONDS
        lead_in = 100. + max(.3, idle - 2.) + .1
        with patch("local.music_surface_window.time.monotonic", return_value=lead_in):
            frozen = self.window._surface_glass_backdrop().cacheKey()
        self.window._background_cache.fill(QColor("red"))
        image = self.image()
        with QPainter(image) as painter, \
                patch("local.music_surface_window.time.monotonic", return_value=100. + idle + .1), \
                patch("local.music_surface_window.paint_liquid_glass") as paint:
            self.window._paint_surface_glass(painter, QRectF(800, 450, 44, 44), live=True)
        sampled = paint.call_args.kwargs["backdrop"]
        self.assertEqual(sampled.cacheKey(), self.window._background_cache.cacheKey())
        self.assertNotEqual(sampled.cacheKey(), frozen)

    def test_dj_caption_and_icon_both_request_live_glass(self):
        self.window._owned_personal_dj_enabled = False
        image = self.image()
        with QPainter(image) as painter, \
                patch("local.music_surface_window.time.monotonic", return_value=100.), \
                patch.object(self.window, "_personal_dj_reveal_at", return_value=1.), \
                patch.object(self.window, "_personal_dj_activity_at", return_value=0.), \
                patch.object(self.window, "_paint_surface_glass") as glass:
            self.window._paint_personal_dj_control(painter, QRectF(self.window.rect()))
        self.assertEqual(glass.call_count, 2)
        self.assertTrue(all(call.kwargs.get("live") is True for call in glass.call_args_list))


if __name__ == "__main__":
    unittest.main()
