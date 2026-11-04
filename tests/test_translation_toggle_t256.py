from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from local.music_lyric_motion import LyricEmphasisPlan
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_lyric_visual_layers import _LayerOwner


def _toggle_ease(fraction: float) -> float:
    """Independently invert the specified CSS cubic-bezier's x coordinate."""
    low, high = 0.0, 1.0
    for _ in range(45):
        parameter = (low + high) * 0.5
        inverse = 1.0 - parameter
        x = 3.0 * inverse * inverse * parameter * .28 + 3.0 * inverse * parameter * parameter * .72 + parameter ** 3
        if x < fraction:
            low = parameter
        else:
            high = parameter
    parameter = (low + high) * .5
    return 3.0 * (1.0 - parameter) * parameter * parameter + parameter ** 3


class TranslationToggleT256Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        private = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="gluspot-t256-test-")))
        environment = {"GLUSPOT_TRANSLATION_VARIANT": "classic"}
        for name in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
            environment[name] = str(private / name)
        self.stack.enter_context(patch.dict(os.environ, environment))
        for name, result in (("lyric_emphasis_plan", LyricEmphasisPlan()), ("explicit_slow_unit_groups", ())):
            self.stack.enter_context(patch("local.music_surface_window." + name, return_value=result))

    def _surface(self, size=(960, 540), *, translated=False, layout="lyrics"):
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            owner = _LayerOwner()
            owner.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            owner._music_windowed_preference = size[0] < 1920
            owner.enable_dual_line_lyrics = translated
            owner.media_is_playing = False
            owner.media_position_source = "local-seek"
            owner.media_position_seconds = owner.media_position_observed_seconds = 164.0
            owner.media_position_sampled_at = 100.0
            owner.media_duration_seconds = 1920.0
            owner._estimated_media_position = lambda _now: owner.media_position_seconds
            owner.timed_lyrics = [
                (index * 20.0, f"A steady lyric sentence number {index} stays visible\n翻译：第{index}句的译文一直保持可见")
                for index in range(96)
            ]
            owner.timed_lyric_units = [()] * len(owner.timed_lyrics)
            owner.timed_lyric_timing_kinds = ["line"] * len(owner.timed_lyrics)
            owner.set_enable_dual_line_lyrics = lambda enabled: setattr(owner, "enable_dual_line_lyrics", enabled)
            owner.album_color = QColor("#27354a")
            owner.secondary_color = QColor("#263044")
            window = MusicSurfaceWindow(owner)
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            window.resize(*size)
            window._fullscreen = size[0] >= 1920
            window.set_content_layout(layout, animate=False)
            for timer in window.findChildren(QTimer):
                timer.stop()
        self.addCleanup(owner.close)
        self.addCleanup(window.shutdown)
        return owner, window

    def _render(self, owner, window, now):
        owner.media_position_sampled_at = now
        primary = "A steady lyric sentence number 8 stays visible"
        painted = []
        read = window._paint_wrapped_lifted_lyric
        whole = window._draw_sprung_cached_wrapped_layout
        future = window._draw_future_wrapped_layout

        def observe(painter, layout, origin, text, offsets):
            if text != primary:
                return
            first = layout.lineAt(0)
            offset = offsets[0] if offsets else 0.0
            top = painter.transform().map(QPointF(origin.x(), origin.y() + first.y() + offset)).y()
            painted.append((top, first.height(), painter.opacity()))

        def read_observer(painter, row, text, font, layout, progress, *args, **kwargs):
            observe(painter, layout, row.topLeft(), text, kwargs.get("line_offsets", ()))
            return read(painter, row, text, font, layout, progress, *args, **kwargs)

        def whole_observer(painter, layout, origin, text, font, width, height, color, offsets, *args, **kwargs):
            observe(painter, layout, origin, text, offsets)
            return whole(painter, layout, origin, text, font, width, height, color, offsets, *args, **kwargs)

        def future_observer(painter, layout, origin, text, font, width, height, offsets=(), *args, **kwargs):
            observe(painter, layout, origin, text, offsets)
            return future(painter, layout, origin, text, font, width, height, offsets, *args, **kwargs)

        with (
            patch("local.music_surface_window.time.monotonic", return_value=now),
            patch.object(window, "_paint_wrapped_lifted_lyric", side_effect=read_observer),
            patch.object(window, "_draw_sprung_cached_wrapped_layout", side_effect=whole_observer),
            patch.object(window, "_draw_future_wrapped_layout", side_effect=future_observer),
        ):
            image = QImage(window.width(), window.height(), QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.black)
            window.render(image)
        return image, painted

    def _warm(self, owner, window):
        self._render(owner, window, 100.0)
        window._advance_song_layout_build(max_rows=None)
        for frame in range(1, 61):
            image, painted = self._render(owner, window, 100.0 + frame / 60.0)
        self.assertTrue(painted, "The fixture must show the actual current primary before toggling")
        return image, painted

    @staticmethod
    def _toggle(window, now):
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            window._toggle_translation()

    @staticmethod
    def _bright_current_pixels(image, window, top, height):
        pixels = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(image.height(), image.bytesPerLine())
        pixels = pixels[:, :image.width() * 4].reshape(image.height(), image.width(), 4)
        clip = window._lyric_clip_rect
        left, right = max(0, int(clip.left())), min(image.width(), int(clip.right()) + 1)
        start, stop = max(0, int(top)), min(image.height(), int(top + height) + 1)
        return int(np.count_nonzero(pixels[start:stop, left:right, :3].min(axis=2) > 190))

    def test_long_song_toggle_has_primary_ink_on_every_frame_and_no_layout_rebuild(self):
        for size in ((960, 540), (1920, 1080)):
            for translated in (False, True):
                with self.subTest(size=size, opening=not translated):
                    owner, window = self._surface(size, translated=translated)
                    before, current = self._warm(owner, window)
                    top, height, _ = current[0]
                    bright_before = self._bright_current_pixels(before, window, top, height)
                    self.assertGreater(bright_before, 50)
                    relayouts = window._lyric_relayout_count
                    layouts = window._song_layouts
                    self._toggle(window, 101.01)
                    for frame in range(26):
                        image, painted = self._render(owner, window, 101.01 + frame / 60.0)
                        self.assertTrue(painted, f"Translation toggle erased primary ink on frame {frame}")
                        y, row_height, opacity = painted[0]
                        self.assertAlmostEqual(y, top, delta=.5, msg=f"Current-line anchor jumped on frame {frame}")
                        self.assertAlmostEqual(opacity, current[0][2], delta=.001)
                        bright = self._bright_current_pixels(image, window, y, row_height)
                        self.assertGreaterEqual(bright, bright_before * .85, f"Current lyric raster flashed on frame {frame}")
                    self.assertEqual(window._lyric_relayout_count, relayouts)
                    self.assertIs(window._song_layouts, layouts)

    def test_toggle_height_and_opacity_follow_requested_320ms_cubic_in_both_directions(self):
        for translated in (False, True):
            with self.subTest(opening=not translated):
                owner, window = self._surface(translated=translated)
                self._warm(owner, window)
                text = "第8句的译文一直保持可见"
                self._toggle(window, 101.01)
                for fraction in (0.0, .25, .5, .75, 1.0):
                    now = 101.01 + .320 * fraction
                    self._render(owner, window, now)
                    height, opacity, _ = window._translation_layer_pose(8, text, now)
                    expected = _toggle_ease(fraction)
                    if translated:
                        expected = 1.0 - expected
                    self.assertAlmostEqual(height, expected, delta=.002)
                    self.assertAlmostEqual(opacity, expected, delta=.002)

    def test_cover_lyrics_toggle_keeps_current_primary_anchored_through_settlement(self):
        for size in ((960, 540), (1920, 1080)):
            for translated in (False, True):
                with self.subTest(size=size, opening=not translated):
                    owner, window = self._surface(size, translated=translated, layout="cover_lyrics")
                    _, before = self._warm(owner, window)
                    self._toggle(window, 101.01)
                    for frame in range(49):
                        _, painted = self._render(owner, window, 101.01 + frame / 60.0)
                        self.assertTrue(painted, f"Cover lyric disappeared on frame {frame}")
                        self.assertAlmostEqual(
                            painted[0][0], before[0][0], delta=.5,
                            msg=f"Cover lyric primary moved on frame {frame}",
                        )

    def test_provider_removing_translation_on_close_never_erases_current_primary(self):
        owner, window = self._surface(translated=True)
        _, before = self._warm(owner, window)
        self._toggle(window, 101.01)
        owner.timed_lyrics = [(stamp, text.split("\n", 1)[0]) for stamp, text in owner.timed_lyrics]
        for frame in range(49):
            _, painted = self._render(owner, window, 101.01 + frame / 60.0)
            self.assertTrue(painted, f"Provider translation removal erased current primary on frame {frame}")
            self.assertAlmostEqual(painted[0][0], before[0][0], delta=.5)
        self.assertIsNone(window._translation_exit_source)

    def _deliver_delayed_translation(self, size=(960, 540)):
        owner, window = self._surface(size)
        bilingual = owner.timed_lyrics
        owner.timed_lyrics = [(stamp, text.split("\n", 1)[0]) for stamp, text in bilingual]
        before, current = self._warm(owner, window)
        top, height, _ = current[0]
        bright_before = self._bright_current_pixels(before, window, top, height)
        self._toggle(window, 101.01)
        for now in (101.01, 101.20):
            _, painted = self._render(owner, window, now)
            self.assertTrue(painted)
            self.assertAlmostEqual(painted[0][0], top, delta=.5)
            self.assertIsNone(window._song_secondary_layouts[8])
        # Delivery is later than the original click's entire animation. The
        # bounded companion build must keep the already shaped primary ink.
        owner.timed_lyrics = bilingual
        saw_pending_companion = False
        for frame in range(121):
            now = 101.50 + frame / 60.0
            image, painted = self._render(owner, window, now)
            self.assertTrue(painted, f"Delayed translation erased primary ink on frame {frame}")
            self.assertAlmostEqual(painted[0][0], top, delta=.5)
            bright = self._bright_current_pixels(image, window, painted[0][0], painted[0][1])
            self.assertGreaterEqual(bright, bright_before * .85)
            if window._song_secondary_layouts[8] is not None:
                self.assertTrue(saw_pending_companion, "This long-song fixture must exercise the bounded build")
                return owner, window, top, now
            saw_pending_companion = True
        self.fail("The bounded companion build never promoted its translated layouts")

    def test_delayed_provider_translation_keeps_primary_then_animates_from_arrival(self):
        for size in ((960, 540), (1920, 1080)):
            with self.subTest(size=size):
                owner, window, top, arrived = self._deliver_delayed_translation(size)
                text = "第8句的译文一直保持可见"
                self.assertEqual(window._translation_layer_pose(8, text, arrived), (0.0, 0.0, 0.0))
                for fraction in (.25, .5, .75, 1.0):
                    now = arrived + .320 * fraction
                    _, painted = self._render(owner, window, now)
                    self.assertTrue(painted)
                    self.assertAlmostEqual(painted[0][0], top, delta=.5)
                    pose = window._translation_layer_pose(8, text, now)
                    expected = _toggle_ease(fraction)
                    self.assertAlmostEqual(pose[0], expected, delta=.002)
                    self.assertAlmostEqual(pose[1], expected, delta=.002)

    def test_delayed_provider_translation_can_reverse_during_entrance(self):
        owner, window, top, arrived = self._deliver_delayed_translation()
        text = "第8句的译文一直保持可见"
        now = arrived + .080
        _, painted = self._render(owner, window, now)
        pose = window._translation_layer_pose(8, text, now)
        self.assertAlmostEqual(pose[0], _toggle_ease(.25), delta=.002)
        self._toggle(window, now)
        _, after = self._render(owner, window, now)
        self.assertEqual(window._translation_layer_pose(8, text, now), pose)
        self.assertTrue(painted and after)
        self.assertAlmostEqual(after[0][0], top, delta=.5)
        now += .080
        _, painted = self._render(owner, window, now)
        collapsed = window._translation_layer_pose(8, text, now)
        expected = pose[0] * (1.0 - _toggle_ease(.25))
        self.assertAlmostEqual(collapsed[0], expected, delta=.002)
        self.assertAlmostEqual(collapsed[1], expected, delta=.002)
        self._toggle(window, now)
        _, after = self._render(owner, window, now)
        self.assertEqual(window._translation_layer_pose(8, text, now), collapsed)
        self.assertTrue(painted and after)
        self.assertAlmostEqual(after[0][0], top, delta=.5)

    def test_rapid_reversals_preserve_current_height_opacity_and_painted_anchor(self):
        owner, window = self._surface()
        _, initial = self._warm(owner, window)
        text = "第8句的译文一直保持可见"
        now = 101.01
        self._toggle(window, now)
        self._render(owner, window, now)
        for elapsed in (.08, .06, .09, .07):
            now += elapsed
            _, before = self._render(owner, window, now)
            before_pose = window._translation_layer_pose(8, text, now)
            self.assertGreater(before_pose[0], .0)
            self.assertLess(before_pose[0], 1.0)
            self._toggle(window, now)
            _, after = self._render(owner, window, now)
            after_pose = window._translation_layer_pose(8, text, now)
            for old, new in zip(before_pose, after_pose):
                self.assertAlmostEqual(new, old, delta=1e-7)
            self.assertTrue(before and after)
            self.assertAlmostEqual(before[0][0], after[0][0], delta=.001)
            self.assertAlmostEqual(after[0][0], initial[0][0], delta=.5)
        self._render(owner, window, now + .321)
        destination = 1.0 if owner.enable_dual_line_lyrics else 0.0
        self.assertEqual(window._translation_layer_pose(8, text, now + .321)[:2], (destination, destination))

    def test_reversal_between_paints_samples_current_animation_value(self):
        owner, window = self._surface()
        self._warm(owner, window)
        self._toggle(window, 101.01)
        self._render(owner, window, 101.01)
        # There is deliberately no intervening paint or pose sample before
        # this click. The retarget must evaluate the running curve itself.
        self._toggle(window, 101.122)
        pose = window._translation_layer_pose(8, "第8句的译文一直保持可见", 101.122)
        expected = _toggle_ease(.112 / .320)
        self.assertAlmostEqual(pose[0], expected, delta=.002)
        self.assertAlmostEqual(pose[1], expected, delta=.002)
        later = window._translation_layer_pose(8, "第8句的译文一直保持可见", 101.202)
        self.assertLess(later[0], pose[0])
        self.assertGreater(later[0], 0.0)


if __name__ == "__main__":
    unittest.main()
