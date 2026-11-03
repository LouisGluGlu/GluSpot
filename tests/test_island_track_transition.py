"""Track identity, cover projection and crest-light regressions for the island."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QHideEvent, QImage, QPainter, QShowEvent, QTransform
from PySide6.QtWidgets import QApplication

from api.audio_analyzer import analyze_frequency_block
from api.models import ColorToken, IslandVisualData, TrackData
from api.providers import EmptyIslandDataProvider
from local.island_stack_visual import IslandStackVisual
from local.music_background_renderer import NativeMusicBackgroundRenderer
from local.sea_wave import SeaWaveState
from tests.test_music_background_renderer import PaintHost


class VocalRippleTests(unittest.TestCase):
    def test_normal_ripple_recognizes_center_band_without_lifting_quiet_input(self):
        samples = np.arange(2205) / 44100
        def state(frequency, amplitude, side=False):
            signal = amplitude * np.sin(np.pi * 2 * frequency * samples)
            frame = analyze_frequency_block(np.column_stack((signal, -signal if side else signal)),
                                            44100, np, [0., 0., 0.])
            result = SeaWaveState()
            for n in range(90):
                result.update_ripple(frame.peak, n / 30, vocal=frame.vocal)
            return result
        centered, bass, side, quiet = (state(1000, .25), state(60, .25),
                                      state(1000, .25, True), state(1000, .0004))
        self.assertGreater(centered.ripple_level, bass.ripple_level + .15)
        self.assertGreater(centered.ripple_level, side.ripple_level + .15)
        self.assertLess(np.ptp([quiet.ripple_displacement(i / 48) for i in range(49)]), .8)

    def test_vocal_contribution_keeps_attack_release_pause_and_unknown_contracts(self):
        state = SeaWaveState()
        state.update_ripple(0., 10.)
        state.update_ripple(.04, 10.06, vocal=.4)
        target = .2 + 1.4 * (.4 - .02)
        self.assertAlmostEqual(state.ripple_level, target * (1 - np.exp(-1)))
        level = state.ripple_level
        state.update_ripple(.04, 10.31, vocal=.4, is_playing=False)
        self.assertAlmostEqual(state.ripple_level, level / np.e)
        unknown = SeaWaveState()
        unknown.update_ripple(None, 10., vocal=1.)
        self.assertLess(unknown.ripple_level, .07)


class IslandTrackTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.visual = IslandStackVisual(EmptyIslandDataProvider())
        self.visual.timer.stop()
        self.visual._u = float
        self.visual._navigation_surface_blocks_island = lambda: False
        self.visual._sync_timed_lyrics = lambda *_args, **_kwargs: None
        self.addCleanup(self.visual.close)

    @staticmethod
    def track(identity, color):
        art = QImage(128, 128, QImage.Format.Format_RGB32)
        art.fill(QColor(color))
        with QPainter(art) as painter:
            # Asymmetric marker detects accidental mirroring of the new face.
            painter.fillRect(QRectF(10, 20, 18, 30), QColor("yellow"))
        data = QBuffer()
        data.open(QIODevice.OpenModeFlag.WriteOnly)
        art.save(data, "PNG")
        return TrackData(title=identity, artist="Fixture", lyrics=("A sung line", "Another line"),
                         album_color=ColorToken(30, 110, 170), secondary_color=ColorToken(20, 60, 80),
                         audio_frames=(), album_art_bytes=bytes(data.data()), track_id=identity,
                         is_playing=False, duration_seconds=180.)

    def apply(self, track, now, initial=False):
        with patch("local.island_stack_visual.time.monotonic", return_value=now):
            self.visual._apply_visual_data(IslandVisualData(track, (), ()), initial=initial)

    def switch(self):
        self.apply(self.track("old", "#bb1830"), 10., initial=True)
        self.apply(self.track("new", "#126ec0"), 11.)

    def render(self, progress, side=80, dpr=1):
        image = QImage(120 * dpr, 120 * dpr, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.visual._paint_island_track_cover(painter, QRectF(20, 20, side, side), 8, progress=progress)
        return image

    def test_initial_upgrade_and_paused_track_identity(self):
        old = self.track("old", "#bb1830")
        self.apply(old, 10., initial=True)
        self.assertEqual(self.visual._island_track_transition_progress(10.), 1.)
        self.apply(self.track("new", "#126ec0"), 11.)
        started = self.visual._island_track_transition_started_at
        self.assertEqual(started, 11.)
        self.assertFalse(self.visual.media_is_playing)
        self.assertTrue(self.visual._active_visual_animation_plan(11.1, include_media=False).active)
        self.apply(self.track("new", "#088f80"), 11.1)
        self.assertEqual(self.visual._island_track_transition_started_at, started)
        self.assertGreater(self.visual._island_track_previous_cover.pixelColor(64, 64).red(), 150)

    def test_faces_squeeze_to_edge_and_new_marker_is_not_mirrored_at_both_sizes(self):
        self.switch()
        for side in (28, 80):
            for dpr in (1, 2):
                old, tilted, edge, incoming, new = [self.render(p, side, dpr) for p in (0., .35, .5, .65, 1.)]
                alpha_width = lambda image: sum(any(image.pixelColor(x, y).alpha() > 100 for y in range(image.height()))
                                                for x in range(image.width()))
                self.assertGreater(alpha_width(old), alpha_width(tilted))
                self.assertEqual(alpha_width(edge), 0)
                self.assertLess(alpha_width(incoming), alpha_width(new))
                center = new.pixelColor(round((20 + side * .5) * dpr), round((20 + side * .5) * dpr))
                self.assertGreater(center.blue(), center.red() * 2)
                marker = new.pixelColor(round((20 + side * .15) * dpr), round((20 + side * .27) * dpr))
                self.assertGreater(marker.red(), 170)
                self.assertLess(marker.blue(), 100)

    def test_interrupted_switch_snapshots_visible_pose_and_release_is_bounded(self):
        self.switch()
        expected = self.visual._island_track_cover_snapshot(11.2)
        self.apply(self.track("third", "#008760"), 11.2)
        self.assertEqual(self.visual._island_track_previous_cover, expected)
        self.assertLessEqual(self.visual._island_track_previous_cover.width(), 192)
        self.assertEqual(self.visual._island_track_transition_progress(11.84), 1.)
        self.visual._clear_island_track_transition()
        self.assertTrue(self.visual._island_track_previous_cover.isNull())

    def test_parent_transform_and_clip_survive_projected_cover(self):
        self.switch()
        image = QImage(150, 150, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            painter.translate(7, 11)
            painter.setClipRect(QRectF(0, 0, 55, 120))
            transform = QTransform(painter.worldTransform())
            self.visual._paint_island_track_cover(painter, QRectF(20, 20, 80, 80), 8, progress=.28)
            self.assertEqual(painter.worldTransform(), transform)
            self.assertTrue(painter.hasClipping())
        self.assertEqual(image.pixelColor(70, 55).alpha(), 0)

    def test_hide_show_and_no_media_cancel_instead_of_replaying(self):
        self.switch()
        self.visual.hideEvent(QHideEvent())
        self.assertEqual(self.visual._island_track_transition_progress(11.1), 1.)
        self.apply(self.track("third", "#008760"), 11.2)
        with patch.object(self.visual, "_center_on_screen"), patch.object(self.visual, "_force_super_topmost"):
            self.visual.showEvent(QShowEvent())
        self.assertEqual(self.visual._island_track_transition_progress(11.3), 1.)
        self.apply(replace(self.track("empty", "black"), album_art_bytes=b"", position_source="none",
                           is_playing=False, duration_seconds=0.), 12.)
        self.assertTrue(self.visual._island_track_previous_cover.isNull())

    def test_sea_transition_stays_palette_tinted_without_whole_shell_wash(self):
        self.switch()
        self.assertEqual(self.visual.music_surface_transition_from[0], QColor(30, 110, 170))
        image = QImage(220, 70, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            self.visual._paint_music_surface_transition_wash(painter, QRectF(0, 0, 220, 70), 18, False)
        self.assertEqual(image.pixelColor(110, 35).alpha(), 0)

    def test_live_palette_matches_both_sizes_and_dpr_cache_tracks_traveling_light(self):
        host = PaintHost({})
        host._sea_wave_state = SeaWaveState()
        renderer = NativeMusicBackgroundRenderer(host)
        for color in ("#ca102d", "#c01a91", "#087d86"):
            host._xiaomi_duotone_palette = lambda: (QColor(color),) * 3
            colors = [renderer._sea_level_colors(renderer.render_context("sea_level", expanded=size), size)
                      for size in (True, False)]
            self.assertEqual(colors[0], colors[1])
            self.assertAlmostEqual(colors[0][0].hslHueF(), QColor(color).hslHueF(), places=3)
        frames = []
        for progress in (.2, .7):
            host._island_track_transition_progress = lambda _now: progress
            frame = QImage(1040, 272, QImage.Format.Format_ARGB32_Premultiplied)
            frame.setDevicePixelRatio(2)
            frame.fill(Qt.GlobalColor.transparent)
            with QPainter(frame) as painter:
                renderer.paint_sea_level_wave(painter, QRectF(0, 0, 520, 136), 24, True)
            frames.append(frame)
        self.assertNotEqual(frames[0], frames[1])


if __name__ == "__main__":
    unittest.main()
