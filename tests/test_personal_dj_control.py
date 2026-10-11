from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat
from PySide6.QtWidgets import QApplication, QWidget

from api.models import AudioFrame
from local.owned_audio_bridge import OwnedAudioBridge
from local.personal_dj_icon import paint_personal_dj_icon
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
        self.provider.get_owned_personal_dj_enabled = lambda: self.enabled if self.provider.fm and self.provider.owned else None
        self.provider.is_owned_personal_dj_speaking = lambda: self.speaking
        self.provider.get_owned_personal_dj_audio_frame = lambda: AudioFrame(.2, .1, .05)
        self.provider.set_owned_personal_dj_enabled = self.set_enabled
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

    def test_button_is_below_right_of_fm_and_has_own_round_hit_shape(self):
        for size in ((640, 360), (960, 540), (1920, 1080)):
            self.window.resize(*size)
            region = QRectF(self.window.rect())
            dj = self.window._personal_dj_button_rect(region)
            fm = self.window._playback_mode_button_rect(self.window._window_control_base_rect(region), self.window._surface_scale(region))
            self.assertGreater(dj.top(), fm.bottom())
            self.assertGreater(dj.center().x(), fm.center().x())
            self.assertTrue(region.contains(dj))
            self.assertTrue(self.window._window_control_contains("personal_dj", dj, dj.center()))
            self.assertFalse(self.window._window_control_contains("personal_dj", dj, dj.topLeft()))

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
