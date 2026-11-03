"""Item 7: real levels, silver expanded ripple, and temporary lyricless cover."""
from contextlib import contextmanager
import ctypes
import math
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.audio_analyzer import LiveAudioAnalyzer, RenderPeakMeter, analyze_frequency_block
from api.models import AudioFrame
from api.providers import EmptyIslandDataProvider, WindowsMediaProvider
from local.island_stack_visual import IslandStackVisual, SpringRect, IslandKind
from local.music_background_renderer import NativeMusicBackgroundRenderer
from local.sea_wave import SeaWaveState
from tests.test_music_background_renderer import PaintHost


class RippleLevelTests(unittest.TestCase):
    def test_absolute_quiet_and_loud_stay_distinct(self):
        states = [SeaWaveState(), SeaWaveState()]
        for n in range(180):
            for state, level in zip(states, (.0004, .64)):
                state.update_ripple(level, n / 30)
        self.assertAlmostEqual(states[0].ripple_level, .02, places=5)
        self.assertAlmostEqual(states[1].ripple_level, .8, places=5)
        ranges = [np.ptp([s.ripple_displacement(i / 48) for i in range(49)]) for s in states]
        self.assertLess(ranges[0], .8)
        self.assertGreater(ranges[1], 8)

    def test_attack_release_time_constants_and_pause(self):
        state = SeaWaveState()
        state.update_ripple(0, 0)
        state.update_ripple(1, .06)
        self.assertAlmostEqual(state.ripple_level, 1 - math.exp(-1))
        peak = state.ripple_level
        state.update_ripple(0, .31)
        self.assertAlmostEqual(state.ripple_level, peak / math.e)
        for n in range(120):
            state.update_ripple(1, .31 + (n + 1) / 30, is_playing=False)
        self.assertLess(state.ripple_level, .0001)

    def test_unavailable_breathes_but_real_zero_settles(self):
        unavailable, silent = SeaWaveState(), SeaWaveState()
        for n in range(300):
            unavailable.update_ripple(None, n / 30)
            silent.update_ripple(0, n / 30)
        self.assertGreater(unavailable.ripple_level, .02)
        self.assertLess(unavailable.ripple_level, .07)
        self.assertEqual(silent.ripple_level, 0)

    def test_layered_motion_is_idempotent_and_continuous(self):
        state = SeaWaveState()
        for n in range(90):
            state.update_ripple(.64, n / 30)
        before = [state.ripple_displacement(i / 48) for i in range(49)]
        self.assertEqual(before, [state.ripple_displacement(i / 48) for i in range(49)])
        state.update_ripple(.64, 3.)
        after = [state.ripple_displacement(i / 48) for i in range(49)]
        self.assertLess(max(abs(a-b) for a, b in zip(before, after)), .3)
        self.assertGreater(np.ptp(before), 8)


