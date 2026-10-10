from __future__ import annotations

import threading
import time
import unittest
import warnings
from types import SimpleNamespace
from unittest.mock import patch

import numpy  # Keep its native extension loaded across the fake-backend contexts.

from api.audio_analyzer import LiveAudioAnalyzer
from api.models import AudioFrame
from api.providers import WindowsMediaProvider


class _CaptureWarning(RuntimeWarning):
    pass


class AudioAnalyzerCaptureTests(unittest.TestCase):
    def _analyzer(self):
        with patch.object(LiveAudioAnalyzer, "_start_capture"):
            analyzer = LiveAudioAnalyzer()
        analyzer._running = True
        analyzer._analyze_block = lambda *_args: AudioFrame(0.2, 0.3, 0.4)
        return analyzer

    def _backend(self, speaker, get_loopback):
        backend = SimpleNamespace(default_speaker=speaker, get_microphone=get_loopback)
        return patch.dict("sys.modules", {
            "soundcard": backend,
            "soundcard.mediafoundation": SimpleNamespace(SoundcardRuntimeWarning=_CaptureWarning),
        })

    def test_visual_meter_uses_shared_headroom_buffer_without_slowing_its_reads(self):
        analyzer = self._analyzer()
        settings = []
        reads = []

        class Recorder:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def record(self, *, numframes):
                reads.append(numframes)
                if len(reads) == 2:
                    analyzer._running = False
                return object()

        def recorder(**kwargs):
            settings.append(kwargs)
            return Recorder()

        loopback = SimpleNamespace(isloopback=True, recorder=recorder)
        with self._backend(lambda: SimpleNamespace(id="stereo-output"), lambda **_kwargs: loopback):
            analyzer._capture_loop()

        self.assertEqual(settings, [{"samplerate": 44100, "channels": 2, "blocksize": 4096, "exclusive_mode": False}])
        self.assertEqual(reads, [1024, 1024])
        self.assertEqual(analyzer._frame, AudioFrame(0.2, 0.3, 0.4))
        self.assertFalse(analyzer.diagnostics()["capture_active"])

    def test_repeated_discontinuities_close_capture_without_reopening_same_output(self):
        analyzer = self._analyzer()
        lifecycle = []
        opens = []
        polls = []

        class Recorder:
            def __enter__(self):
                lifecycle.append("enter")
                return self

            def __exit__(self, *_args):
                lifecycle.append("exit")
                return False

            def record(self, *, numframes):
                lifecycle.append("read")
                warnings.warn("data discontinuity in recording", _CaptureWarning)
                return object()

        class StopAfterPolling:
            def wait(self, _timeout):
                polls.append("wait")
                if len(polls) == 4:
                    analyzer._running = False
                    return True
                return False

        analyzer._stop_event = StopAfterPolling()
        loopback = SimpleNamespace(isloopback=True, recorder=lambda **_kwargs: Recorder())

        def get_loopback(**kwargs):
            opens.append(kwargs["id"])
            return loopback

        with self._backend(lambda: SimpleNamespace(id="bluetooth-output"), get_loopback), patch(
            "api.audio_analyzer.time.monotonic", return_value=100.0
        ):
            analyzer._capture_loop()

        self.assertEqual(opens, ["bluetooth-output"])
        self.assertEqual(lifecycle, ["enter", "read", "read", "read", "exit"])
        diagnostics = analyzer.diagnostics()
        self.assertEqual(diagnostics["status"], "suspended")
        self.assertEqual(diagnostics["capture_discontinuities"], 3)
        self.assertEqual(diagnostics["suspended_device_id"], "bluetooth-output")
        self.assertFalse(diagnostics["capture_active"])
        self.assertFalse(diagnostics["has_recent_frame"])

    def test_isolated_discontinuities_do_not_suspend_capture(self):
        analyzer = self._analyzer()
        analyzed = []
        analyzer._analyze_block = lambda *_args: analyzed.append(1)
        reads = []

        class Recorder:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def record(self, *, numframes):
                reads.append(numframes)
                if len(reads) == 4:
                    analyzer._running = False
                else:
                    warnings.warn("data discontinuity in recording", _CaptureWarning)
                return object()

        loopback = SimpleNamespace(isloopback=True, recorder=lambda **_kwargs: Recorder())
        with self._backend(lambda: SimpleNamespace(id="output"), lambda **_kwargs: loopback), patch(
            "api.audio_analyzer.time.monotonic", side_effect=[100.0, 100.1, 102.2, 104.4]
        ):
            analyzer._capture_loop()

        self.assertEqual(len(analyzed), 3)
        self.assertEqual(analyzer._capture_discontinuities, 3)
        self.assertEqual(analyzer._status, "capturing")
        self.assertEqual(analyzer._suspended_device_id, "")

    def test_output_change_closes_old_stream_before_opening_new_loopback(self):
        analyzer = self._analyzer()
        lifecycle = []
        defaults = iter(["old-output", "new-output", "new-output"])

        class Recorder:
            def __init__(self, endpoint_id):
                self.endpoint_id = endpoint_id

            def __enter__(self):
                lifecycle.append((self.endpoint_id, "enter"))
                return self

            def __exit__(self, *_args):
                lifecycle.append((self.endpoint_id, "exit"))
                return False

            def record(self, *, numframes):
                lifecycle.append((self.endpoint_id, "read"))
                if self.endpoint_id == "new-output":
                    analyzer._running = False
                return object()

        def get_loopback(*, id, include_loopback):
            self.assertTrue(include_loopback)
            return SimpleNamespace(isloopback=True, recorder=lambda **_kwargs: Recorder(id))

        with self._backend(lambda: SimpleNamespace(id=next(defaults)), get_loopback), patch(
            "api.audio_analyzer.time.monotonic", side_effect=[100.0, 102.1, 102.2]
        ):
            analyzer._capture_loop()

        self.assertEqual(lifecycle, [
            ("old-output", "enter"), ("old-output", "read"), ("old-output", "exit"),
            ("new-output", "enter"), ("new-output", "read"), ("new-output", "exit"),
        ])
        self.assertEqual(analyzer._capture_restarts, 1)

    def test_failed_capture_waits_for_output_change_before_a_single_recovery(self):
        analyzer = self._analyzer()
        defaults = iter(["failed-output", "failed-output", "failed-output", "new-output", "new-output"])
        opened = []
        waits = []

        class Recorder:
            def __init__(self, endpoint_id):
                self.endpoint_id = endpoint_id

            def __enter__(self):
                opened.append(self.endpoint_id)
                return self

            def __exit__(self, *_args):
                return False

            def record(self, *, numframes):
                if self.endpoint_id == "failed-output":
                    raise RuntimeError("device invalidated")
                analyzer._running = False
                return object()

        def get_loopback(*, id, include_loopback):
            return SimpleNamespace(isloopback=True, recorder=lambda **_kwargs: Recorder(id))

        analyzer._stop_event = SimpleNamespace(wait=lambda timeout: waits.append(timeout) or False)
        with self._backend(lambda: SimpleNamespace(id=next(defaults)), get_loopback):
            analyzer._capture_loop()

        self.assertEqual(opened, ["failed-output", "new-output"])
        self.assertEqual(waits, [2.0, 2.0, 2.0])
        self.assertEqual(analyzer._capture_restarts, 1)
        self.assertEqual(analyzer._last_error, "")
        self.assertEqual(analyzer._suspended_device_id, "")

    def test_stop_wakes_a_suspended_monitor_without_waiting_for_poll_interval(self):
        analyzer = self._analyzer()
        entered = threading.Event()

        class MonitorEvent:
            def __init__(self):
                self.event = threading.Event()

            def wait(self, timeout):
                entered.set()
                return self.event.wait(timeout)

            def set(self):
                self.event.set()

        analyzer._stop_event = MonitorEvent()
        analyzer._status = "suspended"
        backend = SimpleNamespace(default_speaker=lambda: self.fail("stop must wake before another output probe"))
        thread = threading.Thread(target=analyzer._wait_for_output_change, args=(backend, "output"))
        analyzer._thread = thread
        thread.start()
        self.assertTrue(entered.wait(0.5))
        start = time.monotonic()
        analyzer.stop()
        thread.join(timeout=0.5)

        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertEqual(analyzer._status, "stopped")

    def test_provider_keeps_suspended_analyzer_on_fallback_without_retrying_stream(self):
        analyzer = self._analyzer()
        analyzer._suspend_capture("output", "repeated loopback data discontinuity")
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: key == "v2_music_reactive_background_enabled"
        provider.audio_analyzer = analyzer
        provider._last_data = None

        with patch("api.providers.LiveAudioAnalyzer", side_effect=AssertionError("a suspended endpoint must not reopen")):
            self.assertFalse(provider.has_live_audio())
            self.assertIsNone(provider.get_audio_frame())
            self.assertIs(provider._ensure_audio_analyzer_if_requested(), analyzer)

        provider.settings_getter = lambda key, default=None: False
        self.assertIsNone(provider._ensure_audio_analyzer_if_requested())
        self.assertFalse(analyzer._running)
        self.assertTrue(analyzer._stop_event.is_set())


if __name__ == "__main__":
    unittest.main()
