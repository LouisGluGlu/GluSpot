"""Item 7: real levels, palette water/light, and temporary lyricless cover."""
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

    def test_sea_ignores_playing_fallback_bands_but_reads_absolute_low_onsets(self):
        from local.audio_reactive_engine import AudioReactiveRuntime
        runtime = AudioReactiveRuntime()
        frame = [AudioFrame(0., 0., 0., peak=0.)]
        read_frame = Mock(side_effect=lambda: frame[0])
        host = SimpleNamespace(data_provider=SimpleNamespace(get_audio_frame=read_frame,
                                   has_live_audio=lambda: False),
                               media_is_playing=True, wave_values=[.1] * 36, ambient_phase=1.25,
                               _audio_reactive_runtime_for_current_settings=lambda: runtime,
                               _effective_music_ambience_style=lambda: "sea_level",
                               _invalidate_music_reactive_snapshot=Mock())
        host._music_wave_input_snapshot = lambda now: IslandStackVisual._music_wave_input_snapshot(host, now)
        host._music_wave_update_plan = lambda now: IslandStackVisual._music_wave_update_plan(host, now)
        for tick in range(90):
            with patch("local.island_stack_visual.time.monotonic", return_value=tick / 30.):
                IslandStackVisual._update_wave_values(host)
        self.assertGreater(host.current_audio_bands[0], 0., "Exercise the real nonzero playing fallback")
        self.assertEqual(host._sea_low_sample, 0.)
        self.assertLess(max(abs(host._sea_wave_state.ripple_displacement(i / 48.)) for i in range(49)), .25)
        self.assertEqual(read_frame.call_count, 90, "All absolute features share one provider read per tick")

        frame[0] = AudioFrame(.64, .1, .1, peak=.64)
        with patch("local.island_stack_visual.time.monotonic", return_value=3.):
            IslandStackVisual._update_wave_values(host)
        level = host._sea_wave_state.ripple_level
        self.assertEqual(host._sea_low_sample, .64)
        self.assertAlmostEqual(level, .8 * -math.expm1(-(1 / 30.) / .060))
        peak_only = SeaWaveState()
        for tick in range(90):
            peak_only.update_ripple(0., tick / 30.)
        peak_only.update_ripple(.64, 3.)
        self.assertGreater(host._sea_wave_state.ripple_beat, .8)
        self.assertGreater(max(abs(host._sea_wave_state.ripple_displacement(i / 48.)) for i in range(49)),
                           max(abs(peak_only.ripple_displacement(i / 48.)) for i in range(49)))
        frame[0] = None
        with patch("local.island_stack_visual.time.monotonic", return_value=3.033):
            IslandStackVisual._update_wave_values(host)
        self.assertEqual(host._sea_low_sample, 0.)
        self.assertIsNone(host._sea_peak_sample)
        self.assertLess(host._sea_wave_state.ripple_level, level)

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

    @staticmethod
    def _render(renderer, *, dpr=1, body=True):
        image = QImage(520 * dpr, 136 * dpr, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(QColor("black"))
        with QPainter(image) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if body:
                renderer.paint_sea_level_ambience(painter, QRectF(0, 0, 520, 136), 24, True)
            renderer.paint_sea_level_wave(painter, QRectF(0, 0, 520, 136), 24, True)
        return image

    @staticmethod
    def _rgb(image):
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        return np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine() // 4, 4)[:, :image.width(), :3].copy()

    def test_palette_body_and_light_keep_hue_and_all_motion_stays_in_strip(self):
        renders = []
        for color in (QColor("#ca102d"), QColor("#c01a91"), QColor("#087d86")):
            host = PaintHost({})
            host._xiaomi_duotone_palette = lambda: (color, color, color)
            renderer = NativeMusicBackgroundRenderer(host)
            rect = QRectF(0, 0, 520, 136)
            images = []
            for peak in (.0004, .81):
                host._sea_wave_state = SeaWaveState()
                for n in range(90):
                    host._sea_wave_state.update_ripple(peak, n / 30)
                image = self._render(renderer)
                images.append(self._rgb(image))
                body_pixel = image.pixelColor(260, 77)
                self.assertLess(abs(body_pixel.hslHueF() - color.hslHueF()), .025)
                # The moderate wave and its graded light stay inside
                # the bounded damage strip.
                strip = renderer.sea_level_wave_strip(rect).toAlignedRect()
                crest = images[-1][strip.top():strip.bottom()+1, 20:500]
                brightest = crest.reshape(-1, 3)[np.argmax(crest.sum(axis=2))].astype(int)
                self.assertGreater(np.ptp(brightest), 15)
                # A translucent palette hairline is readable in quiet water
                # too; the former absent-quiet-crest rule no longer applies.
                self.assertGreater(brightest.max(), 100)
                self.assertLess(brightest.max(), 240)
            strip = renderer.sea_level_wave_strip(rect).toAlignedRect()
            self.assertTrue(np.array_equal(images[0][:strip.top()], images[1][:strip.top()]))
            self.assertTrue(np.array_equal(images[0][strip.bottom()+1:], images[1][strip.bottom()+1:]))
            self.assertFalse(np.array_equal(images[0], images[1]))
            self.assertGreater(images[1].max(), images[0].max())
            renders.append(images[1])
        self.assertFalse(np.array_equal(renders[0], renders[1]))
        self.assertFalse(np.array_equal(renders[1], renders[2]))

    def test_audio_grows_downward_glow_depth_and_crest_brightness(self):
        for dpr in (1, 2):
            columns = []
            for peak in (0., .81):
                host = PaintHost({})
                color = QColor("#c01a91")
                host._xiaomi_duotone_palette = lambda: (color, color, color)
                state = host._sea_wave_state = SeaWaveState()
                # Hold geometry flat on the audio clock, so both the current
                # surface and its shared history describe the same water.
                state.ripple_displacement = lambda _pos: 0.
                for n in range(90):
                    state.update_ripple(peak, n / 30)
                columns.append(self._rgb(self._render(NativeMusicBackgroundRenderer(host), dpr=dpr, body=False))[:, 260*dpr].max(axis=1))
            low, high = columns
            crest = round(136 * .495 * dpr)
            self.assertGreater(high.max(), low.max() + 25)
            self.assertEqual(np.count_nonzero(low[crest+2*dpr:] > 8), 0)
            self.assertGreater(np.count_nonzero(high[crest+2*dpr:] > 8), 7*dpr)

    def test_dark_palette_is_lifted_without_losing_hue_or_neutrality(self):
        for source in (QColor("#180305"), QColor("#181313"), QColor("#04031c"), QColor("#031816"), QColor("#080808"), QColor("#f7f7f7")):
            host = PaintHost({})
            host._xiaomi_duotone_palette = lambda: (source, source, source)
            renderer = NativeMusicBackgroundRenderer(host)
            sea, line = renderer._sea_level_colors(renderer.render_context("sea_level", expanded=True))
            self.assertGreater(sea.lightnessF(), .25)
            self.assertGreater(line.lightnessF(), .8)
            if source.hslSaturationF() > .04:
                self.assertAlmostEqual(sea.hslHueF(), source.hslHueF(), places=3)
                self.assertAlmostEqual(line.hslHueF(), source.hslHueF(), places=3)
                self.assertGreater(sea.hslSaturationF(), source.hslSaturationF() * .95)
                self.assertGreater(line.hslSaturationF(), .2)
            else:
                self.assertEqual(sea.red(), sea.green())
                self.assertEqual(line.red(), line.green())
            state = host._sea_wave_state = SeaWaveState()
            calm_peak = int(self._rgb(self._render(renderer)).max())
            self.assertGreater(calm_peak, 80)
            self.assertLess(calm_peak, 245)
            for n in range(90):
                state.update_ripple(.81, n / 30)
            self.assertGreater(int(self._rgb(self._render(renderer)).max()), calm_peak)

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
        # The 104-unit wave strip includes the larger crest and glow while
        # avoiding a full-shell repaint and preserving the top header.
        self.assertLess(damage.height(), rect.height() * .8)
        self.assertGreaterEqual(damage.top(), rect.top() + 22.)
        motion.vw = 1.
        self.assertIsNone(IslandStackVisual._sea_wave_only_damage(host, 10.))
        motion.vw = 0.
        host.surface_transition_pixmap = QPixmap(1, 1)
        self.assertIsNone(IslandStackVisual._sea_wave_only_damage(host, 10.))
        host.surface_transition_pixmap = QPixmap()
        host._music_metadata_scrolls = True
        self.assertIsNone(IslandStackVisual._sea_wave_only_damage(host, 10.))

    def test_high_dpi_light_invalidates_same_state_raster_on_palette_change(self):
        host = PaintHost({})
        state = host._sea_wave_state = SeaWaveState()
        renderer = NativeMusicBackgroundRenderer(host)
        images = []
        for n in range(90):
            state.update_ripple(.81, n / 30)
        for color in (QColor("#ca102d"), QColor("#087d86")):
            host._xiaomi_duotone_palette = lambda: (color, color, color)
            images.append(self._render(renderer, dpr=2, body=False))
        self.assertNotEqual(images[0], images[1])
        for image in images:
            pixels = self._rgb(image).astype(int)
            # The new crest is saturated palette ink; test visibility and
            # color separation without demanding the former pale stroke.
            self.assertGreater(pixels.max(), 100)
            self.assertGreater(np.ptp(pixels, axis=2).max(), 50)

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
