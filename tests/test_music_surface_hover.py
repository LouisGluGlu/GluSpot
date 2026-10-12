from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from local.music_surface_window import MusicSurfaceWindow
from local.personal_dj_voice_popover import PersonalDjVoicePopover
from tests.test_personal_dj_voice_window import _Tasks
from tests.test_music_surface_transition import _TransitionOwner
from tests.test_owned_playback_surface import _Provider


class MusicSurfaceHoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.provider = _Provider()
        self.provider.fm = True
        self.enabled, self.speaking = True, False
        self.provider.get_owned_personal_dj_enabled = lambda: self.enabled if self.provider.fm else None
        self.provider.is_owned_personal_dj_speaking = lambda: self.speaking
        self.owner.data_provider = self.provider
        self.owner._music_library_back_callback = Mock()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window._reset_control_idle(100.0)
            self.window._sync_owned_playback_mode()
            self.window._sync_window_control_rects()

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _hover(self, key="", command="", now=100.0):
        self.window._hovered_window_button = key
        self.window._hovered_media_command = command
        self.window._set_control_hover(key, command, now)
        self.window._control_hover_menu_timer.stop()

    def _advance(self, start=100.0, end=100.5):
        steps = max(1, round((end - start) / .01))
        for index in range(1, steps + 1):
            self.window._advance_control_neighbor_motion(start + (end - start) * index / steps)

    def test_every_top_control_grows_in_half_second_and_hit_matches_paint(self):
        rect = QRectF(self.window.rect())
        for fullscreen in (False, True):
            self.window._fullscreen = fullscreen
            group = self.window._top_control_hover_group(rect)
            for key, base in group.items():
                with self.subTest(fullscreen=fullscreen, key=key):
                    self.window._reset_control_hover(100.0)
                    self._hover(key)
                    midway = self.window._hover_control_rect(key, base, group, 100.1)
                    end = self.window._hover_control_rect(key, base, group, 100.5)
                    self.assertGreater(midway.width(), base.width())
                    self.assertAlmostEqual(end.width(), base.width() * 1.10)
                    early_growth = midway.width() - base.width()
                    late_growth = end.width() - self.window._hover_control_rect(key, base, group, 100.4).width()
                    self.assertGreater(early_growth, late_growth)
                    with patch("local.music_surface_window.time.monotonic", return_value=100.5):
                        self.window._sync_window_control_rects()
                        self.assertEqual(self.window._button_rects[key], end)
                        newly_visible = QPointF(end.right() - .01, end.center().y())
                        self.assertFalse(base.contains(newly_visible))
                        self.assertTrue(self.window._window_control_contains(key, end, newly_visible))

    def test_neighbor_spring_moves_away_with_distance_decay_and_settles_back(self):
        self._hover("playback_mode")
        group = self.window._top_control_hover_group(QRectF(self.window.rect()))
        self._advance()
        near = self.window._hover_control_rect("translation", group["translation"], group, 100.5)
        far = self.window._hover_control_rect("return", group["return"], group, 100.5)
        self.assertGreater(near.center().x(), group["translation"].center().x())
        self.assertGreater(far.center().x(), group["return"].center().x())
        self.assertGreater(near.center().x() - group["translation"].center().x(),
                           far.center().x() - group["return"].center().x())
        self.assertLessEqual(near.center().x() - group["translation"].center().x(), 12.5)
        self._hover(now=100.5)
        continuous = self.window._hover_control_rect("translation", group["translation"], group, 100.5)
        self.assertEqual(continuous, near)
        self._advance(100.5, 101.5)
        restored = self.window._hover_control_rect("translation", group["translation"], group, 101.5)
        self.assertAlmostEqual(restored.center().x(), group["translation"].center().x(), delta=.01)

    def test_transport_icons_and_click_slots_share_the_enlarged_pose(self):
        panel = QRectF(20, 350, 270, 48)
        image = QImage(960, 540, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        for command in ("previous", "toggle_play_pause", "next"):
            with self.subTest(command=command):
                self.window._reset_control_hover(100.0)
                self._hover(command=command)
                with patch("local.music_surface_window.time.monotonic", return_value=100.5), \
                        patch.object(self.window, "_paint_transport_icon") as paint_icon:
                    with QPainter(image) as painter:
                        self.window._paint_media_controls(painter, panel, QColor("white"), QColor("gray"))
                drawn = {call.args[2]: call.args[1] for call in paint_icon.call_args_list}
                hit = self.window._media_button_rects[command]
                self.assertEqual(drawn[command].center(), hit.center())
                self.assertAlmostEqual(hit.height(), min(panel.height(), self.window.TRANSPORT_PANEL_HEIGHT * self.window._surface_scale()) * 1.10)
                baseline = next(value for key, value in drawn.items() if key != command)
                self.assertAlmostEqual(drawn[command].width() / baseline.width(), 1.10)

    def test_library_circle_and_expanded_label_use_the_enlarged_hit_shape(self):
        self._hover("library")
        for expanded in (0.0, 1.0):
            self.window._library_expansion_from = self.window._library_expansion_target = expanded
            with patch("local.music_surface_window.time.monotonic", return_value=100.5):
                self.window._sync_window_control_rects()
                base = self.window._library_button_face(QRectF(self.window.rect()))
                shown = self.window._button_rects["library"]
                self.assertAlmostEqual(shown.width(), base.width() * 1.10)
                self.assertTrue(self.window._window_control_contains("library", shown, shown.center()))
                self.assertFalse(self.window._window_control_contains("library", shown, shown.topLeft()))

    def test_device_hover_requires_continuous_half_second_and_click_still_toggles(self):
        self._hover("audio_output")
        with patch.object(self.window, "isVisible", return_value=True), \
                patch.object(self.window, "_open_audio_output_panel") as open_panel:
            with patch("local.music_surface_window.time.monotonic", return_value=100.3):
                self.window._check_control_hover_menu()
            open_panel.assert_not_called()
            self.window._control_hover_menu_timer.stop()
            with patch("local.music_surface_window.time.monotonic", return_value=100.5):
                self.window._sync_window_control_rects()
                position = self.window.mapToGlobal(self.window._button_rects["audio_output"].center().toPoint())
                with patch("local.music_surface_window.QCursor.pos", return_value=position):
                    self.window._check_control_hover_menu()
                    self.window._check_control_hover_menu()
            open_panel.assert_called_once_with()
        self._hover("audio_output", now=101.0)
        self._hover("", now=101.2)
        self.assertFalse(self.window._control_hover_menu_timer.isActive())
        self.assertEqual(self.window._control_hover_menu_key, "")
        self.window._audio_output_panel = Mock()
        self.window._audio_output_panel.isVisible.return_value = True
        self.window._toggle_audio_output_panel()
        self.window._audio_output_panel.dismiss.assert_called_once_with(immediate=False)
        self.window._audio_output_panel = None

    def test_dj_half_second_hover_opens_caption_without_toggling_music(self):
        callback = Mock()
        self.owner._personal_dj_voice_hover_callback = callback
        self._hover("personal_dj")
        with patch.object(self.window, "isVisible", return_value=True), \
                patch("local.music_surface_window.time.monotonic", return_value=100.5):
            self.window._sync_window_control_rects()
            position = self.window.mapToGlobal(self.window._button_rects["personal_dj"].center().toPoint())
            # The compact icon is elliptical; its actual center owns hover.
            with patch("local.music_surface_window.QCursor.pos", return_value=position):
                self.window._check_control_hover_menu()
            self.assertTrue(self.window._dj_voice_selector_active)
            callback.assert_called_once_with(self.window, True)
            self.assertEqual(self.provider.calls, [])
        icon, caption = self.window._personal_dj_geometry(QRectF(self.window.rect()), 101.0)
        self.assertGreater(caption.width(), caption.height() * 4)
        self.assertGreater(icon.height(), 44 * self.window._surface_scale())
        self.assertEqual(self.window._control_reveal_at(120.0), 1.0)

    def test_expanded_hover_has_speech_glyph_placement_without_icon_ball_or_speech_backdrop(self):
        self.owner._personal_dj_voice_hover_callback = Mock()
        self.window._open_dj_voice_selector(100.0)
        rect = QRectF(self.window.rect())
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch("local.music_surface_window.time.monotonic", return_value=100.6), \
                patch.object(self.window, "_paint_surface_glass") as glass, \
                patch.object(self.window, "_paint_personal_dj_backdrop") as backdrop:
            icon, caption = self.window._personal_dj_geometry(rect, 100.6)
            with QPainter(image) as painter:
                self.window._paint_personal_dj_control(painter, rect)
            self.assertEqual(glass.call_count, 1)
            self.assertEqual(glass.call_args.args[1], caption)
            self.assertNotEqual(glass.call_args.args[1], icon)
            self.assertEqual(backdrop.call_args.args[2], 0.0)

    def test_selector_leave_keeps_gap_then_closes_outside(self):
        popup = QWidget(self.window)
        self.owner._personal_dj_voice_hover_callback = Mock(return_value=popup)
        self.window._owned_personal_dj_enabled = True
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window._open_dj_voice_selector(100.0)
        self._hover(now=101.0)
        self.window._set_dj_voice_selector_hover(False)
        self.assertTrue(self.window._dj_voice_selector_close_timer.isActive())
        with patch.object(self.window, "_dj_voice_selector_contains_pointer", return_value=True):
            self.window._check_dj_voice_selector_leave()
        self.assertTrue(self.window._dj_voice_selector_active)
        self.window._dj_voice_selector_close_timer.stop()
        # After dwelling in the icon/caption corridor, another blank-hover
        # move outside still re-arms close even though no hover key changes.
        self.window._set_control_hover("", "", 101.4)
        self.assertTrue(self.window._dj_voice_selector_close_timer.isActive())
        with patch.object(self.window, "_dj_voice_selector_contains_pointer", return_value=False):
            self.window._check_dj_voice_selector_leave()
        self.assertFalse(self.window._dj_voice_selector_active)
        self.owner._personal_dj_voice_hover_callback.assert_called_with(self.window, False)

    def test_focused_real_popup_closes_after_two_second_leave_without_restarting_deadline(self):
        popup = PersonalDjVoicePopover(self.provider, self.window, tasks=_Tasks())
        popup.setGeometry(10, 10, 80, 30)
        self.owner._personal_dj_voice_hover_callback = Mock(return_value=popup)
        self.window.show()
        popup.show()
        self.window.activateWindow()
        popup.setFocus(Qt.FocusReason.OtherFocusReason)
        self.app.processEvents()
        self.assertTrue(popup.hasFocus())
        self.window._open_dj_voice_selector(100.0)
        self._hover("personal_dj", now=101.0)
        timer = self.window._dj_voice_selector_close_timer
        self.assertEqual(timer.interval(), 2000)
        timer.stop()
        with patch.object(timer, "start", wraps=timer.start) as start:
            popup.leaveEvent(QEvent(QEvent.Type.Leave))
            popup.leaveEvent(QEvent(QEvent.Type.Leave))
            # A child popup can leave a stale parent icon hover key; an
            # animation tick must not stop its real pointer-leave deadline.
            self.window._update_control_animation_regions(101.1)
            self.assertTrue(timer.isActive())
            self.window._set_control_hover("", "", 101.2)
            self.window._set_control_hover("", "", 101.4)
            start.assert_called_once_with()
        outside = self.window.mapToGlobal(QPoint(-100, -100))
        with patch("local.music_surface_window.QCursor.pos", return_value=outside):
            self.assertFalse(self.window._dj_voice_selector_contains_pointer())
            self.window._check_dj_voice_selector_leave()
        self.assertFalse(self.window._dj_voice_selector_active)
        self.assertFalse(timer.isActive())
        self.owner._personal_dj_voice_hover_callback.assert_called_with(self.window, False)

    def test_non_fm_source_and_immediate_geometry_dismiss_close_selector(self):
        callback = Mock()
        self.owner._personal_dj_voice_hover_callback = callback
        for cause in ("source", "geometry"):
            with self.subTest(cause=cause):
                self.provider.fm = True
                self.window._owned_personal_fm = True
                self.window._owned_personal_dj_enabled = True
                self.window._open_dj_voice_selector(100.0)
                if cause == "source":
                    self.provider.fm = False
                    self.window._sync_owned_playback_mode()
                else:
                    self.window._dismiss_audio_output_panel(immediate=True)
                self.assertFalse(self.window._dj_voice_selector_active)
                self.assertFalse(self.window._dj_voice_selector_close_timer.isActive())
                self.assertEqual(self.window._dj_voice_selector_motion.value(101), 0.0)
                callback.assert_called_with(self.window, False)

    def test_dj_underlying_music_has_paused_cover_and_actual_play_pause_intent(self):
        self.speaking = True
        self.owner.media_is_playing = True
        self.owner._send_playback_command_async = Mock()
        self.assertTrue(self.window._display_playing_state(100.0))
        self.assertFalse(self.window._cover_visual_playing_state(100.0))
        self.window._cover_scale_from = self.window._cover_scale_to = 1.0
        self.window._cover_scale_at(100.0)
        self.assertAlmostEqual(self.window._cover_scale_at(100.4), self.window.COVER_PAUSE_SCALE)
        with patch("local.music_surface_window.time.monotonic", return_value=100.4):
            self.window._send_media_command("toggle_play_pause")
        self.assertFalse(self.window._optimistic_playing)
        self.owner._send_playback_command_async.assert_called_once_with("toggle_play_pause")
        self.speaking = False
        self.window._optimistic_playing = None
        self.window._cover_scale_at(101.0)
        self.assertAlmostEqual(self.window._cover_scale_at(101.4), 1.0)

    def test_cover_paused_snapshot_survives_speech_part_gap_and_excludes_future_prefetch(self):
        self.owner.media_is_playing = True
        self.speaking = False
        self.provider.get_personal_dj_presentation = lambda: {"cover_paused": True}
        self.provider.is_owned_personal_dj_speaking = Mock(side_effect=RuntimeError("optional speech state unavailable"))
        self.assertFalse(self.window._cover_visual_playing_state(100.0))
        self.speaking = True
        self.provider.get_personal_dj_presentation = lambda: {"cover_paused": False}
        self.assertTrue(self.window._cover_visual_playing_state(100.0))


if __name__ == "__main__":
    unittest.main()
