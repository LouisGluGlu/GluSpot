"""Exercise the real QWidget handoff, including its first queued event/tick.

Geometry-only handoff fixtures cannot detect a hidden, minimized or transparent
destination. These fixtures keep production show/hide/showEvent/paintEvent paths
and use an inert provider; no installed app, account or audio output is touched.
"""
from __future__ import annotations

from dataclasses import replace
import sys
import traceback
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QEventLoop, QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QRegion
from PySide6.QtWidgets import QApplication

from api.models import TimedLyricLine
from api.providers import EmptyIslandDataProvider, empty_visual_data
from local.island_stack_visual import IslandStackVisual
from local.music_surface_window import MusicSurfaceWindow


class MusicCompactVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # Keep the 100 ms external-scene cache fresh through the real Qt
        # animation. QVariantAnimation itself uses Qt's independent clock.
        self.clock = patch('local.island_stack_visual.time.monotonic', return_value=100.)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.callback_errors = []
        self.exception_hook = patch.object(sys, 'excepthook', side_effect=lambda kind, value, tb:
            self.callback_errors.append(''.join(traceback.format_exception(kind, value, tb))))
        self.exception_hook.start()
        self.addCleanup(self.exception_hook.stop)

        empty = empty_visual_data()
        track = replace(empty.track, title='The song keeps playing', artist='Offline fixture',
                        lyrics=('Before', 'Music continues\n译文继续', 'After'),
                        timed_lyrics=(TimedLyricLine(0., 'Before'),
                                      TimedLyricLine(4., 'Music continues\n译文继续'),
                                      TimedLyricLine(30., 'After')),
                        is_playing=True, duration_seconds=60., position_seconds=5.,
                        position_sampled_at=100., position_observed_seconds=5.,
                        position_source='local-playback', track_id='owned:fixture:compact-visibility')
        self.provider = EmptyIslandDataProvider(replace(empty, track=track))
        self.provider.send_playback_command = Mock(wraps=self.provider.send_playback_command)
        self.provider.seek_playback_position = Mock(wraps=self.provider.seek_playback_position)
        self.island = IslandStackVisual(self.provider)
        self.island.timer.stop()
        self.island.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.island._force_super_topmost = lambda: None
        self.island._release_super_topmost = lambda: None
        self.island._mydockfinder_available = lambda: False
        self.island._finder_bar_available = lambda: False
        self.island._effective_lyric_offset_seconds = lambda: 0.
        self.external_probe = Mock(return_value=(False, ''))
        self.island._detect_external_scene_suppression = self.external_probe
        # A stale fullscreen result must not invoke real taskbar attachment.
        self.island._owner_bar_hidden = Mock(return_value=True)
        self.island._refresh_taskbar_follow_position = Mock(return_value=False)
        self.island._refresh_taskbar_above_follow_position = Mock(return_value=False)
        self.island.settings_changed_callback = Mock()
        self.island.music_auto_detect = False
        self.island.music_ambience_style = 'pure_black'
        self.island.enable_dual_line_lyrics = True
        self.island.location_mode = 'top'
        self.island.finder_mode = self.island.attach_to_real_finder = False
        self.island.album_art = QImage(48, 48, QImage.Format.Format_RGB32)
        self.island.album_art.fill(QColor('#a86f4d'))

        self.page = MusicSurfaceWindow(self.island)
        self.page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.island.music_fullscreen_overlay = self.page
        available = self.page._available_geometry()
        self.minimum_geometry = QRect(0, 0, 330, 400)
        self.minimum_geometry.moveCenter(available.center())
        self.page._normal_geometry = QRect(self.minimum_geometry)
        self.page._timer.stop()
        self.page._audio_output_status_timer.stop()
        self.track_before = self._audio_state()

    def tearDown(self):
        self.page.shutdown()
        self.page.deleteLater()
        self.island.music_fullscreen_overlay = None
        self.island._keep_minimum_surface = False
        self.island.close()
        self.island.deleteLater()
        self.app.processEvents()
        self.assertEqual(self.callback_errors, [])

    def _audio_state(self):
        return (self.island.media_track_id, self.island.media_position_seconds,
                self.island.media_is_playing)

    def _open_page(self):
        self.page._normal_geometry = QRect(self.minimum_geometry)
        self.island._show_music_fullscreen(fullscreen=False, origin_geometry=QRect(),
                                           origin_snapshot=QImage())
        self.app.processEvents()
        self.island.timer.stop()
        self.page._timer.stop()
        self.page._audio_output_status_timer.stop()
        self.assertTrue(self.page.isVisible())
        self.assertFalse(self.island.isVisible())
        self.assertEqual(self.page.geometry().size(), self.minimum_geometry.size())

    def _start_squeeze(self):
        self.page._resize_edges = {'right', 'bottom'}
        self.page._update_window_compact(165., 200.)
        self.assertTrue(self.page._compact_committing)
        self.assertTrue(self.page._compact_target.isValid())
        return QRect(self.page._compact_target)

    def _wait_for_completion(self):
        # Run actual animation.finished and QWidget hide/show delivery, rather
        # than manually calling a mocked owner's completion method.
        loop = QEventLoop()
        QTimer.singleShot(300, loop.quit)
        loop.exec()
        self.app.processEvents()
        self.island.timer.stop()
        self.assertFalse(self.page._compact_committing)

    def _assert_visible_destination(self, expected):
        self.assertTrue(self.island.isVisible())
        self.assertFalse(self.island.isMinimized())
        self.assertFalse(self.page.isVisible())
        self.assertFalse(self.island._music_surface_active)
        self.assertFalse(self.island._navigation_surface_blocks_island())
        self.assertEqual(self.island.location_mode, 'free')
        self.assertFalse(self.island.scene_suppressed)
        self.assertFalse(self.island.input_passthrough_enabled)
        self.assertFalse(self.island.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertTrue(self.island.mask().isEmpty())
        self.assertGreater(self.island.windowOpacity(), .99)

        dpr = self.island.devicePixelRatioF()
        actual = self.island._current_main_global_rect()
        delta = actual.center() - QRectF(expected).center()
        self.assertLessEqual(abs(delta.x()) * dpr, 1.01)
        self.assertLessEqual(abs(delta.y()) * dpr, 1.01)
        origin = self.island.mapToGlobal(QPoint())
        local = actual.translated(-origin.x(), -origin.y())
        self.assertTrue(QRectF(self.island.rect()).contains(local))
        image = QImage(round(self.island.width() * dpr), round(self.island.height() * dpr),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        self.island.render(image)
        self.assertGreater(image.pixelColor(round(local.center().x() * dpr),
                                           round(local.center().y() * dpr)).alpha(), 200)
        self.assertEqual(self._audio_state(), self.track_before)
        self.provider.send_playback_command.assert_not_called()
        self.provider.seek_playback_position.assert_not_called()

    def _advance_first_tick(self):
        self.island._tick()
        self.app.processEvents()
        self.island.timer.stop()

    def test_real_completion_and_first_tick_keep_the_preview_visible(self):
        self._open_page()
        expected = self._start_squeeze()
        self._wait_for_completion()
        self._assert_visible_destination(expected)
        self._advance_first_tick()
        self._assert_visible_destination(expected)

    def test_inherited_fullscreen_cache_cannot_override_explicit_free_completion(self):
        self._open_page()
        expected = self._start_squeeze()
        # This is inherited before completion, not a newly detected external
        # fullscreen scene after the user's explicit placement has finished.
        self.island._external_scene_result = (True, 'fullscreen')
        self.island._external_scene_checked_at = 100.
        self.island._external_fullscreen_active = True
        self.island._fullscreen_placement_active = True
        self.island.scene_checked_at = 100.
        self.external_probe.reset_mock()
        self._wait_for_completion()
        self._advance_first_tick()
        self.external_probe.assert_called()
        self._assert_visible_destination(expected)

    def test_inherited_bar_fade_and_mouse_mask_do_not_erase_the_free_island(self):
        self._open_page()
        expected = self._start_squeeze()
        self.island.scene_suppressed = True
        self.island.scene_reason = 'taskbar-hidden'
        self.island.scene_transition_mode = 'hide'
        self.island._bar_scene_transition = True
        self.island._bar_visibility_value = self.island._bar_visibility_target = 0.
        self.island._bar_visibility_velocity = -5.
        self.island._bar_visibility_at = 100.
        self.island._set_input_passthrough(True)
        self.island.setMask(QRegion(QRect(1, 1, 2, 2)))
        self.island.interaction_mask_active = True
        self.island.last_mask_rect = QRectF(1, 1, 2, 2)
        self._wait_for_completion()
        self.assertEqual(self.island._scene_transition_params(100.), (1., 1., False, 0.))
        self._assert_visible_destination(expected)
        self._advance_first_tick()
        self._assert_visible_destination(expected)

    def test_previously_minimized_owner_is_restored_as_a_visible_normal_island(self):
        self.island.showMinimized()
        self.app.processEvents()
        self.island.timer.stop()
        self.assertTrue(self.island.isMinimized())
        self._open_page()
        self.assertTrue(self.island.isMinimized())
        expected = self._start_squeeze()
        self._wait_for_completion()
        self._assert_visible_destination(expected)
        self._advance_first_tick()
        self._assert_visible_destination(expected)

    def test_three_real_open_squeeze_cycles_keep_audio_and_visible_placement(self):
        for cycle in range(3):
            with self.subTest(cycle=cycle):
                self._open_page()
                expected = self._start_squeeze()
                self._wait_for_completion()
                self._assert_visible_destination(expected)
                self._advance_first_tick()
                self._assert_visible_destination(expected)

    def test_settings_failure_occurs_after_visibility_and_releases_preview(self):
        self._open_page()
        expected = self._start_squeeze()
        self.page._compact_animation.stop()
        self.page._advance_window_compact(1.)
        self.island.settings_changed_callback = Mock(side_effect=RuntimeError('fixture save failure'))
        with self.assertRaisesRegex(RuntimeError, 'fixture save failure'):
            self.page._finish_window_compact()
        self.app.processEvents()
        self.island.timer.stop()
        self.assertTrue(self.page._compact_snapshot.isNull())
        self.assertFalse(self.page._compact_committing)
        self._assert_visible_destination(expected)


if __name__ == '__main__':
    unittest.main()
