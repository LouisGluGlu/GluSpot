from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget

from local.music_navigation_transition import MusicNavigationTransition, capture_navigation_frame
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_library_controller import MusicLibraryControllerTests
from tests.test_music_surface_transition import _TransitionOwner
from tests.test_owned_playback_surface import _Provider


class _LayerWindow(QWidget):
    def __init__(self, angle=0.):
        super().__init__()
        self.angle = angle
        self.frozen = False
        self.capture_count = self.freeze_count = self.resume_count = 0
        self.cover = QImage(80, 60, QImage.Format.Format_RGB32); self.cover.fill(QColor("red"))

    def navigation_cover_state(self):
        return QRect(self.mapToGlobal(QPoint(15, 15)), self.size() / 3), self.cover, self.angle

    def freeze_navigation_cover(self):
        self.freeze_count += 1
        self.frozen = True
        return self.navigation_cover_state()

    def resume_navigation_cover(self):
        self.resume_count += 1
        self.frozen = False

    def navigation_background_frame(self, excluded_cover_rect=None):
        self.capture_count += 1
        image = QImage(self.size() * 2, QImage.Format.Format_RGB32)
        image.fill(QColor("#234567"))
        return image

    def navigation_backdrop_frame(self):
        image = QImage(self.size(), QImage.Format.Format_RGB32); image.fill(QColor("#345678"))
        return image


class MusicCoverNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.source, self.target = _LayerWindow(350.), _LayerWindow(0.)
        self.source.setGeometry(40, 60, 300, 220)
        self.target.setGeometry(140, 100, 800, 600)
        self.source.show(); self.target.show()
        self.transition = MusicNavigationTransition()

    def tearDown(self):
        self.transition.cancel(); self.transition.close()
        self.source.close(); self.target.close()

    def start(self):
        frame = capture_navigation_frame(self.source, separate_cover=True, freeze_cover=True)
        self.assertTrue(self.transition.start(frame, self.target))
        self.transition._animation.stop()
        return frame

    def test_one_independent_cover_scales_and_turns_shortest_path_on_fixed_stage(self):
        frame = self.start()
        stage = self.source.geometry().united(self.target.geometry())
        self.assertEqual(self.transition.geometry(), stage)
        self.assertEqual(self.transition._animation.duration(), 560)
        self.assertTrue(self.transition._cover_transition)
        self.transition._advance(0.)
        self.assertEqual(self.transition._cover_rect_at(), frame.cover_rect)
        self.transition._advance(.5)
        self.assertEqual(self.transition.geometry(), stage)
        self.assertAlmostEqual(self.transition._cover_angle_at(), 355.)
        self.assertGreater(self.transition._cover_rect_at().width(), frame.cover_rect.width())
        self.transition._advance(1.)
        self.assertEqual(self.transition._cover_rect_at(), self.transition._target_frame.cover_rect)
        self.assertAlmostEqual(self.transition._cover_angle_at() % 360., 0.)

    def test_frames_are_bounded_and_animation_never_recaptures_live_pages(self):
        self.source.resize(2000, 1200)
        frame = self.start()
        self.assertLessEqual(max(frame.image.width(), frame.image.height()), 1600)
        counts = self.source.capture_count, self.target.capture_count
        with patch.object(self.source, "grab", side_effect=AssertionError("per-frame grab")), \
                patch.object(self.target, "grab", side_effect=AssertionError("per-frame grab")):
            for fraction in (.05, .25, .5, .75, .95):
                self.transition._advance(fraction)
                image = QImage(self.transition.size(), QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                self.transition.render(image)
        self.assertEqual((self.source.capture_count, self.target.capture_count), counts)

    def test_cancel_restores_original_input_opacity_and_resumes_both_rotations_once(self):
        self.target.setEnabled(False)
        self.target.setWindowOpacity(.72)
        opacity = self.target.windowOpacity()
        self.start()
        self.assertFalse(self.target.isEnabled())
        self.assertEqual(self.target.windowOpacity(), 0.)
        self.assertTrue(self.source.frozen and self.target.frozen)
        self.transition.cancel(); self.transition.cancel()
        self.assertFalse(self.target.isEnabled())
        self.assertAlmostEqual(self.target.windowOpacity(), opacity)
        self.assertEqual((self.source.resume_count, self.target.resume_count), (1, 1))
        self.assertTrue(self.transition._origin.isNull() and self.transition._target.isNull())

    def test_hidden_destination_cancels_and_reenables_a_previously_enabled_page(self):
        self.start()
        self.assertFalse(self.target.isEnabled())
        self.target.hide()
        self.assertFalse(self.transition.isVisible())
        self.assertTrue(self.target.isEnabled())
        self.assertEqual(self.target.windowOpacity(), 1.)

    def test_invalid_destination_releases_frozen_source_without_changing_music(self):
        frame = capture_navigation_frame(self.source, separate_cover=True, freeze_cover=True)
        self.target.hide()
        self.assertFalse(self.transition.start(frame, self.target))
        self.assertFalse(self.source.frozen)
        self.assertEqual(self.source.resume_count, 1)

    def test_failed_layer_capture_releases_frozen_source(self):
        with patch.object(self.source, "navigation_background_frame", side_effect=RuntimeError("capture unavailable")):
            self.assertIsNone(capture_navigation_frame(self.source, separate_cover=True, freeze_cover=True))
        self.assertFalse(self.source.frozen)
        self.assertEqual(self.source.resume_count, 1)

    def test_missing_backdrop_cache_keeps_the_middle_of_the_handoff_opaque(self):
        with patch.object(self.source, "navigation_backdrop_frame", return_value=QImage()), \
                patch.object(self.target, "navigation_backdrop_frame", return_value=QImage()):
            self.start()
        self.transition._advance(.5)
        image = QImage(self.transition.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.transition.render(image)
        window = self.transition._rect_between(self.source.geometry(), self.target.geometry(), .5)
        point = window.bottomRight() - self.transition.geometry().topLeft() - QPoint(25, 25)
        self.assertEqual(image.pixelColor(point.toPoint()).alpha(), 255)

    def test_missing_cache_uses_existing_window_palette_behind_a_transparent_ui_layer(self):
        foreground = QImage(self.source.size(), QImage.Format.Format_ARGB32_Premultiplied)
        foreground.fill(Qt.GlobalColor.transparent)
        with patch.object(self.source, "navigation_backdrop_frame", return_value=QImage()), \
                patch.object(self.source, "navigation_background_frame", return_value=foreground):
            frame = capture_navigation_frame(self.source, separate_cover=True)
        self.assertEqual(frame.image.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(frame.background.pixelColor(0, 0), self.source.palette().window().color())
        self.assertEqual(frame.background.pixelColor(0, 0).alpha(), 255)


class MusicSurfaceNavigationCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.clock_patch = patch("local.music_surface_window.time.monotonic", return_value=100.)
        self.clock = self.clock_patch.start()
        self.owner = _TransitionOwner()
        self.owner.data_provider = _Provider()
        self.owner.album_art = QImage(200, 200, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor("#ff00ff"))
        self.window = MusicSurfaceWindow(self.owner)
        self.window.setGeometry(60, 70, 960, 540)
        self.background = QColor("#112233")
        self.background_patch = patch.object(self.window, "_paint_background",
            side_effect=lambda painter, rect, *args, **kwargs:
                None if getattr(self.window, "_navigation_cover_capture", False)
                else painter.fillRect(rect, self.background))
        self.background_patch.start()
        self.window.show()
        self.window._timer.stop(); self.window._audio_output_status_timer.stop()

    def tearDown(self):
        self.background_patch.stop()
        self.window.shutdown(); self.owner.close()
        self.clock_patch.stop()

    def prepare(self, layout):
        self.window.set_content_layout(layout, animate=False)
        self.window._layout_animation.stop()
        self.window._finish_content_layout()
        self.window._cover_layout_from = self.window._cover_layout_to = float(self.window._no_lyrics_cover_target())
        self.window.grab()
        self.clock.return_value = 120.

    @staticmethod
    def crop(image, rect):
        dpr = image.devicePixelRatio()
        return image.copy(QRect(round(rect.x() * dpr), round(rect.y() * dpr),
                                round(rect.width() * dpr), round(rect.height() * dpr)))

    def test_actual_surface_capture_removes_cover_and_cover_lyrics_then_restores_regular_grab(self):
        for layout in ("cover", "cover_lyrics"):
            with self.subTest(layout=layout):
                self.prepare(layout)
                cover, image, angle = self.window.navigation_cover_state()
                self.assertEqual(image, self.owner.album_art)
                self.assertEqual(angle, 0.)
                local = QRect(self.window.mapFromGlobal(cover.topLeft()), cover.size()).adjusted(12, 12, -12, -12)
                regular = self.crop(self.window.grab().toImage(), local)
                if layout == "cover_lyrics":
                    self.assertTrue(self.window._lyric_hit_rects)
                stripped = self.crop(self.window.navigation_background_frame(), local)
                self.assertNotEqual(regular, stripped)
                for x in range(0, stripped.width(), max(1, stripped.width() // 30)):
                    for y in range(0, stripped.height(), max(1, stripped.height() // 30)):
                        self.assertEqual(stripped.pixelColor(x, y).alpha(), 0)
                self.assertFalse(self.window._navigation_cover_capture)
                self.assertEqual(self.crop(self.window.grab().toImage(), local), regular)

    def test_actual_surface_capture_keeps_external_lyrics(self):
        self.prepare("lyrics")
        regular = self.window.grab().toImage()
        lyric_rect = QRectF(self.window._lyric_clip_rect).toAlignedRect()
        self.assertTrue(lyric_rect.isValid())
        self.assertTrue(self.window._lyric_hit_rects)
        stripped = self.window.navigation_background_frame()
        self.assertTrue(self.window._lyric_hit_rects)
        transparent_lyrics, full_lyrics = self.crop(stripped, lyric_rect), self.crop(regular, lyric_rect)
        normal_ink, layer_ink = set(), set()
        for y in range(0, transparent_lyrics.height(), 2):
            for x in range(0, transparent_lyrics.width(), 2):
                if full_lyrics.pixelColor(x, y) != self.background:
                    normal_ink.add((x, y))
                if transparent_lyrics.pixelColor(x, y).alpha() > 0:
                    layer_ink.add((x, y))
        self.assertGreater(len(layer_ink), 100)
        # Transparent rasters use grayscale antialiasing; opaque Qt grabs may
        # use LCD edges, while the same lyric glyphs retain their geometry.
        self.assertGreater(len(normal_ink & layer_ink) / len(normal_ink | layer_ink), .94)

    def test_failed_capture_restores_regular_cover_painting(self):
        self.prepare("cover_lyrics")
        with patch.object(self.window, "grab", side_effect=RuntimeError("capture unavailable")):
            with self.assertRaises(RuntimeError):
                self.window.navigation_background_frame()
        self.assertFalse(self.window._navigation_cover_capture)


class MusicCoverNavigationControllerTests(MusicLibraryControllerTests):
    # Reuse the established account/player fixtures without re-running the
    # inherited controller suite from this dedicated module.
    def test_top_rotating_cover_forces_window_without_restart_even_when_paused(self):
        self.open()
        self.player.play_queue((self.song,))
        self.player.track = replace(self.player.track, is_playing=False)
        self.window.set_now_playing(self.player.track)
        self.settings.set("v2_library_last_playback_surface", "fullscreen")
        before = tuple(self.player.calls)
        self.island.calls.clear()
        self.assertTrue(self.controller.open_now_playing())
        self.assertEqual(tuple(self.player.calls), before)
        surface = next(call for call in self.island.calls if call[0] == "surface")
        self.assertFalse(surface[1]["fullscreen"])
        self.assertFalse(surface[1]["origin_geometry"].isValid())
        self.assertEqual(self.settings.get("v2_library_last_playback_surface"), "window")
        self.assertFalse(self.window.isVisible())

    def test_surface_open_failure_keeps_library_visible_and_releases_rotating_cover(self):
        self.open()
        self.player.play_queue((self.song,))
        current, queue = self.player.track, self.player.queue
        self.island._show_music_fullscreen = Mock()
        self.assertFalse(self.controller.open_now_playing())
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.island._music_library_active)
        self.assertIs(self.player.track, current)
        self.assertIs(self.player.queue, queue)
        self.assertEqual(self.window.windowOpacity(), 1.)

    def test_preparing_playlist_retains_its_enlarged_cover_and_error_restores_input(self):
        self.open()
        cover = self.window._shelves[0].tiles[0][1]
        cover.click()
        self.assertTrue(cover._opening_pose)
        self.assertFalse(cover.isEnabled())
        cover._animation.setCurrentTime(cover._animation.duration())
        cover._retarget(0.)
        self.assertEqual(cover._animation.endValue(), 1.)
        rect, image, angle = self.window.navigation_opening_cover_state()
        self.assertEqual(rect.width(), cover.width())
        self.assertFalse(image.isNull())
        self.player._set_status("catalog_unavailable")
        self.assertFalse(cover._opening_pose)
        self.assertTrue(cover.isEnabled())
        self.assertIsNone(self.window._opening_cover)
        self.assertTrue(self.window.isVisible())

    def test_playable_playlist_handoff_uses_actual_prepared_pose_and_tile_radius(self):
        self.open()
        cover = self.window._shelves[0].tiles[0][1]
        cover.click()
        cover._animation.setCurrentTime(cover._animation.duration())
        with patch.object(self.controller, "_present_music_surface", return_value=True) as present:
            self.player.play_queue((self.song,))
        mode, frame = present.call_args.args
        self.assertEqual(mode, "fullscreen")
        self.assertEqual(frame.cover_rect.width(), cover.width())
        self.assertAlmostEqual(frame.cover_roundness * frame.cover_rect.width(), 14.)
        self.assertFalse(frame.cover_image.isNull())
        self.assertFalse(cover._opening_pose)
        self.assertIsNone(self.window._opening_cover)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(MusicCoverNavigationTests))
    suite.addTests(loader.loadTestsFromTestCase(MusicSurfaceNavigationCaptureTests))
    for name in ("test_top_rotating_cover_forces_window_without_restart_even_when_paused",
                 "test_surface_open_failure_keeps_library_visible_and_releases_rotating_cover",
                 "test_preparing_playlist_retains_its_enlarged_cover_and_error_restores_input",
                 "test_playable_playlist_handoff_uses_actual_prepared_pose_and_tile_radius"):
        suite.addTest(MusicCoverNavigationControllerTests(name))
    return suite
