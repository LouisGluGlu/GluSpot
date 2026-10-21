from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
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

    def test_window_and_fullscreen_function_controls_keep_fixed_hit_and_paint_bounds(self):
        rect = QRectF(self.window.rect())
        for fullscreen in (False, True):
            self.window._fullscreen = fullscreen
            group = self.window._top_control_hover_group(rect)
            for key, base in group.items():
                with self.subTest(fullscreen=fullscreen, key=key):
                    self.window._reset_control_hover(100.0)
                    self._hover(key)
                    for now in (100.1, 100.5, 105.0):
                        self.assertEqual(self.window._hover_control_rect(key, base, group, now), base)
                    with patch("local.music_surface_window.time.monotonic", return_value=100.5):
                        self.window._sync_window_control_rects()
                        self.assertEqual(self.window._button_rects[key], base)
                        self.assertTrue(self.window._window_control_contains(key, base, base.center()))

    def test_hover_does_not_shift_neighbor_buttons(self):
        group = self.window._top_control_hover_group(QRectF(self.window.rect()))
        self._hover("playback_mode")
        for now in (100.1, 100.5, 101.5):
            self.window._update_control_animation_regions(now)
            for key, base in group.items():
                self.assertEqual(self.window._hover_control_rect(key, base, group, now), base)
        self.assertEqual(self.window._control_hover_motions, {})
        self.assertTrue(self.window.property("controlHoverDisabled"))

    def test_transport_icons_and_hit_slots_stay_stationary_on_hover(self):
        panel = QRectF(20, 350, 270, 48)
        image = QImage(960, 540, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        baseline = None
        for command in ("", "previous", "toggle_play_pause", "next"):
            with self.subTest(command=command):
                self.window._reset_control_hover(100.0)
                self._hover(command=command)
                with patch("local.music_surface_window.time.monotonic", return_value=100.5), \
                        patch.object(self.window, "_paint_transport_icon") as paint_icon:
                    with QPainter(image) as painter:
                        self.window._paint_media_controls(painter, panel, QColor("white"), QColor("gray"))
                drawn = {call.args[2]: call.args[1] for call in paint_icon.call_args_list}
                hits = dict(self.window._media_button_rects)
                if baseline is None:
                    baseline = (drawn, hits)
                self.assertEqual((drawn, hits), baseline)

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

    def test_device_hover_never_opens_panel_and_click_still_toggles(self):
        self._hover("audio_output")
        with patch.object(self.window, "_open_audio_output_panel") as open_panel:
            for now in (100.3, 100.5, 103.0):
                with patch("local.music_surface_window.time.monotonic", return_value=now):
                    self.window._update_control_animation_regions(now)
            open_panel.assert_not_called()
            self.window._toggle_audio_output_panel()
            open_panel.assert_called_once_with()
        self.window._audio_output_panel = Mock()
        self.window._audio_output_panel.isVisible.return_value = True
        self.window._toggle_audio_output_panel()
        self.window._audio_output_panel.dismiss.assert_called_once_with(immediate=False)
        self.window._audio_output_panel = None

    def test_dj_hover_stays_compact_and_click_opens_voice_choices(self):
        callback = Mock()
        self.owner._personal_dj_voice_hover_callback = callback
        self._hover("personal_dj")
        rect = QRectF(self.window.rect())
        baseline = self.window._personal_dj_geometry(rect, 100.0)
        for now in (100.3, 100.5, 103.0):
            self.window._update_control_animation_regions(now)
            self.assertEqual(self.window._personal_dj_geometry(rect, now), baseline)
        self.assertFalse(self.window._dj_voice_selector_active)
        callback.assert_not_called()
        with patch("local.music_surface_window.time.monotonic", return_value=103.0):
            self.window._activate_personal_dj(baseline[0].center(), toggle_ready=False)
        self.assertTrue(self.window._dj_voice_selector_active)
        callback.assert_called_once_with(self.window, True)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.window._control_reveal_at(120.0), 1.0)

    def test_held_menu_press_dispatches_once_on_release(self):
        for key in ("audio_output", "personal_dj"):
            with self.subTest(key=key):
                self.window._reset_control_idle(100.)
                self._hover(key)
                with patch("local.music_surface_window.time.monotonic", return_value=100.1):
                    self.window._sync_window_control_rects()
                    point = self.window._button_rects[key].center()
                    global_point = self.window.mapToGlobal(point.toPoint())
                    press = QMouseEvent(QEvent.Type.MouseButtonPress, point, QPointF(global_point),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                    self.window.mousePressEvent(press)
                self.assertEqual(self.window._pressed_window_button, key)
                with patch.object(self.window, "_toggle_audio_output_panel") as output_click, \
                        patch.object(self.window, "_activate_personal_dj") as dj_click, \
                        patch("local.music_surface_window.time.monotonic", return_value=100.7):
                    self.window._update_control_animation_regions(100.7)
                    output_click.assert_not_called()
                    dj_click.assert_not_called()
                    self.window._sync_window_control_rects()
                    point = self.window._button_rects[key].center()
                    release = QMouseEvent(QEvent.Type.MouseButtonRelease, point,
                        QPointF(self.window.mapToGlobal(point.toPoint())), Qt.MouseButton.LeftButton,
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
                    self.window.mouseReleaseEvent(release)
                    (output_click if key == "audio_output" else dj_click).assert_called_once()
                self.window._set_control_hover("", "", 100.8)
                self.window._set_control_hover(key, "", 101.)
                self.assertFalse(self.window._dj_voice_selector_active)

    def test_click_expanded_selector_has_speech_glyph_placement_without_icon_ball_or_speech_backdrop(self):
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