class PeakInputTests(unittest.TestCase):
    def test_pcm_peak_preserves_antiphase_and_silence(self):
        tone = .6 * np.sin(np.arange(2205) * math.tau * 1000 / 44100)
        smooth = [.4, .3, .2]
        frame = analyze_frequency_block(np.column_stack((tone, -tone)), 44100, np, smooth)
        self.assertAlmostEqual(frame.peak, .6, places=4)
        silent = analyze_frequency_block(np.zeros((1024, 2)), 44100, np, smooth)
        self.assertIsNone(silent)
        self.assertEqual(smooth, [0, 0, 0])
        self.assertIsNone(analyze_frequency_block(np.full((2, 2), np.nan), 44100, np, smooth))

    def test_capture_silence_releases_peak_without_renewing_sound_detection(self):
        with patch.object(LiveAudioAnalyzer, "_start_capture"):
            analyzer = LiveAudioAnalyzer()
        analyzer._running = True
        analyzer._frame = AudioFrame(.8, .5, .2, peak=.8)
        analyzer._last_real_frame_at = 100.
        reads = []

        class Recorder:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def record(self, **_kwargs):
                reads.append(1)
                if len(reads) == 2:
                    analyzer._running = False
                return np.zeros((1024, 2), dtype=np.float32)

        loopback = SimpleNamespace(recorder=lambda **_kwargs: Recorder())
        with patch("api.audio_analyzer.time.monotonic", return_value=100.2):
            analyzer._capture_endpoint(None, loopback, "output", RuntimeWarning, np)
        self.assertEqual(analyzer._frame, AudioFrame(0, 0, 0, peak=0))
        self.assertEqual(analyzer._last_real_frame_at, 100.)

    def test_meter_zero_is_real_stale_is_unknown_and_stop_releases_worker(self):
        observed, released = threading.Event(), threading.Event()
        worker_ids = []

        @contextmanager
        def reader():
            worker_ids.append(threading.get_ident())
            try:
                def read():
                    observed.set()
                    return 0.
                yield read
            finally:
                released.set()

        meter = RenderPeakMeter(reader)
        meter.get_frame()
        self.assertTrue(observed.wait(1))
        # Publication follows the read; wait boundedly for the cached frame.
        deadline = time.monotonic() + 1
        while meter.recent_frame() is None and time.monotonic() < deadline:
            threading.Event().wait(.001)
        self.assertEqual(meter.recent_frame().peak, 0.)
        self.assertIsNone(meter.recent_frame(time.monotonic() + .3))
        self.assertNotEqual(worker_ids[0], threading.get_ident())
        meter.stop()
        self.assertTrue(released.wait(1))
        thread = meter._thread
        if thread is not None:
            thread.join(1)
            self.assertFalse(thread.is_alive())
        self.assertIsNone(meter.get_frame())

    def test_meter_parks_without_demand_and_resumes(self):
        released, resumed = threading.Event(), threading.Event()
        entries = []

        @contextmanager
        def reader():
            entries.append(1)
            if len(entries) > 1:
                resumed.set()
            try:
                yield lambda: .25
            finally:
                released.set()

        meter = RenderPeakMeter(reader)
        try:
            meter.get_frame()
            self.assertTrue(released.wait(1.5))
            self.assertIsNone(meter.recent_frame())
            meter.get_frame()
            self.assertTrue(resumed.wait(1))
        finally:
            meter.stop()
            thread = meter._thread
            if thread is not None:
                thread.join(1)

    def test_provider_fallback_and_passive_availability(self):
        host = SimpleNamespace(_ensure_audio_analyzer_if_requested=lambda: None,
                               _render_peak_frame=Mock(return_value=AudioFrame(0, 0, 0, peak=.5)))
        self.assertEqual(WindowsMediaProvider.get_audio_frame(host).peak, .5)
        host._render_peak_meter = SimpleNamespace(recent_frame=lambda: AudioFrame(0, 0, 0, peak=0))
        self.assertFalse(WindowsMediaProvider.has_live_audio(host))
        host._render_peak_frame.assert_called_once()

    def test_peak_refreshes_even_while_band_cache_is_warm(self):
        from local.audio_reactive_engine import AudioReactiveRuntime
        runtime = AudioReactiveRuntime()
        frame = [AudioFrame(.2, .1, .05, peak=.25)]
        host = SimpleNamespace(data_provider=SimpleNamespace(get_audio_frame=lambda: frame[0]),
                               _audio_reactive_runtime_for_current_settings=lambda: runtime,
                               _effective_music_ambience_style=lambda: "sea_level")
        IslandStackVisual._music_wave_input_snapshot(host, 1.)
        self.assertEqual(host._sea_peak_sample, .25)
        frame[0] = AudioFrame(.2, .1, .05, peak=0.)
        snapshot = IslandStackVisual._music_wave_input_snapshot(host, 1.03)
        self.assertEqual(snapshot.band_source, "cache")
        self.assertEqual(host._sea_peak_sample, 0.)
        frame[0] = None
        IslandStackVisual._music_wave_input_snapshot(host, 1.06)
        self.assertIsNone(host._sea_peak_sample)

    def test_peak_only_meter_never_authorizes_metadata_live_audio(self):
        from local.audio_reactive_engine import AudioReactiveRuntime
        for peak in (0., .75):
            runtime = AudioReactiveRuntime()
            host = SimpleNamespace(data_provider=SimpleNamespace(
                                       get_audio_frame=lambda: AudioFrame(0, 0, 0, peak=peak),
                                       has_live_audio=lambda: False),
                                   media_is_playing=True,
                                   _audio_reactive_runtime_for_current_settings=lambda: runtime,
                                   _effective_music_ambience_style=lambda: "sea_level")
            for now in (1., 1.03, 1.2):
                snapshot = IslandStackVisual._music_wave_input_snapshot(host, now)
                self.assertFalse(snapshot.live_audio_available)
                self.assertEqual(host._sea_peak_sample, peak)

    def test_sdk_peak_binding_only_reads_and_releases(self):
        from api import windows_audio_devices as native
        calls = []
        ole = SimpleNamespace(CoInitializeEx=lambda *_: 0, CoUninitialize=Mock(),
                              CoCreateInstance=Mock())
        adapter = native._WindowsCoreAudioAdapter.__new__(native._WindowsCoreAudioAdapter)
        adapter._ole32 = ole

        def invoke(pointer, slot, types, *args, **kwargs):
            calls.append((pointer.value, slot))
            if slot == 2:
                return 0
            if pointer.value == 1:
                self.assertEqual((slot, args[:2]), (4, (0, 1)))
                ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_void_p))[0] = 2
            elif pointer.value == 2:
                self.assertEqual((slot, args[1:3]), (3, (23, None)))
                ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_void_p))[0] = 3
            else:
                self.assertEqual(slot, 3)
                ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_float))[0] = .625
            return 0

        with patch.object(native._ComScope, "create", lambda scope, *_: scope.own(ctypes.c_void_p(1))), patch.object(native, "_call", invoke):
            with adapter.default_peak_reader() as read:
                self.assertEqual(read(), .625)
        self.assertEqual(calls, [(1, 4), (2, 3), (3, 3), (3, 2), (2, 2), (1, 2)])
        ole.CoUninitialize.assert_called_once()


class ExpandedRippleRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_saturated_covers_cannot_tint_expanded_sea_and_damage_is_bounded(self):
        renders = []
        for color in (QColor("red"), QColor("magenta"), QColor("blue")):
            host = PaintHost({})
            host._solid_album_background_color = lambda: color
            host._sea_wave_state = SeaWaveState()
            for n in range(60):
                host._sea_wave_state.update_ripple(.64, n / 30)
            renderer = NativeMusicBackgroundRenderer(host)
            rect = QRectF(0, 0, 520, 136)
            image = QImage(520, 136, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor(0, 0, 0))
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.paint_sea_level_ambience(painter, rect, 24, True)
            before = QImage(image)
            renderer.paint_sea_level_wave(painter, rect, 24, True)
            painter.end()
            strip = renderer.sea_level_wave_strip(rect).toAlignedRect()
            for y in range(image.height()):
                for x in range(image.width()):
                    pixel = image.pixelColor(x, y)
                    self.assertEqual(pixel.red(), pixel.green())
                    self.assertEqual(pixel.green(), pixel.blue())
                    if not strip.contains(x, y):
                        self.assertEqual(pixel, before.pixelColor(x, y))
            self.assertGreater(max(image.pixelColor(260, y).red() for y in range(136)), 200)
            renders.append(image)
        self.assertEqual(renders[0], renders[1])
        self.assertEqual(renders[1], renders[2])

    def test_resolved_miss_uses_cover_and_recovery_holds_without_settings_write(self):
        visual = IslandStackVisual(EmptyIslandDataProvider())
        visual.timer.stop()
        self.addCleanup(visual.close)
        visual.track_title, visual.artist, visual.media_track_id = "Waves", "Yo Trane", "waves"
        visual.lyrics = ["暂无歌词"]
        visual.timed_lyrics = []
        visual._island_cleaned_lyric_rows = lambda: tuple(visual.lyrics)
        visual._notify_settings_changed = Mock()
        self.assertTrue(visual._expanded_no_lyrics_cover_active())
        visual.lyrics = ["查找歌词中"]
        self.assertTrue(visual._expanded_no_lyrics_cover_active())
        visual._paint_album_island_expanded = Mock()
        visual._paint_lyric_viewport = Mock()
        image = QImage(520, 136, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        visual._paint_music_expanded(painter, QRectF(0, 0, 520, 136))
        painter.end()
        visual._paint_album_island_expanded.assert_called_once()
        visual._paint_lyric_viewport.assert_not_called()
        visual._notify_settings_changed.assert_not_called()
        visual.lyrics = ["A real lyric"]
        self.assertFalse(visual._expanded_no_lyrics_cover_active())
        visual.media_track_id, visual.lyrics = "next", ["查找歌词中"]
        self.assertFalse(visual._expanded_no_lyrics_cover_active())

    def test_metadata_only_miss_does_not_turn_real_intro_into_cover(self):
        host = SimpleNamespace(track_title="Song", artist="Artist", media_track_id="id",
                               _music_surface_waiting_for_loaded_lyrics=lambda: False,
                               _island_cleaned_lyric_rows=lambda: ("", ""))
        self.assertTrue(IslandStackVisual._expanded_no_lyrics_cover_active(host))
        host._island_cleaned_lyric_rows = lambda: ("", "The verse starts later")
        self.assertFalse(IslandStackVisual._expanded_no_lyrics_cover_active(host))

    def test_settled_cover_damage_rejects_pending_cleanup_and_motion(self):
        from PySide6.QtGui import QPixmap
        rect = QRectF(2, 2, 520, 136)
        motion = SpringRect(rect)
        motion.opacity = 1.
        host = SimpleNamespace(expanded=True, _expanded_cover_only=True,
                               motions={"music": motion},
                               _effective_music_ambience_style=lambda: "sea_level",
                               _active_visual_animation_plan=lambda *_args, **_kw: SimpleNamespace(active=False),
                               _album_island_transition_progress=lambda _now: 1.,
                               _build_visuals=lambda: [SimpleNamespace(kind=IslandKind.MUSIC, key="music", rect=rect)],
                               _native_music_background_renderer=lambda: NativeMusicBackgroundRenderer(PaintHost({})))
        damage = IslandStackVisual._sea_wave_only_damage(host, 10.)
        self.assertLess(damage.height(), rect.height() * .5)
        motion.vw = 1.
        self.assertIsNone(IslandStackVisual._sea_wave_only_damage(host, 10.))
        motion.vw = 0.
        host.surface_transition_pixmap = QPixmap(1, 1)
        self.assertIsNone(IslandStackVisual._sea_wave_only_damage(host, 10.))
        host.surface_transition_pixmap = QPixmap()
        host._music_metadata_scrolls = True
        self.assertIsNone(IslandStackVisual._sea_wave_only_damage(host, 10.))

    def test_high_dpi_light_remains_neutral_and_tracks_new_levels(self):
        host = PaintHost({})
        state = host._sea_wave_state = SeaWaveState()
        renderer = NativeMusicBackgroundRenderer(host)
        images = []
        for level in (0., .81):
            for n in range(90):
                state.update_ripple(level, len(images) * 3 + n / 30)
            image = QImage(1040, 272, QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(2)
            image.fill(QColor("black"))
            with QPainter(image) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                renderer.paint_sea_level_wave(painter, QRectF(0, 0, 520, 136), 24, True)
            images.append(image)
        self.assertNotEqual(images[0], images[1])
        self.assertGreater(max(images[1].pixelColor(520, y).red() for y in range(272)), 180)
        for image in images:
            for y in range(0, 272, 3):
                pixel = image.pixelColor(520, y)
                self.assertEqual(pixel.red(), pixel.green())
                self.assertEqual(pixel.green(), pixel.blue())

    def test_explicit_preview_spectrum_still_shapes_the_surface(self):
        host = PaintHost({})
        host.sea_shape_mode = "spectrum"
        state = host._sea_wave_state = SeaWaveState()
        spectrum = [0.] * 24
        spectrum[5] = 1.
        for n in range(60):
            state.update((.2, .1, .05), n / 30, spectrum=spectrum)
        renderer = NativeMusicBackgroundRenderer(host)
        points = renderer._sea_level_surface(renderer.render_context("sea_level", expanded=True), QRectF(0, 0, 520, 136), True)
        self.assertGreater(max(p.y() for p in points) - min(p.y() for p in points), 10.)
