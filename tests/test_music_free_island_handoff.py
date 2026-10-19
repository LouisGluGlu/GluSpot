"""A squeeze preview is disposable; only its completed handoff places Free."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from api.providers import EmptyIslandDataProvider, empty_visual_data
from local.audio_reactive_engine import ReactiveFrame, ReactiveSnapshot
from local.island_stack_visual import IslandStackVisual, SpringRect
from local.music_lyric_motion import LogicalLineSpringChain, MusicLyricMotion
from local.music_lyric_reading import LyricReadingRenderer


class MusicFreeIslandHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.island = IslandStackVisual(EmptyIslandDataProvider(empty_visual_data()))
        self.island.timer.stop()
        self.island.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.island._force_super_topmost = lambda: None
        self.island._mydockfinder_available = lambda: False
        self.island._finder_bar_available = lambda: False
        self.island._effective_lyric_offset_seconds = lambda: 0.
        self.island.settings_changed_callback = Mock()
        self.island.music_playing = True
        self.island.music_auto_detect = False
        self.island.media_is_playing = True
        self.island.media_track_id = 'owned:fixture:squeeze'
        self.island.track_title = 'A continuing song'
        self.island.artist = 'Fixture'
        self.island.media_position_seconds = 5.
        self.island.media_position_observed_seconds = 5.
        self.island.media_position_sampled_at = 100.
        self.island.media_position_sync_at = 100.
        self.island.media_duration_seconds = 60.
        self.island.media_position_source = 'local-playback'
        self.island.lyrics = ['Before', 'Music continues\n译文继续', 'After']
        self.island.timed_lyrics = [(0., self.island.lyrics[0]), (4., self.island.lyrics[1]),
                                    (30., self.island.lyrics[2])]
        self.island.timed_lyric_units = [(), (), ()]
        self.island.timed_lyric_timing_kinds = ['line'] * 3
        self.island.lyric_index = self.island.prev_lyric_index = 1
        self.island.music_ambience_style = 'pure_black'
        self.island.enable_dual_line_lyrics = True
        self.island._music_surface_active = True
        self.island.music_fullscreen_overlay = SimpleNamespace(hide=Mock(), isVisible=lambda: False)
        self.screen = SimpleNamespace(geometry=lambda: QRect(0, 0, 1600, 1000),
                                      availableGeometry=lambda: QRect(0, 0, 1600, 960))
        self.screen_patch = patch('local.island_stack_visual.QGuiApplication.screenAt', return_value=self.screen)
        self.screen_patch.start()

    def tearDown(self):
        self.screen_patch.stop()
        self.island._keep_minimum_surface = False
        self.island.music_fullscreen_overlay = None
        self.island.close()
        self.island.deleteLater()
        self.app.processEvents()

    def _preview(self, center=QPointF(800, 400), dpr=1.):
        with patch('local.island_stack_visual.time.monotonic', return_value=100.):
            return self.island._preview_free_island_from_music_surface(center, dpr)

    def _commit(self, center, target=None):
        with patch('local.island_stack_visual.time.monotonic', return_value=100.), \
                patch.object(self.island, 'show') as show, \
                patch.object(self.island, 'raise_'), patch.object(self.island, 'activateWindow'):
            self.island._commit_free_island_from_music_surface(center, target)
        show.assert_called_once()

    def test_real_free_frame_has_transparent_corners_at_both_pixel_scales(self):
        for mode in ('finder', 'top', 'tasker', 'free'):
            self.island.location_mode = mode
            self.island.finder_mode = self.island.attach_to_real_finder = mode == 'finder'
            for dpr in (1., 2.):
                with self.subTest(mode=mode, dpr=dpr):
                    before = self.island.settings_snapshot()
                    target, image = self._preview(dpr=dpr)
                    self.assertEqual(target.center(), QPointF(800, 400).toPoint())
                    self.assertEqual(image.devicePixelRatioF(), dpr)
                    self.assertEqual(image.width(), round(target.width() * dpr))
                    self.assertEqual(image.height(), round(target.height() * dpr))
                    self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
                    self.assertGreater(image.pixelColor(image.width() // 2, image.height() // 2).alpha(), 200)
                    self.assertEqual(self.island.settings_snapshot(), before)
                    self.assertEqual(self.island.location_mode, mode)
        self.island.settings_changed_callback.assert_not_called()
        self.assertFalse(self.island.isVisible())

    def test_cancelled_preview_preserves_clock_springs_caches_hit_areas_and_all_fields(self):
        self.island.location_mode = 'top'
        self.island._island_lyric_source_clock = MusicLyricMotion()
        self.island._wave_line_spring = LogicalLineSpringChain()
        self.island._wave_line_spring.frame('fixture', (0., 30., 60.), 1, 99.)
        self.island._wave_reading_renderer = LyricReadingRenderer()
        self.island.music_button_rects = {'prior': QRectF(1, 2, 3, 4)}
        self.island.motions['preview-fixture'] = SpringRect(QRectF(12, 14, 160, 40))
        self.island._preview_reactive_snapshot = ReactiveSnapshot(ReactiveFrame(), {'fixture': {'glow': .25}})
        saved_fields = self.island.__dict__.copy()
        clock = self.island._island_lyric_source_clock.__dict__.copy()
        simulation_clock = self.island._island_lyric_source_clock._clock
        simulation_clock_state = simulation_clock.__dict__.copy()
        spring_positions = list(self.island._wave_line_spring.positions)
        motion_x = self.island.motions['preview-fixture'].x
        paint = self.island._paint_music

        def write_render_state(painter, rect, expanded):
            self.assertEqual(self.island.location_mode, 'free')
            self.island.motions['preview-fixture'].x += 200
            self.island._wave_line_spring.positions.append(999.)
            self.island._island_lyric_source_clock.scroll_index = 5.
            self.island._island_lyric_source_clock._clock.advance(200.)
            self.island.music_button_rects['preview'] = QRectF(40, 50, 20, 10)
            self.island._preview_reactive_snapshot.style_responses['fixture']['glow'] = .9
            self.island._preview_added_field = ['temporary']
            paint(painter, rect, expanded)

        with patch.object(self.island, '_paint_music', side_effect=write_render_state), \
                patch.object(self.island, 'move') as move, patch.object(self.island, 'resize') as resize, \
                patch.object(self.island, 'show') as show, patch.object(self.island, 'set_location_mode') as place:
            # Patch fields themselves must also survive the transaction.
            before = self.island.__dict__.copy()
            self._preview()
            self.assertEqual(self.island.__dict__.keys(), before.keys())
            self.assertTrue(all(self.island.__dict__[key] is value for key, value in before.items()))
            move.assert_not_called()
            resize.assert_not_called()
            show.assert_not_called()
            place.assert_not_called()
        self.assertEqual(self.island._island_lyric_source_clock.__dict__, clock)
        self.assertIs(self.island._island_lyric_source_clock._clock, simulation_clock)
        self.assertEqual(simulation_clock.__dict__, simulation_clock_state)
        self.assertEqual(self.island._wave_line_spring.positions, spring_positions)
        self.assertEqual(self.island.motions['preview-fixture'].x, motion_x)
        self.assertEqual(self.island.music_button_rects, {'prior': QRectF(1, 2, 3, 4)})
        self.assertEqual(self.island._preview_reactive_snapshot.style_responses, {'fixture': {'glow': .25}})
        self.assertIs(self.island.timed_lyrics, saved_fields['timed_lyrics'])
        self.island.settings_changed_callback.assert_not_called()

    def test_failed_preview_restores_current_placement_and_reading_state(self):
        self.island.location_mode = 'finder'
        self.island.finder_mode = self.island.attach_to_real_finder = True
        self.island.expanded = True
        self.island.music_button_rects = {'prior': QRectF(1, 2, 3, 4)}
        with patch.object(self.island, '_paint_music', side_effect=RuntimeError('fixture paint failure')):
            before = self.island.__dict__.copy()
            with self.assertRaisesRegex(RuntimeError, 'fixture paint failure'):
                self._preview()
            self.assertEqual(self.island.__dict__.keys(), before.keys())
            self.assertTrue(all(self.island.__dict__[key] is value for key, value in before.items()))
        self.island.settings_changed_callback.assert_not_called()

    def test_commit_centers_free_without_restarting_music_or_running_a_second_morph(self):
        self.island.location_mode = 'finder'
        self.island.finder_mode = self.island.attach_to_real_finder = True
        self.island.expanded = True
        self.island._bar_fallback_restore = {'mode': 'tasker'}
        center = QPointF(800, 400)
        target, _image = self._preview(center)
        track = (self.island.media_track_id, self.island.media_position_seconds, self.island.media_is_playing)
        restore = self.island._restore_island_from_music_surface
        with patch.object(self.island, '_restore_island_from_music_surface', wraps=restore) as restored, \
                patch.object(self.island, '_settle_standalone_drag_release') as attach:
            self._commit(center, target)
            restored.assert_called_once()
            attach.assert_not_called()
        self.assertEqual(self.island.location_mode, 'free')
        self.assertFalse(self.island.finder_mode)
        self.assertFalse(self.island.attach_to_real_finder)
        self.assertFalse(self.island.expanded)
        self.assertIsNone(self.island._bar_fallback_restore)
        self.assertFalse(self.island._music_surface_active)
        actual = self.island._current_main_target_global_rect()
        # The raster is displayed through QRectF; QRect.center() instead
        # measures inclusive integer edges and differs by half a pixel.
        painted_center = QRectF(target).center()
        self.assertLessEqual(abs(actual.center().x() - painted_center.x()), 1.)
        self.assertLessEqual(abs(actual.center().y() - painted_center.y()), 1.)
        self.assertLessEqual(abs(actual.width() - target.width()), 1.)
        self.assertLessEqual(abs(actual.height() - target.height()), 1.)
        self.assertEqual(track, (self.island.media_track_id, self.island.media_position_seconds, self.island.media_is_playing))
        self.island.music_fullscreen_overlay.hide.assert_called_once()
        self.island.settings_changed_callback.assert_called_once()
        self.assertEqual(self.island.saved_window_pos, (self.island.x(), self.island.y()))

    def test_preview_and_commit_share_screen_clamping_without_bar_attachment(self):
        for center in (QPointF(1, 1), QPointF(1598, 955)):
            with self.subTest(center=center):
                self.island._music_surface_active = True
                self.island.music_fullscreen_overlay.hide.reset_mock()
                self.island.settings_changed_callback.reset_mock()
                target, _image = self._preview(center)
                self.assertTrue(self.screen.availableGeometry().contains(target))
                self._commit(center, target)
                actual = self.island._current_main_target_global_rect()
                painted_center = QRectF(target).center()
                self.assertLessEqual(abs(actual.center().x() - painted_center.x()), 1.)
                self.assertLessEqual(abs(actual.center().y() - painted_center.y()), 1.)
                self.assertLessEqual(abs(actual.width() - target.width()), 1.)
                self.assertEqual(self.island.location_mode, 'free')

    def test_cached_preview_position_wins_over_original_center_at_both_pixel_scales(self):
        for dpr in (1., 2.):
            with self.subTest(dpr=dpr):
                self.island._music_surface_active = True
                self.island.music_fullscreen_overlay.hide.reset_mock()
                self.island.settings_changed_callback.reset_mock()
                target, _image = self._preview(QPointF(600, 350), dpr)
                track = (self.island.media_track_id, self.island.media_position_seconds,
                         self.island.media_is_playing)
                # A changed base-window center must never replace the frame
                # already drawn by the gesture, nor trigger target resolution.
                with patch.object(self.island, '_free_island_from_music_surface_target',
                                  side_effect=AssertionError('cached destination must not be recomputed')), \
                        patch.object(self.island, '_settle_standalone_drag_release') as attach:
                    self._commit(QPointF(1250, 700), target)
                    attach.assert_not_called()
                for _tick in range(3):
                    actual = self.island._current_main_target_global_rect()
                    delta = actual.center() - QRectF(target).center()
                    self.assertLessEqual(abs(delta.x()) * dpr, 1.)
                    self.assertLessEqual(abs(delta.y()) * dpr, 1.)
                self.assertEqual(self.island.location_mode, 'free')
                self.assertEqual(track, (self.island.media_track_id, self.island.media_position_seconds,
                                         self.island.media_is_playing))
                self.island.music_fullscreen_overlay.hide.assert_called_once()
                self.island.settings_changed_callback.assert_called_once()

    def test_legacy_center_only_commit_still_resolves_its_destination(self):
        resolver = self.island._free_island_from_music_surface_target
        with patch.object(self.island, '_free_island_from_music_surface_target', wraps=resolver) as resolved:
            self._commit(QPointF(800, 400))
        resolved.assert_called_once_with(QPointF(800, 400))
        self.assertEqual(self.island.location_mode, 'free')

    def test_narrow_screen_uses_the_same_standalone_minimum_as_live_layout(self):
        self.screen = SimpleNamespace(geometry=lambda: QRect(0, 0, 330, 1000),
                                      availableGeometry=lambda: QRect(0, 0, 330, 960))
        self.screen_patch.stop()
        self.screen_patch = patch('local.island_stack_visual.QGuiApplication.screenAt', return_value=self.screen)
        self.screen_patch.start()
        self.island._stack_target_width = lambda _kind: 760.
        center = QPointF(165, 400)
        target, _image = self._preview(center)
        self.assertTrue(self.screen.availableGeometry().contains(target))
        self.assertLess(target.width(), 380.)  # No default half-width floor.
        self.assertGreaterEqual(target.width(), self.island._u(120.))
        self._commit(center, target)
        actual = self.island._current_main_target_global_rect()
        self.assertLessEqual(abs(actual.width() - target.width()), 1.)
        self.assertLessEqual(abs(actual.height() - target.height()), 1.)
        self.assertLessEqual(abs(actual.center().x() - QRectF(target).center().x()), 1.)
        self.assertLessEqual(abs(actual.center().y() - QRectF(target).center().y()), 1.)
        self.assertEqual(self.island.location_mode, 'free')

    def test_stacked_screens_follow_the_painted_island_instead_of_transparent_canvas(self):
        upper = SimpleNamespace(geometry=lambda: QRect(0, 0, 1600, 400),
                                availableGeometry=lambda: QRect(0, 0, 1600, 400))
        lower = SimpleNamespace(geometry=lambda: QRect(0, 400, 800, 1000),
                                availableGeometry=lambda: QRect(0, 400, 800, 960))

        def screen_at(point):
            return next((screen for screen in (upper, lower) if screen.geometry().contains(point)), None)

        self.screen_patch.stop()
        self.screen_patch = patch('local.island_stack_visual.QGuiApplication.screenAt', side_effect=screen_at)
        self.screen_patch.start()
        self.island.location_mode = 'free'
        self.island.stack_direction = 1
        self.island._stack_target_width = lambda _kind: 600.
        self.island._restoring_position = True
        self.island.move(0, 0)
        self.island._restoring_position = False
        center = QPointF(600, 250)
        target, _image = self._preview(center)
        self.assertEqual(target.width(), 600)
        self._commit(center, target)
        # The 650-high transparent owner reaches into the lower screen, but
        # the visible Free shell remains on the upper screen throughout.
        canvas_center = self.island.mapToGlobal(self.island.rect().center())
        self.assertIs(screen_at(canvas_center), lower)
        self.assertEqual(self.island._current_screen_geometry(), upper.geometry())
        for _tick in range(3):
            actual = self.island._current_main_target_global_rect()
            self.assertTrue(upper.availableGeometry().contains(actual.toAlignedRect()))
            self.assertLessEqual(abs(actual.center().x() - QRectF(target).center().x()), 1.)
            self.assertLessEqual(abs(actual.center().y() - QRectF(target).center().y()), 1.)
            self.assertLessEqual(abs(actual.width() - target.width()), 1.)


if __name__ == '__main__':
    unittest.main()
