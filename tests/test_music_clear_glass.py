"""Confirmed song-page Clear material, rest geometry and press-only motion."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPainterPath, QPainterPathStroker
from PySide6.QtWidgets import QApplication

from local.glass_style import (GLASS_CLEAR, GLASS_DJ, GLASS_GLYPH, PRESS_HOLD_SECONDS,
                               PRESS_STIFFNESS, PRESS_DAMPING)
from local.music_control_glass import (ControlFeedback, ControlMotion, GlassControlRenderer,
                                       control_geometry, paint_control_overlay, _sampling_maps, _sample, _sample_cached)
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner
from tests.test_owned_playback_surface import _Provider
from tests.test_song_collection_surface import Collection


class ClearMotionTests(unittest.TestCase):
    def setUp(self):
        self.feedback = ControlFeedback()
        self.top = {"a": QRectF(0, 0, 35, 35), "b": QRectF(45, 0, 35, 35),
                    "c": QRectF(90, 0, 35, 35)}
        self.back = {"library": QRectF(0, 0, 35, 35)}
        self.feedback.advance([self.top, self.back], "", 100.)

    def tick(self, until, hovered="a"):
        while self.feedback.last_tick < until - 1e-7:
            self.feedback.advance([self.top, self.back], hovered,
                                  min(until, self.feedback.last_tick + 1. / 120.))

    def test_sampling_maps_are_reused_and_match_bilinear_sampling(self):
        key = (70, 70, 17.5, 2., GLASS_CLEAR, None)
        _sampling_maps.cache_clear()
        maps = _sampling_maps(key, 20, 110, 110)
        self.assertIs(maps, _sampling_maps(key, 20, 110, 110))
        self.assertEqual(_sampling_maps.cache_info().hits, 1)
        self.assertEqual(_sampling_maps.cache_info().maxsize, 32)
        samples, _, _ = control_geometry(*key)
        pixels = np.random.default_rng(1).random((110, 110), dtype=np.float32)
        for mapping, (x, y) in zip(maps, samples):
            expected = _sample(pixels, (x + 20) * 110 / 110 - .5,
                               (y + 20) * 110 / 110 - .5)
            np.testing.assert_array_equal(_sample_cached(pixels, mapping), expected)
        self.assertIsNot(maps, _sampling_maps(key, 21, 112, 112))

    def test_shade_matches_frozen_reference(self):
        from local.music_control_glass import _shade, _path_signature
        from tests.glass_shade_reference import reference_shade

        rng = np.random.default_rng(90210)
        scenes = []
        for low, high in ((0, 256), (0, 96), (160, 256)):
            pixels = rng.integers(low, high, (200, 240, 4), dtype=np.uint8)
            pixels[..., 3] = 255
            scenes.append(QImage(pixels.data, 240, 200, 240 * 4,
                                 QImage.Format.Format_RGBA8888).copy())
        threshold_scene = QImage(240, 200, QImage.Format.Format_RGB32)
        threshold_scene.fill(QColor(1, 185, 107))
        scenes.extend((threshold_scene, QImage()))
        bounds = QRectF(0, 0, 240, 200)
        path = QPainterPath()
        path.moveTo(2, 2)
        path.lineTo(28, 16)
        path.lineTo(2, 30)
        path.closeSubpath()
        for scene in scenes:
            for spec in (GLASS_CLEAR, GLASS_DJ, GLASS_GLYPH):
                for rect, dpr, signature in ((QRectF(70, 60, 35, 35), 2., None),
                                             (QRectF(-4, 4, 45, 28), 2., None),
                                             (QRectF(210, 170, 32, 32), 3., _path_signature(path))):
                    w, h = round(rect.width() * dpr), round(rect.height() * dpr)
                    key = (w, h, min(rect.width(), rect.height()) / 2, dpr, spec, signature)
                    for level in (0, 50, 100):
                        args = (scene, bounds, rect, dpr, spec.at_level(level), key, (70, 150, 233))
                        expected = reference_shade(*args)
                        actual = _shade(*args)
                        a = np.frombuffer(actual.constBits(), np.uint8).astype(np.int16)
                        b = np.frombuffer(expected.constBits(), np.uint8).astype(np.int16)
                        self.assertLessEqual(np.max(np.abs(a - b)), 1, (spec, rect, level))

    def test_moving_refresh_interval_reuses_patches(self):
        scene = QImage(160, 160, QImage.Format.Format_RGB32)
        scene.fill(QColor("red"))
        output = QImage(160, 160, QImage.Format.Format_ARGB32_Premultiplied)
        state = ControlMotion(held=True)
        for interval, expected in ((0., (1, 2, 3)), (.033, (1, 1, 2))):
            renderer = GlassControlRenderer()
            with patch("local.glass_style.GLASS_MOVING_REFRESH_SECONDS", interval), \
                 patch("local.music_control_glass._shade", wraps=__import__(
                     "local.music_control_glass", fromlist=["_shade"])._shade) as shade, \
                 QPainter(output) as painter:
                for index, now in enumerate((100., 100.01, 100.04)):
                    scene.fill(QColor("red" if index % 2 == 0 else "blue"))
                    renderer.paint(painter, QRectF(30 + index, 30, 35, 35), scene=scene,
                                   scene_bounds=QRectF(0, 0, 160, 160), ink=QColor("white"),
                                   now=now, role="test", state=state)
                    self.assertEqual(shade.call_count, expected[index])
                renderer.paint(painter, QRectF(33, 30, 36, 35), scene=scene,
                               scene_bounds=QRectF(0, 0, 160, 160), ink=QColor("white"),
                               now=100.041, role="test", state=state)
                self.assertEqual(shade.call_count, expected[-1] + (interval == 0.))
                renderer.paint(painter, QRectF(33, 30, 36, 35), scene=scene,
                               scene_bounds=QRectF(0, 0, 160, 160), ink=QColor("white"),
                               now=100.08, role="test", state=state)
                self.assertEqual(shade.call_count, expected[-1] + 1)

    def test_confirmed_tokens(self):
        self.assertEqual(tuple(vars(GLASS_CLEAR).values()), (1.2, 9., 6., .15, .05, 0., 1.3, 1., .45, .05))
        self.assertEqual(tuple(vars(GLASS_DJ).values()), (1.2, 9., 6., .15, 0., .72, 1., 1., .45, .05))
        self.assertEqual(tuple(vars(GLASS_GLYPH).values()), (1.2, 3.2, 2.2, .12, 0., .42, 1.3, 1., .5, .06))
        self.assertEqual((PRESS_STIFFNESS, PRESS_DAMPING, PRESS_HOLD_SECONDS), (420., 30., .3))

    def test_level_changes_only_blur_and_veil(self):
        for token in (GLASS_CLEAR, GLASS_DJ, GLASS_GLYPH):
            for level in (0, 25, 50, 100):
                scaled = vars(token.at_level(level))
                for name, value in vars(token).items():
                    self.assertEqual(scaled[name], value * level / 50. if name in {"blur", "veil"} else value)

    def test_hover_lights_without_growth_or_neighbor_motion(self):
        self.tick(100.5)
        self.assertGreater(self.feedback.hover["a"], .99)
        for state in self.feedback.controls.values():
            self.assertEqual((state.lift, state.glow, state.offset), (0., 0., QPointF()))

    def test_push_starts_while_button_is_down_and_groups_are_isolated(self):
        self.feedback.press("a", QPointF(10, 10), 100.)
        self.tick(100.2)
        a, b, c, back = [self.feedback.state(key) for key in ("a", "b", "c", "library")]
        self.assertGreater(a.lift, .9)
        self.assertGreater(a.glow, .9)
        self.assertEqual(a.offset, QPointF())
        self.assertGreater(b.offset.x(), 4.)
        self.assertGreater(b.offset.x(), c.offset.x())
        self.assertEqual(back.offset, QPointF())

    def test_quick_tap_keeps_push_for_point_three_seconds_then_returns(self):
        self.feedback.press("a", QPointF(10, 10), 100.)
        self.tick(100.025)
        self.feedback.release()
        self.tick(100.299)
        self.assertGreater(self.feedback.state("a").press_motion.value(100.299), .98)
        self.assertGreater(self.feedback.state("b").offset.x(), 4.5)
        self.tick(100.65)
        self.assertLess(self.feedback.state("b").offset.x(), .25)
        self.assertLess(abs(self.feedback.state("a").lift), .001)
        self.assertEqual(self.feedback.state("a").glow, 0.)

    def test_long_hold_keeps_push_until_release_and_repress_is_continuous(self):
        self.feedback.press("a", QPointF(10, 10), 100.)
        self.tick(101.)
        self.assertAlmostEqual(self.feedback.state("a").lift, 1., places=3)
        self.assertGreater(self.feedback.state("b").offset.x(), 5.)
        self.feedback.release()
        self.tick(101.04)
        lift = self.feedback.state("a").lift
        self.feedback.press("a", QPointF(11, 10), 101.04)
        self.assertEqual(self.feedback.state("a").lift, lift)
        self.tick(101.2)
        self.assertGreater(self.feedback.state("a").lift, lift)

    def test_touch_point_dead_zone_and_release(self):
        self.feedback.press("a", QPointF(10, 10), 100.)
        self.feedback.move(QPointF(12, 10))
        self.assertEqual(self.feedback.state("a").contact, QPointF(10, 10))
        self.feedback.move(QPointF(12, 12))
        self.assertEqual(self.feedback.state("a").contact, QPointF(10, 10))
        self.feedback.move(QPointF(15, 12))
        self.assertEqual(self.feedback.state("a").contact, QPointF(15, 12))
        self.feedback.release()
        self.feedback.move(QPointF(30, 30))
        self.assertEqual(self.feedback.state("a").contact, QPointF(15, 12))

    def test_large_tick_and_reset_are_finite_and_release_all_records(self):
        self.feedback.press("a", QPointF(), 100.)
        self.tick(100.15)
        self.feedback.release()
        self.feedback.advance([self.top], "", 200.)
        self.assertTrue(np.isfinite(self.feedback.state("a").lift))
        self.feedback.reset()
        self.assertFalse(self.feedback.controls)
        self.assertFalse(self.feedback.hover)


class ClearRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(320, 200)
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()
        self.window._reset_control_idle(100.)
        self.scene = QImage(320, 200, QImage.Format.Format_RGB32)
        self.scene.fill(QColor("#376a83"))
        self.window._background_cache = self.scene

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def image(self):
        image = QImage(320, 200, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        return image

    def test_visible_layer_cache_invalidation_and_filtered_feedback(self):
        window = self.window
        renderer = window._clear_control_renderer
        window._glass_feedback.controls["library"] = ControlMotion()
        window._glass_feedback.controls["media:next"] = ControlMotion()
        rect = QRectF(70, 60, 35, 35)
        now = [100.]
        material = {"ink": QColor("white"), "level": 50, "light": True}
        calls = []
        def draw(painter):
            calls.append(1)
            renderer.paint(painter, rect, scene=self.scene, scene_bounds=QRectF(window.rect()),
                           ink=material["ink"], now=now[0], role="library",
                           state=window._glass_feedback.state("library"), level=material["level"])
        def paint(reveal=1., bounds=None, shift=QPointF(), key=("library",)):
            output = self.image()
            renderer.begin_frame(now[0])
            try:
                with QPainter(output) as painter:
                    window._paint_control_layer(painter, bounds or rect, reveal, shift, key, draw)
            finally:
                renderer.end_frame()
            return output
        with patch("local.music_surface_window.time.monotonic", new=lambda: now[0]), \
                patch.object(window, "_surface_glass_backdrop", new=lambda: self.scene), \
                patch.object(window, "_scene_control_ink", new=lambda: material["ink"]), \
                patch.object(window, "_surface_glass_level", new=lambda: material["level"]), \
                patch.object(window, "_surface_glass_light", new=lambda: material["light"]):
            first = paint()
            self.assertEqual(paint(), first)
            self.assertEqual(len(calls), 1)
            # Other groups must not invalidate this layer.
            window._glass_feedback.controls["media:next"].glow = .7
            window._glass_feedback.hover["media:next"] = .4
            self.assertEqual(paint(), first)
            self.assertEqual(len(calls), 1)
            for change in (
                lambda: window._glass_feedback.hover.__setitem__("library", .5),
                lambda: setattr(window._glass_feedback.controls["library"], "lift", .2),
                lambda: setattr(window._glass_feedback.controls["library"], "glow", .6),
                lambda: material.__setitem__("ink", QColor("black")),
                lambda: material.__setitem__("level", 70),
                lambda: material.__setitem__("light", False),
            ):
                old = len(calls)
                change()
                paint()
                self.assertEqual(len(calls), old + 1)
            old = len(calls)
            paint(bounds=rect.adjusted(0, 0, 1, 0))
            paint(shift=QPointF(.125, .25))
            paint(reveal=.9995)
            self.assertEqual(len(calls), old + 3)
            # Scene changes alone wait for the existing patch schedule.
            window._glass_feedback.controls["library"] = ControlMotion()
            paint()
            old = len(calls)
            self.scene.fill(QColor("red"))
            now[0] += .01
            paint()
            self.assertEqual(len(calls), old)
            now[0] += 1.
            paint()
            self.assertEqual(len(calls), old + 1)
            version = renderer.versions["library"]
            # A patch updated by another caller invalidates this layer.
            other_image = self.image()
            with QPainter(other_image) as painter:
                renderer.paint(painter, rect.adjusted(1, 0, 1, 0), scene=self.scene,
                               scene_bounds=QRectF(window.rect()), ink=material["ink"],
                               now=now[0], role="library", level=material["level"])
            self.assertGreater(renderer.versions["library"], version)
            paint()
            self.assertEqual(len(calls), old + 2)
            paint(reveal=.7)
            self.assertEqual(len(calls), old + 3)

    def test_cached_layer_touches_selected_patch_and_records_seen_role(self):
        renderer = self.window._clear_control_renderer
        rect = QRectF(70, 60, 35, 35)
        calls = []
        now = [100.]
        def draw(p):
            calls.append(1)
            renderer.paint(p, rect, scene=self.scene, scene_bounds=QRectF(self.window.rect()),
                           ink=QColor("white"), now=now[0], role="library")
        def frame():
            renderer.begin_frame(now[0])
            output = self.image()
            with QPainter(output) as painter:
                self.window._paint_control_layer(painter, rect, 1., QPointF(), ("library",), draw)
            renderer.end_frame()
        with patch("local.music_surface_window.time.monotonic", new=lambda: now[0]), \
                patch.object(self.window, "_surface_glass_backdrop", return_value=self.scene):
            frame()
            now[0] += 1.
            frame()
            self.assertEqual(len(calls), 1)
            self.assertEqual(renderer.seen_roles, {"library"})
            self.assertEqual(renderer.patches["library"][2], now[0])

    def test_original_transport_path_has_no_pixels_outside_silhouette(self):
        for playing in (False, True):
            for command in ("previous", "toggle_play_pause", "next"):
                image = self.image()
                with patch.object(self.window._clear_control_renderer, "paint", wraps=self.window._clear_control_renderer.paint) as draw, \
                        patch.object(self.window, "_display_playing_state", return_value=playing), \
                        patch("local.music_surface_window.time.monotonic", return_value=100.):
                    with QPainter(image) as p:
                        self.window._paint_transport_icon(p, QRectF(100, 60, 40, 40), command, QColor("white"))
                shape = draw.call_args.kwargs["path"]
                stroke = QPainterPathStroker()
                # Include the two reconstruction filters' antialias fringe;
                # the removed downward contact shadow extended beyond it.
                stroke.setWidth(2.5)
                silhouette = shape.united(stroke.createStroke(shape))
                painted = 0
                for y in range(image.height()):
                    for x in range(image.width()):
                        if image.pixelColor(x, y).alpha():
                            self.assertTrue(silhouette.contains(QPointF(x + .5, y + .5)),
                                (playing, command, x, y, image.pixelColor(x, y).alpha(), shape.boundingRect()))
                            painted += 1
                self.assertGreater(painted, 30)
                if command == "toggle_play_pause" and playing:
                    self.assertEqual(image.pixelColor(120, 80).alpha(), 0)

    def test_glyph_geometry_cache_survives_background_movement_growth_and_level(self):
        control_geometry.cache_clear()
        self.window._glass_feedback.press("media:next", QPointF(120, 80), 100.)
        for index in range(12):
            self.scene.fill(QColor(30 + index * 4, 70, 120))
            self.window._background_cache = self.scene
            state = self.window._glass_feedback.state("media:next")
            state.lift = index / 11.
            state.offset = QPointF(index * .137, index * -.073)
            image = self.image()
            with patch("local.music_surface_window.time.monotonic", return_value=100. + index * .02), \
                    patch.object(self.window, "_surface_glass_level", return_value=index * 8):
                with QPainter(image) as p:
                    self.window._paint_transport_icon(p, QRectF(100 + index * .137, 60, 40, 40), "next", QColor("white"))
            self.assertEqual(control_geometry.cache_info().misses, 1)
        self.assertGreaterEqual(control_geometry.cache_info().hits, 11)

    def test_circle_and_capsule_have_no_shadow_and_plus_light_is_clipped(self):
        for kind, rect in (("circle", QRectF(80, 60, 35, 35)), ("capsule", QRectF(80, 60, 90, 35))):
            for pressed in (False, True):
                image = self.image()
                state = ControlMotion(lift=1. if pressed else 0., glow=1. if pressed else 0., contact=rect.center())
                with QPainter(image) as p:
                    self.window._clear_control_renderer.paint(p, rect, scene=self.scene,
                        scene_bounds=QRectF(self.window.rect()), ink=QColor("white"), now=100.,
                        role=kind, kind=kind, state=state)
                grown = rect.adjusted(-4., -2.5, 4., 2.5) if kind == "capsule" and pressed else rect.adjusted(-2.45, -2.45, 2.45, 2.45) if pressed else rect
                for y in range(55, 108):
                    for x in range(75, 180):
                        if not grown.adjusted(-1., -1., 1., 1.).contains(QPointF(x + .5, y + .5)):
                            self.assertEqual(image.pixelColor(x, y).alpha(), 0)

    def test_all_control_compositing_uses_at_least_two_x(self):
        image = self.image()
        seen = []
        with QPainter(image) as p:
            paint_control_overlay(p, QRectF(0, 0, 100, 50), lambda layer: seen.append(layer.device().devicePixelRatioF()))
        self.assertEqual(seen, [2.])
        self.assertGreaterEqual(self.window.CONTROL_LAYER_DPR_CAP, 2.)

    def test_scene_refresh_throttles_rest_but_not_moving_controls(self):
        renderer = GlassControlRenderer()
        rect = QRectF(80, 60, 35, 35)
        def draw(now, state=None):
            image = self.image()
            with QPainter(image) as p:
                renderer.paint(p, rect, scene=self.scene, scene_bounds=QRectF(self.window.rect()),
                               ink=QColor("white"), now=now, role="one", state=state)
            return renderer.patches["one"][3].cacheKey()
        first = draw(100.)
        self.scene.fill(QColor("red"))
        self.assertEqual(draw(100.05), first)
        self.assertNotEqual(draw(100.101), first)
        last = draw(100.11)
        self.scene.fill(QColor("blue"))
        self.assertNotEqual(draw(100.12, ControlMotion(held=True)), last)

    def test_stationary_refresh_is_staggered_and_invisible_roles_do_not_block(self):
        renderer = GlassControlRenderer()
        rect = QRectF(80, 60, 35, 35)
        def frame(now, roles, moving=()):
            renderer.begin_frame(now)
            image = self.image()
            with QPainter(image) as p:
                for role in roles:
                    renderer.paint(p, rect, scene=self.scene, scene_bounds=QRectF(self.window.rect()),
                                   ink=QColor("white"), now=now, role=role,
                                   state=ControlMotion(held=True) if role in moving else None)
            renderer.end_frame()
        frame(100., ("a", "b", "c", "hidden"))
        self.scene.fill(QColor("red"))
        frame(100.11, ("a", "b", "c"))
        self.assertEqual([renderer.patches[r][2] for r in ("a", "b", "c")], [100.11, 100.11, 100.])
        frame(100.13, ("a", "b", "c"))
        self.assertEqual(renderer.patches["c"][2], 100.13)
        self.scene.fill(QColor("blue"))
        frame(100.14, ("a", "b", "c"), moving=("b",))
        self.assertEqual(renderer.patches["b"][2], 100.14)

    def test_current_scene_patch_yields_refresh_slot(self):
        renderer = GlassControlRenderer()
        rect = QRectF(80, 60, 35, 35)
        image = self.image()
        with QPainter(image) as p:
            renderer.paint(p, rect, scene=self.scene, scene_bounds=QRectF(self.window.rect()),
                           ink=QColor("white"), now=100., role="current")
            self.scene.fill(QColor("red"))
            for role in ("stale1", "stale2"):
                renderer.paint(p, rect, scene=self.scene, scene_bounds=QRectF(self.window.rect()),
                               ink=QColor("white"), now=100.01, role=role)
            self.scene.fill(QColor("blue"))
            # Make the oldest patch current while the others remain stale.
            renderer.patches["current"] = (renderer.patches["current"][0], self.scene.cacheKey(),
                                           100., renderer.patches["current"][3])
            renderer.seen_roles = {"current", "stale1", "stale2"}
            for now in (100.11, 100.13):
                renderer.begin_frame(now)
                for role in ("current", "stale1", "stale2"):
                    renderer.paint(p, rect, scene=self.scene, scene_bounds=QRectF(self.window.rect()),
                                   ink=QColor("white"), now=now, role=role)
                renderer.end_frame()
            self.assertEqual(renderer.patches["stale2"][1], self.scene.cacheKey())

    def test_paint_event_opens_and_closes_geometry_cache(self):
        self.window._frame_geometry_cache = None
        seen = []
        with patch.object(self.window, "_paint_surface_frame", side_effect=lambda event: seen.append(self.window._frame_geometry_cache)):
            self.window.paintEvent(None)
        self.assertEqual(seen, [{}])
        self.assertIsNone(self.window._frame_geometry_cache)
        self.assertIsNone(self.window._clear_control_renderer.refresh_roles)

    def test_geometry_cache_is_scoped_to_frame_and_keys_inputs(self):
        rect = QRectF(self.window.rect())
        self.window._frame_geometry_cache = {}
        with patch.object(self.window, "_personal_dj_geometry_uncached", wraps=self.window._personal_dj_geometry_uncached) as dj, \
             patch.object(self.window, "_song_collection_geometry_uncached", wraps=self.window._song_collection_geometry_uncached) as collection:
            first = self.window._personal_dj_geometry(rect, 100.)
            first[0].translate(500, 0)
            again = self.window._personal_dj_geometry(rect, 100.)
            self.assertNotEqual(first[0], again[0])
            self.window._personal_dj_geometry(rect, 100., posed=False)
            self.assertEqual(dj.call_count, 2)
            self.window._song_collection_geometry(rect, 100.)
            self.window._song_collection_geometry(rect, 100.)
            self.window._song_collection_geometry(rect, 101.)
            self.assertEqual(collection.call_count, 2)
            self.window._frame_geometry_cache = {}
            self.window._personal_dj_geometry(rect, 100.)
            self.assertEqual(dj.call_count, 3)
        self.window._frame_geometry_cache = None

    def test_mouse_press_triggers_before_release_and_hit_rects_stay_at_rest(self):
        with patch("local.music_surface_window.time.monotonic", return_value=100.):
            self.window._sync_window_control_rects()
        hits = dict(self.window._button_rects)
        key = "content_layout"
        point = hits[key].center()
        event = QMouseEvent(QEvent.Type.MouseButtonPress, point, point, Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        with patch("local.music_surface_window.time.monotonic", return_value=100.):
            self.window.mousePressEvent(event)
        self.assertTrue(self.window._glass_feedback.state(key).held)
        for index in range(1, 25):
            self.window._advance_glass_controls(100. + index / 120.)
        self.assertGreater(self.window._glass_feedback.state(key).lift, .9)
        with patch("local.music_surface_window.time.monotonic", return_value=100.2):
            self.window._sync_window_control_rects()
        self.assertEqual(self.window._button_rects, hits)

    def test_painting_does_not_advance_motion_and_shutdown_releases_caches(self):
        self.window._glass_feedback.press("library", QPointF(30, 30), 100.)
        before = self.window._glass_feedback.pose_key()
        image = self.image()
        with QPainter(image) as p, patch("local.music_surface_window.time.monotonic", return_value=100.2):
            self.window._paint_surface_glass(p, QRectF(20, 20, 35, 35), role="library")
        self.assertEqual(self.window._glass_feedback.pose_key(), before)
        self.assertTrue(self.window._clear_control_renderer.patches)
        self.window.shutdown()
        self.assertFalse(self.window._clear_control_renderer.patches)
        self.assertFalse(self.window._glass_feedback.controls)

    def test_collection_push_keeps_expanded_dj_status_text_hit_at_rest(self):
        self.owner.media_track_id = "owned:netease:1"
        self.owner._song_collection_controller = Collection()
        self.owner.data_provider = provider = _Provider()
        provider.fm = True
        provider.get_owned_personal_dj_enabled = lambda: True
        self.window.resize(960, 540)
        rect = QRectF(self.window.rect())
        with patch("local.music_surface_window.time.monotonic", return_value=100.):
            self.window._sync_owned_playback_mode()
            self.window._dj_introduction_pending = True
            self.window._personal_dj_button_rect(rect)
        with patch("local.music_surface_window.time.monotonic", return_value=101.):
            self.window._reset_control_idle(101.)
            self.window._sync_window_control_rects()
            target = QRectF(self.window._button_rects["personal_dj"])
            text = QPointF(target.left() + 16., target.center().y())
            self.assertTrue(self.window._window_control_contains("personal_dj", target, text))
            point = self.window._button_rects["favorite"].center()
            self.window._glass_feedback.press("favorite", point, 101.)
        for index in range(1, 31):
            self.window._advance_glass_controls(101. + index / 120.)
        self.assertGreater(self.window._glass_feedback.state("personal_dj").offset.x(), .5)
        with patch("local.music_surface_window.time.monotonic", return_value=101.25):
            self.window._sync_window_control_rects()
            self.assertEqual(self.window._button_rects["personal_dj"], target)
            self.assertTrue(self.window._window_control_contains("personal_dj", target, text))


if __name__ == "__main__":
    unittest.main()
