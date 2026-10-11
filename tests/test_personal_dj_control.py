from __future__ import annotations

from types import SimpleNamespace
import math
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat
from PySide6.QtWidgets import QApplication, QWidget

from api.models import AudioFrame
from local.owned_audio_bridge import OwnedAudioBridge
from local.personal_dj_icon import (DJ_RING_CYCLE_SECONDS, DJ_RING_HOLD_SECONDS, DJ_RING_TRAVEL_SECONDS,
                                   paint_personal_dj_icon, personal_dj_ring_poses, personal_dj_status_color,
                                   personal_dj_status_opacity, personal_dj_travel_progress)
from local.music_surface_window import MusicSurfaceWindow
from tests.test_owned_audio_bridge_handoff import Owner
from tests.test_owned_playback_surface import _Provider


class PersonalDjPcmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = Owner()
        self.position = 5
        self.speaking = True
        self.owner.player.position = lambda: self.position
        self.owner.is_personal_dj_speaking = lambda: self.speaking
        self.bridge = OwnedAudioBridge(self.owner)

    def tearDown(self):
        self.bridge.close()

    def feed(self, hz, start=0, count=4096):
        wave = .7 * np.sin(2 * np.pi * hz * np.arange(count) / 44100) if hz else np.zeros(count)
        samples = np.repeat((wave * 32767).astype("<i2")[:, None], 2, axis=1)
        format_ = QAudioFormat()
        format_.setSampleRate(44100)
        format_.setChannelCount(2)
        format_.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self.bridge._buffer(QAudioBuffer(samples.tobytes(), format_, start))

    def test_real_frequencies_select_corresponding_bands_without_direct_cast(self):
        self.assertFalse(self.bridge._direct)
        for hz, dominant in ((90, 0), (1000, 1), (5000, 2)):
            self.bridge._dj_smooth[:] = [0.0, 0.0, 0.0]
            self.feed(hz)
            frame = self.bridge.get_personal_dj_audio_frame()
            values = (frame.low, frame.mid, frame.high)
            self.assertEqual(values.index(max(values)), dominant)
            self.assertGreater(values[dominant], .02)

    def test_decoder_ahead_does_not_drive_current_presentation(self):
        self.feed(1000, start=1000000)
        self.assertIsNone(self.bridge.get_personal_dj_audio_frame())
        self.position = 1005
        self.assertIsNotNone(self.bridge.get_personal_dj_audio_frame())
        self.position = 2000
        self.assertIsNone(self.bridge.get_personal_dj_audio_frame())

    def test_short_sound_after_first_1024_samples_is_not_dropped(self):
        wave = np.zeros(2205)
        wave[1024:] = .7 * np.sin(2 * np.pi * 5000 * np.arange(1181) / 44100)
        samples = np.repeat((wave * 32767).astype("<i2")[:, None], 2, axis=1)
        format_ = QAudioFormat()
        format_.setSampleRate(44100)
        format_.setChannelCount(2)
        format_.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self.bridge._buffer(QAudioBuffer(samples.tobytes(), format_, 0))
        frame = self.bridge.get_personal_dj_audio_frame()
        self.assertGreater(frame.high, .02)
        self.assertGreater(frame.high, max(frame.low, frame.mid))

    def test_same_voice_short_seek_clears_old_frequency_history(self):
        self.position = 1005
        with patch("local.owned_audio_bridge.time.monotonic", return_value=10):
            self.bridge._position_changed(self.position)
            self.feed(1000, start=1000000)
            self.assertIsNotNone(self.bridge.get_personal_dj_audio_frame())
            epoch = self.owner._epoch
            self.position = 805
            self.bridge._position_changed(self.position)
        self.assertEqual(self.owner._epoch, epoch)
        self.assertFalse(self.bridge._dj_frames)
        self.assertEqual(self.bridge._dj_smooth, [0.0, 0.0, 0.0])
        self.assertIsNone(self.bridge.get_personal_dj_audio_frame())
        self.feed(5000, start=800000)
        self.assertGreater(self.bridge.get_personal_dj_audio_frame().high, .02)

    def test_silence_pause_track_change_and_close_have_no_synthetic_signal(self):
        self.feed(0)
        self.assertEqual(self.bridge.get_personal_dj_audio_frame(), AudioFrame(0, 0, 0))
        self.feed(1000)
        self.speaking = False
        self.assertIsNone(self.bridge.get_personal_dj_audio_frame())
        self.speaking = True
        self.owner._epoch += 1
        self.owner.changed.emit()
        self.assertIsNone(self.bridge.get_personal_dj_audio_frame())
        self.assertFalse(self.bridge._dj_frames)
        self.bridge.close()
        self.assertIsNone(self.bridge.get_personal_dj_audio_frame())

    def test_timeline_storage_is_bounded_and_does_not_retain_audio(self):
        for i in range(100):
            self.feed(1000, start=i * 100000)
        self.assertEqual(len(self.bridge._dj_frames), 96)
        self.assertTrue(all(isinstance(frame, AudioFrame) for _, _, frame in self.bridge._dj_frames))


class PersonalDjControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.provider = _Provider()
        self.provider.fm = True
        self.enabled, self.speaking = False, False
        self.presentation = {}
        self.provider.get_owned_personal_dj_enabled = lambda: self.enabled if self.provider.fm and self.provider.owned else None
        self.provider.is_owned_personal_dj_speaking = lambda: self.speaking
        self.provider.get_owned_personal_dj_audio_frame = lambda: AudioFrame(.2, .1, .05)
        self.provider.set_owned_personal_dj_enabled = self.set_enabled
        self.provider.get_personal_dj_presentation = lambda: self.presentation
        self.owner.data_provider = self.provider
        self.owner.album_color, self.owner.secondary_color = QColor("#9a426b"), QColor("#38233a")
        self.owner.album_art = QImage()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def set_enabled(self, value):
        self.enabled = value
        return True

    def test_control_is_fm_only_and_toggle_reuses_provider(self):
        self.window._sync_owned_playback_mode()
        self.assertFalse(self.window._owned_personal_dj_enabled)
        self.assertTrue(self.window._toggle_personal_dj())
        self.assertTrue(self.enabled)
        self.assertTrue(self.window._toggle_personal_dj())
        self.assertFalse(self.enabled)
        self.provider.fm = False
        self.window._sync_owned_playback_mode()
        self.assertIsNone(self.window._owned_personal_dj_enabled)
        self.assertFalse(self.window._toggle_personal_dj())

    def test_voice_pops_only_dj_control_then_follows_shared_idle(self):
        self.enabled = True
        self.window._reset_control_idle(100)
        with patch("local.music_surface_window.time.monotonic", return_value=110):
            self.window._sync_owned_playback_mode()
            self.assertEqual(self.window._personal_dj_reveal_at(110), 0)
            self.speaking = True
            self.window._sync_personal_dj_state()
        with patch("local.music_surface_window.time.monotonic", return_value=110.4):
            self.window._sync_window_control_rects()
            self.assertEqual(set(self.window._button_rects), {"personal_dj"})
            self.assertTrue(self.window._window_button_clickable("personal_dj", False))
            self.assertEqual(self.window._control_reveal_at(110.4), 0)
            self.speaking = False
            self.window._sync_personal_dj_state()
        with patch("local.music_surface_window.time.monotonic", return_value=110.8):
            self.window._sync_window_control_rects()
            self.assertFalse(self.window._button_rects)
        self.window._reset_control_idle(111)
        self.assertEqual(self.window._personal_dj_reveal_at(111), 1)

    def test_button_is_bottom_right_with_own_round_hit_shape(self):
        for size in ((640, 360), (960, 540), (1920, 1080)):
            self.window.resize(*size)
            region = QRectF(self.window.rect())
            dj = self.window._personal_dj_button_rect(region)
            fm = self.window._playback_mode_button_rect(self.window._window_control_base_rect(region), self.window._surface_scale(region))
            self.assertGreater(dj.top(), region.bottom() * .8)
            self.assertGreater(dj.center().x(), fm.center().x())
            self.assertTrue(region.contains(dj))
            self.assertTrue(self.window._window_control_contains("personal_dj", dj, dj.center()))
            self.assertFalse(self.window._window_control_contains("personal_dj", dj, dj.topLeft()))

    def test_speech_expands_twice_with_actual_next_title_below_not_over_cover(self):
        self.enabled = True
        self.presentation = {"state": "speaking", "next_title": "真实下一首歌曲的名称", "speaking": True, "token": 4}
        for size in ((640, 360), (960, 540), (1920, 1080)):
            with self.subTest(size=size):
                self.window.resize(*size)
                self.speaking = False
                self.window._dj_activity_from = self.window._dj_activity_to = 0.0
                region = QRectF(self.window.rect())
                compact, _ = self.window._personal_dj_geometry(region, 100, expansion=0)
                self.speaking = True
                with patch("local.music_surface_window.time.monotonic", return_value=100):
                    self.window._sync_owned_playback_mode()
                    self.window._dj_activity_from = 0.0
                    self.window._dj_activity_to = 1.0
                    self.window._dj_activity_started_at = 100.0
                icon, title = self.window._personal_dj_geometry(region, 100.4)
                self.assertAlmostEqual(icon.width(), compact.width() * 2)
                self.assertGreater(title.top(), icon.bottom())
                self.assertGreater(title.width(), icon.width())
                self.assertTrue(region.contains(icon.united(title)))
                self.assertGreater(icon.left(), region.width() * .65)
                self.assertGreater(icon.top(), region.height() * .60)
                self.assertEqual(self.window._dj_next_title, self.presentation["next_title"])

    def test_speech_union_hit_shape_rejects_empty_space_and_keeps_title_clickable(self):
        self.enabled = self.speaking = True
        self.presentation = {"state": "speaking", "next_title": "Next actual music", "token": 3}
        with patch("local.music_surface_window.time.monotonic", return_value=100):
            self.window._sync_owned_playback_mode()
        with patch("local.music_surface_window.time.monotonic", return_value=100.4):
            icon, caption = self.window._personal_dj_geometry(QRectF(self.window.rect()), 100.4)
            whole = self.window._personal_dj_button_rect(QRectF(self.window.rect()))
            self.assertTrue(self.window._window_control_contains("personal_dj", whole, icon.center()))
            self.assertTrue(self.window._window_control_contains("personal_dj", whole, caption.center()))
            self.assertFalse(self.window._window_control_contains("personal_dj", whole, whole.topLeft()))
            self.assertFalse(self.window._window_control_contains("personal_dj", whole, QPointF(icon.center().x(), (icon.bottom() + caption.top()) / 2)))

    def test_snapshot_hit_shape_stays_captured_when_speech_ends_and_morph_stretches(self):
        self.enabled = self.speaking = True
        self.presentation = {"state": "speaking", "next_title": "Next music", "token": 6}
        with patch("local.music_surface_window.time.monotonic", return_value=100):
            self.window._sync_owned_playback_mode()
        with patch("local.music_surface_window.time.monotonic", return_value=100.4):
            self.window._sync_window_control_rects()
            source_size = QSize(self.window.size())
            icon, caption = self.window._personal_dj_geometry(QRectF(self.window.rect()), 100.4)
            self.window._remember_transition_controls(source_size, False)
        self.window._geometry_transition_snapshot = QImage(source_size, QImage.Format.Format_RGB32)
        self.speaking = False
        self.presentation = {"state": "idle", "next_title": "", "token": 7}
        with patch("local.music_surface_window.time.monotonic", return_value=101):
            self.window._sync_personal_dj_state()
            self.window.resize(1440, 720)
            transformed = self.window._transition_control_rects()["personal_dj"]
            def transformed_point(point):
                return QPointF(point.x() * 1440 / 960, point.y() * 720 / 540)
            self.assertTrue(self.window._transition_window_control_contains("personal_dj", transformed, transformed_point(icon.center())))
            self.assertTrue(self.window._transition_window_control_contains("personal_dj", transformed, transformed_point(caption.center())))
            self.assertFalse(self.window._transition_window_control_contains("personal_dj", transformed, transformed.topLeft()))

    def test_provider_states_use_verified_colors_without_revealing_idle_controls(self):
        self.enabled = True
        self.window._reset_control_idle(100)
        for state, color in (("loading", "#248eea"), ("ready", "#16c777"), ("failed", "#ec373f")):
            with self.subTest(state=state), patch("local.music_surface_window.time.monotonic", return_value=110):
                self.presentation = {"state": state, "next_title": "actual song", "speaking": False, "token": 2}
                self.window._sync_owned_playback_mode()
                self.window._sync_window_control_rects()
                self.assertEqual(self.window._dj_presentation_state, state)
                self.assertEqual(personal_dj_status_color(state).name(), color)
                self.assertEqual(self.window._personal_dj_reveal_at(110), 0)
                self.assertNotIn("personal_dj", self.window._button_rects)
        self.assertNotEqual(personal_dj_status_opacity("loading", 0), personal_dj_status_opacity("loading", .85))
        self.assertNotEqual(personal_dj_status_opacity("ready", 0), personal_dj_status_opacity("ready", .85))
        self.assertEqual(personal_dj_status_opacity("loading", 0), 0)
        self.assertEqual(personal_dj_status_opacity("ready", 1.7), 0)
        self.assertEqual(personal_dj_status_opacity("failed", .85), 1)

    def test_failed_state_latches_visual_only_then_returns_without_changing_provider(self):
        self.enabled = True
        self.presentation = {"state": "failed", "token": 2}
        with patch("local.music_surface_window.time.monotonic", return_value=100):
            self.window._sync_owned_playback_mode()
        self.presentation = {"state": "idle", "token": 3}
        with patch("local.music_surface_window.time.monotonic", return_value=100.5):
            self.window._sync_personal_dj_state()
            self.assertEqual(self.window._dj_presentation_state, "failed")
        with patch("local.music_surface_window.time.monotonic", return_value=101.3):
            self.window._sync_personal_dj_state()
            self.assertEqual(self.window._dj_presentation_state, "idle")
        self.assertTrue(self.enabled)
        self.assertEqual(self.presentation, {"state": "idle", "token": 3})

    def test_speech_end_preserves_real_caption_geometry_until_contraction_finishes(self):
        self.enabled = self.speaking = True
        title = "A long but real upcoming song title retained through closing"
        self.presentation = {"state": "speaking", "next_title": title, "token": 2}
        with patch("local.music_surface_window.time.monotonic", return_value=100):
            self.window._sync_owned_playback_mode()
        with patch("local.music_surface_window.time.monotonic", return_value=100.4):
            old_icon, old_caption = self.window._personal_dj_geometry(QRectF(self.window.rect()), 100.4)
            self.speaking = False
            self.presentation = {"state": "idle", "next_title": "", "token": 3}
            self.window._sync_personal_dj_state()
            icon, caption = self.window._personal_dj_geometry(QRectF(self.window.rect()), 100.4)
            self.assertEqual(icon, old_icon)
            self.assertEqual(caption, old_caption)
            self.assertEqual(self.window._dj_next_title, title)
        with patch("local.music_surface_window.time.monotonic", return_value=100.6):
            self.window._sync_personal_dj_state()
            icon, caption = self.window._personal_dj_geometry(QRectF(self.window.rect()), 100.6)
            self.assertLess(caption.width(), old_caption.width())
            self.assertGreater(caption.width(), icon.width())
        with patch("local.music_surface_window.time.monotonic", return_value=100.8):
            self.window._sync_personal_dj_state()
            self.assertEqual(self.window._dj_next_title, "")
            icon, caption = self.window._personal_dj_geometry(QRectF(self.window.rect()), 100.8)
            self.assertEqual(icon, caption)

    def test_five_rings_complete_accent_right_left_right_cycle_then_hold_half_second(self):
        first = {identity: (x, height) for identity, x, height, _ in personal_dj_ring_poses(0)}
        low, high = 0, DJ_RING_TRAVEL_SECONDS
        for _ in range(40):
            middle = (low + high) / 2
            if personal_dj_travel_progress(middle) < .8:
                low = middle
            else:
                high = middle
        left_at = (low + high) / 2
        middle = {identity: (x, height) for identity, x, height, _ in personal_dj_ring_poses(left_at)}
        self.assertEqual(len(first), 5)
        self.assertLess(middle[4][0], first[4][0])
        self.assertAlmostEqual(middle[4][0], first[0][0])
        complete = {identity: (x, height) for identity, x, height, _ in personal_dj_ring_poses(DJ_RING_TRAVEL_SECONDS)}
        self.assertEqual(complete[4], first[4])
        self.assertEqual(personal_dj_ring_poses(DJ_RING_TRAVEL_SECONDS + .01),
                         personal_dj_ring_poses(DJ_RING_CYCLE_SECONDS - .01))
        self.assertEqual(personal_dj_ring_poses(0), personal_dj_ring_poses(DJ_RING_CYCLE_SECONDS * 3))

    def test_longer_ring_motion_is_continuous_fast_early_and_slow_near_the_hold(self):
        self.assertGreater(DJ_RING_TRAVEL_SECONDS, 1.5)
        self.assertEqual(DJ_RING_HOLD_SECONDS, .5)
        self.assertEqual(DJ_RING_CYCLE_SECONDS, DJ_RING_TRAVEL_SECONDS + DJ_RING_HOLD_SECONDS)
        travel = DJ_RING_TRAVEL_SECONDS
        values = [personal_dj_travel_progress(travel * index / 200) for index in range(201)]
        self.assertEqual(values[0], 0)
        self.assertEqual(values[-1], 1)
        self.assertTrue(all(0 <= value <= 1 for value in values))
        self.assertTrue(all(first <= second for first, second in zip(values, values[1:])))
        early_change = personal_dj_travel_progress(travel * .20) - personal_dj_travel_progress(travel * .15)
        late_change = personal_dj_travel_progress(travel * .85) - personal_dj_travel_progress(travel * .80)
        self.assertGreater(early_change, late_change * 8)
        epsilon = .0001
        self.assertLess(personal_dj_travel_progress(epsilon) - personal_dj_travel_progress(0), epsilon * .02)
        self.assertLess(personal_dj_travel_progress(travel) - personal_dj_travel_progress(travel - epsilon), epsilon * .02)
        self.assertEqual(personal_dj_travel_progress(-1), 0)
        self.assertEqual(personal_dj_travel_progress(travel + DJ_RING_HOLD_SECONDS * .5), 1)
        self.assertEqual(personal_dj_travel_progress(DJ_RING_CYCLE_SECONDS), 0)

    def test_five_ring_centers_preserve_transparent_and_colored_backdrops(self):
        rect = QRectF(16, 16, 288, 168)
        for backdrop in (Qt.GlobalColor.transparent, QColor("#f1ca69"), QColor("#234a69")):
            with self.subTest(backdrop=backdrop):
                image = QImage(320, 200, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(backdrop)
                original = image.copy()
                with QPainter(image) as painter:
                    paint_personal_dj_icon(painter, rect, QColor("#f19fc3"), elapsed=0)
                for _identity, x, _height, _opacity in personal_dj_ring_poses(0):
                    center = QPointF(rect.left() + rect.width() * x, rect.center().y())
                    # A hollow ellipse must leave its unobstructed center exact,
                    # including the input alpha; the outer glow is not a fill.
                    self.assertEqual(image.pixelColor(center.toPoint()), original.pixelColor(center.toPoint()))
                self.assertNotEqual(bytes(image.constBits()), bytes(original.constBits()))

    def test_overlapping_ring_interiors_do_not_erase_another_rings_outline(self):
        rect = QRectF(16, 16, 288, 168)
        poses = personal_dj_ring_poses(0)
        rendered = []
        for selected in (poses, (poses[1],), (poses[2],)):
            image = QImage(320, 200, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with patch("local.personal_dj_icon.personal_dj_ring_poses", return_value=selected), QPainter(image) as painter:
                paint_personal_dj_icon(painter, rect, QColor("#f19fc3"), elapsed=0)
            rendered.append(image)
        combined, earlier, later = rendered
        later_center = QPointF(rect.left() + rect.width() * poses[2][1], rect.center().y())
        # Observe production pixels from an earlier outline inside the next
        # ellipse. A filled later face would replace these bright strokes.
        crossings = []
        for y in range(20, 180):
            for x in range(round(later_center.x() - 20), round(later_center.x())):
                first, second = earlier.pixelColor(x, y), later.pixelColor(x, y)
                if first.alpha() > 40 and second.alpha() == 0:
                    crossings.append((x, y))
        self.assertGreater(len(crossings), 20)
        for x, y in crossings:
            actual = combined.pixelColor(x, y)
            self.assertEqual(actual, earlier.pixelColor(x, y))

    def test_each_hollow_ring_has_left_side_light_through_travel_and_frequency_deformation(self):
        rect = QRectF(16, 16, 288, 168)
        bands = AudioFrame(.2, .1, .05)
        for elapsed in (0, .2, .65):
            for identity, x, height_fraction, opacity in personal_dj_ring_poses(elapsed):
                if opacity < .9:
                    continue
                with self.subTest(elapsed=elapsed, identity=identity):
                    image = QImage(320, 200, QImage.Format.Format_RGB32)
                    image.fill(QColor("#000000"))
                    pose = ((identity, x, height_fraction, opacity),)
                    with patch("local.personal_dj_icon.personal_dj_ring_poses", return_value=pose), QPainter(image) as painter:
                        paint_personal_dj_icon(painter, rect, QColor("#f19fc3"), elapsed=elapsed, bands=bands)
                    center_x = round(rect.left() + rect.width() * x)
                    # Integrate the actual strokes on each half, so frequency
                    # deformation and native antialiasing cannot move a sample
                    # off the line and produce a false lighting comparison.
                    def light(left, right):
                        value = 0
                        for py in range(16, 184):
                            for px in range(max(0, left), min(320, right)):
                                pixel = image.pixelColor(px, py)
                                value += .2126 * pixel.red() + .7152 * pixel.green() + .0722 * pixel.blue()
                        return value
                    left, right = light(0, center_x), light(center_x, 320)
                    self.assertGreater(left, right * 1.4)
                    self.assertEqual(image.pixelColor(center_x, round(rect.center().y())), QColor("#000000"))

    def test_visible_idle_ring_animation_updates_only_local_damage_and_keeps_zero_raster_growth(self):
        self.enabled = True
        with patch("local.music_surface_window.time.monotonic", return_value=100):
            self.window._sync_owned_playback_mode()
            self.window._reset_control_idle(100)
        with patch("local.music_surface_window.time.monotonic", return_value=100.1), patch.object(self.window, "isVisible", return_value=True), patch.object(self.window, "update") as update:
            self.window._update_personal_dj_control(100.1)
        update.assert_called_once()
        self.assertLess(update.call_args.args[0].width(), self.window.width() * .35)
        self.assertLess(update.call_args.args[0].height(), self.window.height() * .35)

    def test_ripple_excludes_both_speech_ring_and_title_background(self):
        self.enabled = self.speaking = True
        self.presentation = {"state": "speaking", "next_title": "A real next song", "token": 4}
        with patch("local.music_surface_window.time.monotonic", return_value=100):
            self.window._sync_owned_playback_mode()
        with patch("local.music_surface_window.time.monotonic", return_value=100.4):
            image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor("#223344"))
            original = image.copy()
            self.window._transport_feedback = {"next": (100.0, QPointF(180, 260), 1.0, 4.0)}
            with patch.object(self.window, "_transport_feedback_frame", return_value=(1.0, .43, .9)):
                with QPainter(image) as painter:
                    self.window._paint_transport_spatial_light(painter, QRectF(self.window.rect()), 100.4)
            icon, caption = self.window._personal_dj_geometry(QRectF(self.window.rect()), 100.4)
            for protected in (QRectF(icon.center().x() - 3, icon.center().y() - 3, 6, 6), caption.adjusted(10, 4, -10, -4)):
                protected_crop = image.copy(protected.toAlignedRect())
                original_crop = original.copy(protected.toAlignedRect())
                self.assertEqual(bytes(protected_crop.constBits()), bytes(original_crop.constBits()))
            self.assertNotEqual(bytes(image.constBits()), bytes(original.constBits()))

    def test_fully_expanded_speech_has_caption_background_and_only_soft_ring_backdrop(self):
        self.enabled = self.speaking = True
        self.presentation = {"state": "speaking", "next_title": "A real next song", "token": 4}
        with patch("local.music_surface_window.time.monotonic", return_value=100):
            self.window._sync_owned_playback_mode()
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter, patch("local.music_surface_window.time.monotonic", return_value=100.4), patch(
            "local.music_surface_window.paint_personal_dj_icon",
        ):
            self.window._paint_personal_dj_control(painter, QRectF(self.window.rect()))
        icon, caption = self.window._personal_dj_geometry(QRectF(self.window.rect()), 100.4)
        ring_center = image.pixelColor(icon.center().toPoint())
        self.assertGreater(ring_center.alpha(), 40)
        self.assertLessEqual(ring_center.alpha(), 177)
        self.assertGreater(image.pixelColor(caption.center().toPoint()).alpha(), 200)

    def test_speech_backdrop_is_feathered_bounded_and_respects_parent_opacity(self):
        self.window._dj_next_title = "A real next song"
        region = QRectF(self.window.rect())
        with patch("local.music_surface_window.time.monotonic", return_value=100.4):
            bounds = self.window._personal_dj_backdrop_rect(region)
            image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                self.window._paint_personal_dj_backdrop(painter, region, 1)
            center = QPointF(region.right() - 1, region.bottom() - 1)
            alphas = [image.pixelColor(QPointF(center.x() - bounds.width() * fraction / math.sqrt(2),
                                              center.y() - bounds.height() * fraction / math.sqrt(2)).toPoint()).alpha()
                      for fraction in (0, .35, .68, .88, .98)]
            self.assertGreater(alphas[0], 170)
            self.assertLessEqual(alphas[0], 177)
            self.assertTrue(all(first > second for first, second in zip(alphas, alphas[1:])), alphas)
            self.assertLess(alphas[-1], 10)
            for edge in (bounds.topLeft(), bounds.topRight(), bounds.bottomLeft()):
                point = edge.toPoint()
                if image.rect().contains(point):
                    self.assertEqual(image.pixelColor(point).alpha(), 0)
            self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
            self.window._owned_dj_speaking = True
            self.assertTrue(self.window._personal_dj_damage_rect(region).contains(bounds.toAlignedRect()))

            reduced = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
            reduced.fill(Qt.GlobalColor.transparent)
            with QPainter(reduced) as painter:
                painter.setOpacity(.6)
                self.window._paint_personal_dj_backdrop(painter, region, .5)
                self.assertAlmostEqual(painter.opacity(), .6)
            self.assertGreater(reduced.pixelColor(center.toPoint()).alpha(), 49)
            self.assertLessEqual(reduced.pixelColor(center.toPoint()).alpha(), 54)

    def test_speech_backdrop_handoff_fades_with_activity_and_clears_after_closing(self):
        self.enabled = True
        self.window._reset_control_idle(100)
        self.presentation = {"state": "ready", "next_title": "The next real song", "token": 4}
        with patch("local.music_surface_window.time.monotonic", return_value=100):
            self.window._sync_owned_playback_mode()
        region = QRectF(self.window.rect())

        def mask_alpha(now):
            image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with patch("local.music_surface_window.time.monotonic", return_value=now), QPainter(image) as painter:
                self.window._paint_personal_dj_backdrop(painter, region, self.window._personal_dj_activity_at(now))
                center = QPointF(region.right() - 1, region.bottom() - 1).toPoint()
            return image.pixelColor(center).alpha()

        self.assertEqual(mask_alpha(100.4), 0)
        self.speaking = True
        self.presentation.update(state="speaking", speaking=True)
        with patch("local.music_surface_window.time.monotonic", return_value=101):
            self.window._sync_personal_dj_state()
        self.assertEqual(mask_alpha(101), 0)
        entering = mask_alpha(101.08)
        full = mask_alpha(101.4)
        self.assertGreater(entering, 0)
        self.assertLess(entering, full)
        self.speaking = False
        self.presentation.update(state="idle", speaking=False, next_title="", token=5)
        with patch("local.music_surface_window.time.monotonic", return_value=101.4):
            self.window._sync_personal_dj_state()
        self.assertEqual(mask_alpha(101.4), full)
        self.assertGreater(mask_alpha(101.48), 0)
        self.assertLess(mask_alpha(101.48), full)
        self.assertEqual(mask_alpha(101.8), 0)

    def test_backdrop_preserves_artwork_and_other_controls_at_all_supported_sizes(self):
        self.window._dj_next_title = "An intentionally long next-song title that needs elision"
        for size in ((640, 360), (960, 540), (1920, 1080)):
            with self.subTest(size=size):
                self.window.resize(*size)
                region = QRectF(self.window.rect())
                with patch("local.music_surface_window.time.monotonic", return_value=100.4):
                    bounds = self.window._personal_dj_backdrop_rect(region)
                    self.assertTrue(region.contains(bounds))
                    self.assertAlmostEqual(bounds.left() / region.width(), .68)
                    self.assertAlmostEqual(bounds.top() / region.height(), .44)
                    self.assertEqual(bounds.bottomRight(), region.bottomRight())
                    image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
                    image.fill(QColor("#ddb579"))
                    original = image.copy()
                    with QPainter(image) as painter:
                        self.window._paint_personal_dj_backdrop(painter, region, 1)
                    protected = [self.window._window_control_base_rect(region),
                                 self.window._library_button_face(region, 1)]
                    for centered in (0, 1):
                        self.window._cover_layout_from = self.window._cover_layout_to = centered
                        protected.append(self.window._current_cover_rect(100.4))
                        surface = QImage(self.window.size(), QImage.Format.Format_RGB32)
                        self.window.render(surface)
                        self.assertTrue(self.window._media_control_panel_rect.isValid())
                        protected.append(QRectF(self.window._media_control_panel_rect))
                    for slot in protected:
                        cropped = image.copy(slot.toAlignedRect())
                        before = original.copy(slot.toAlignedRect())
                        self.assertEqual(bytes(cropped.constBits()), bytes(before.constBits()))

    def test_expanded_speech_places_double_glyph_above_wide_title_inside_corner_mask(self):
        self.window._dj_next_title = "下一首真实歌曲"
        for size in ((640, 360), (960, 540), (1920, 1080)):
            with self.subTest(size=size):
                self.window.resize(*size)
                region = QRectF(self.window.rect())
                compact, _ = self.window._personal_dj_geometry(region, 100, expansion=0)
                icon, caption = self.window._personal_dj_geometry(region, 100.4, expansion=1)
                mask = self.window._personal_dj_backdrop_rect(region)
                self.assertAlmostEqual(icon.width(), compact.width() * 2)
                self.assertGreaterEqual(caption.width(), icon.width() * 1.9)
                self.assertGreater(caption.top() - icon.bottom(), icon.height() * .35)
                self.assertLess(caption.top() - icon.bottom(), icon.height() * .65)
                self.assertGreater(icon.center().x() / region.width(), .84)
                self.assertLess(icon.center().x() / region.width(), .94)
                self.assertGreater(icon.center().y() / region.height(), .68)
                self.assertLess(icon.center().y() / region.height(), .86)
                self.assertTrue(mask.contains(icon.united(caption)))

    def test_local_damage_expands_for_speech_and_repaints_previous_mask_on_exit(self):
        self.enabled = True
        with patch("local.music_surface_window.time.monotonic", return_value=100):
            self.window._sync_owned_playback_mode()
            self.window._reset_control_idle(100)
        region = QRectF(self.window.rect())
        with patch("local.music_surface_window.time.monotonic", return_value=100.4):
            idle = self.window._personal_dj_damage_rect(region)
        self.assertLess(idle.height(), region.height() * .35)
        self.speaking = True
        self.presentation = {"state": "speaking", "next_title": "下一首真实歌曲", "token": 2}
        with patch("local.music_surface_window.time.monotonic", return_value=101):
            self.window._sync_personal_dj_state()
        with patch("local.music_surface_window.time.monotonic", return_value=101.4):
            speech = self.window._personal_dj_damage_rect(region)
            self.window._dj_last_damage = speech
            self.assertTrue(speech.contains(self.window._personal_dj_backdrop_rect(region).toAlignedRect()))
            self.assertLess(speech.width() * speech.height(), region.width() * region.height() * .22)
        self.speaking = False
        self.presentation = {"state": "idle", "next_title": "", "token": 3}
        with patch("local.music_surface_window.time.monotonic", return_value=101.4):
            self.window._sync_personal_dj_state()
        with patch("local.music_surface_window.time.monotonic", return_value=101.8), patch.object(self.window, "isVisible", return_value=True), patch.object(self.window, "update") as update:
            self.window._update_personal_dj_control(101.8)
        self.assertTrue(any(args.args[0].contains(speech) for args in update.call_args_list))
        with patch("local.music_surface_window.time.monotonic", return_value=101.8):
            self.assertLess(self.window._personal_dj_damage_rect(region).height(), region.height() * .35)

    def test_real_frequency_glow_changes_glyph_without_raster_blur_or_cache_growth(self):
        self.enabled = self.speaking = True
        with patch("local.music_surface_window.time.monotonic", return_value=100):
            self.window._sync_owned_playback_mode()
            self.window._reset_control_idle(100)
        before = len(self.window._control_layer_cache)
        pixels = []
        for bands in (AudioFrame(0, 0, 0), AudioFrame(.2, 0, 0), AudioFrame(0, .2, 0), AudioFrame(0, 0, .2)):
            image = QImage(60, 60, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                paint_personal_dj_icon(painter, QRectF(12, 8, 36, 44), QColor("#f19fc3"), bands=bands)
            pixels.append(bytes(image.constBits()))
        self.assertEqual(len(set(pixels)), 4)
        with patch.object(self.window, "_build_control_blur", side_effect=AssertionError("DJ frame blur")):
            for _ in range(100):
                image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                with QPainter(image) as painter:
                    self.window._dj_audio_frame = AudioFrame(.2, .1, .05)
                    self.window._paint_personal_dj_control(painter, QRectF(self.window.rect()))
        self.assertEqual(len(self.window._control_layer_cache), before)
