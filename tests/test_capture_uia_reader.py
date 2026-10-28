"""Linux-safe checks for the capture-only, single-mutation UIA entrypoints."""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from api.netease_position import NeteasePositionReader, NeteasePositionSample, QQPositionReader


class FakeRange:
    CurrentMinimum = 0.0
    CurrentMaximum = 100.0
    CurrentValue = 0.0

    def __init__(self, *, read_only=False, after_write=None):
        self.CurrentIsReadOnly = read_only
        self.after_write = after_write
        self.writes = []

    def SetValue(self, value):
        self.writes.append(value)
        self.CurrentValue = value
        if self.after_write is not None:
            self.after_write(value)


class FakeWindow:
    """Semantic UIA wrapper without a native HWND or a Windows dependency."""

    def __init__(self, ranges=(), *, progress=10.0):
        self.caption = "Song - Artist"
        self.ranges = list(ranges)
        self.progress = progress
        self.clicks = []
        self.after_click = None

    def window_text(self):
        return self.caption

    def rectangle(self):
        return SimpleNamespace(left=100, top=100, right=1100, bottom=960)

    def descendants(self, control_type=None):
        if control_type == "Slider":
            return [
                SimpleNamespace(
                    iface_range_value=interface,
                    rectangle=lambda: SimpleNamespace(left=180, top=850, right=1020, bottom=870),
                )
                for interface in self.ranges
            ]
        if control_type == "Button":
            return [SimpleNamespace(
                element_info=SimpleNamespace(automation_id="btn_pc_minibar_play"),
                rectangle=lambda: SimpleNamespace(left=500, top=850, right=540, bottom=890),
            )]
        return []

    def is_visible(self):
        return True

    def is_active(self):
        return True

    def set_focus(self):
        pass

    def click_input(self, coords=None):
        self.clicks.append(coords)
        if self.after_click is not None:
            self.after_click()


class CaptureUiaReaderTests(unittest.TestCase):
    def setUp(self):
        self.reader = NeteasePositionReader()
        self.windows = []
        self.reader._netease_windows = lambda *_args, **_kwargs: self.windows
        self.reader._position_from_progress_windows = self._sample
        fake_pywinauto = SimpleNamespace(Desktop=lambda **_kwargs: object())
        self.modules = patch.dict(sys.modules, {"pywinauto": fake_pywinauto})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.sleep = patch("api.netease_position.time.sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    @staticmethod
    def _sample(windows, duration):
        return NeteasePositionSample(
            windows[0].progress, duration, "netease-uia-progress", 0.92, sampled_at=123.0
        )

    def _seek(self):
        return self.reader.seek_playback_position_once(120.0, 240.0, "Song", "Artist")

    def test_read_returns_fresh_progress_without_cached_sample_or_worker(self):
        self.windows = [FakeWindow(progress=42.0)]
        self.reader._last_sample = NeteasePositionSample(99.0, 240.0, "netease-uia-lyrics", 0.5)
        with patch("api.netease_position.threading.Thread") as thread:
            sample = self.reader.read_progress_position("Song", "Artist", 240.0)
        self.assertEqual(sample.position_seconds, 42.0)
        self.assertEqual(sample.source, "netease-uia-progress")
        thread.assert_not_called()

    def test_read_rejects_untrusted_window_and_changed_caption(self):
        wrong = FakeWindow(progress=99.0)
        wrong.caption = "Another Song - Artist"
        changing = FakeWindow(progress=42.0)
        self.windows = [wrong, changing]

        def changed_sample(windows, duration):
            sample = self._sample(windows, duration)
            changing.caption = "Replacement - Artist"
            return sample

        self.reader._position_from_progress_windows = changed_sample
        self.assertIsNone(self.reader.read_progress_position("Song", "Artist", 240.0))

    def test_read_failure_cannot_return_a_previous_progress_sample(self):
        self.windows = [FakeWindow()]
        self.reader._last_sample = NeteasePositionSample(99.0, 240.0, "netease-uia-progress", 0.92)
        self.reader._position_from_progress_windows = Mock(side_effect=RuntimeError("element disappeared"))
        self.assertIsNone(self.reader.read_progress_position("Song", "Artist", 240.0))

    def test_invalid_inputs_do_not_resolve_windows(self):
        self.reader._capture_progress_windows = Mock(side_effect=AssertionError("unexpected UIA"))
        for position, duration in ((float("nan"), 240), (120, float("inf")), (120, 0)):
            with self.subTest(position=position, duration=duration):
                self.assertFalse(self.reader.seek_playback_position_once(position, duration, "Song", "Artist"))
        self.assertIsNone(self.reader.read_progress_position("Song", "Artist", float("nan")))
        self.reader._capture_progress_windows.assert_not_called()

    def test_writable_range_wins_over_other_controls_and_windows(self):
        first = FakeRange()
        other_control = FakeRange()
        other_window_range = FakeRange()
        window = FakeWindow([FakeRange(read_only=True), first, other_control])
        first.after_write = lambda value: setattr(window, "progress", value * 2.4)
        self.windows = [window, FakeWindow([other_window_range])]
        self.assertTrue(self._seek())
        self.assertEqual(first.writes, [50.0])
        self.assertEqual(other_control.writes, [])
        self.assertEqual(other_window_range.writes, [])
        self.assertTrue(self.reader.last_capture_seek_attempted)
        self.assertEqual(window.clicks, [])

    def test_range_fake_ack_never_falls_back_to_another_write_or_click(self):
        first, other = FakeRange(), FakeRange()
        self.windows = [FakeWindow([first, other]), FakeWindow([FakeRange()])]
        self.assertFalse(self._seek())
        self.assertEqual(first.CurrentValue, 50.0)
        self.assertEqual(first.writes, [50.0])
        self.assertEqual(other.writes, [])
        self.assertEqual(self.windows[1].ranges[0].writes, [])
        self.assertTrue(self.reader.last_capture_seek_attempted)
        self.assertTrue(all(not window.clicks for window in self.windows))

    def test_exception_after_range_write_never_retries(self):
        def fail_after_write(_value):
            raise RuntimeError("COM reply lost after mutation")

        first, other = FakeRange(after_write=fail_after_write), FakeRange()
        self.windows = [FakeWindow([first, other]), FakeWindow([FakeRange()])]
        self.assertFalse(self._seek())
        self.assertEqual(first.writes, [50.0])
        self.assertEqual(other.writes, [])
        self.assertTrue(self.reader.last_capture_seek_attempted)
        self.assertTrue(all(not window.clicks for window in self.windows))

    def test_ambiguous_range_access_error_fails_closed_without_click(self):
        class DeniedRange(FakeRange):
            @property
            def CurrentMaximum(self):
                raise PermissionError("UIPI access denied")

        denied = DeniedRange()
        self.windows = [FakeWindow([denied, FakeRange()])]
        self.assertFalse(self._seek())
        self.assertFalse(self.reader.last_capture_seek_attempted)
        self.assertEqual(self.windows[0].clicks, [])

    def test_read_only_range_uses_one_coordinate_fallback(self):
        readonly = FakeRange(read_only=True)
        window = FakeWindow([readonly])
        window.after_click = lambda: setattr(window, "progress", 120.0)
        self.windows = [window, FakeWindow()]
        self.assertTrue(self._seek())
        self.assertEqual(readonly.writes, [])
        self.assertEqual(len(window.clicks), 1)
        self.assertEqual(self.windows[1].clicks, [])
        self.assertTrue(self.reader.last_capture_seek_attempted)

    def test_coordinate_fake_ack_never_clicks_another_window(self):
        self.windows = [FakeWindow(), FakeWindow()]
        self.assertFalse(self._seek())
        self.assertEqual(len(self.windows[0].clicks), 1)
        self.assertEqual(self.windows[1].clicks, [])

    def test_exception_after_coordinate_write_never_retries(self):
        def fail_after_click():
            raise RuntimeError("input response lost")

        self.windows = [FakeWindow(), FakeWindow()]
        self.windows[0].after_click = fail_after_click
        self.assertFalse(self._seek())
        self.assertEqual(len(self.windows[0].clicks), 1)
        self.assertEqual(self.windows[1].clicks, [])
        self.assertTrue(self.reader.last_capture_seek_attempted)

    def test_qq_does_not_use_netease_coordinate_geometry(self):
        self.reader = QQPositionReader()
        self.reader._netease_windows = lambda *_args, **_kwargs: self.windows
        self.windows = [FakeWindow([FakeRange(read_only=True)])]
        self.assertFalse(self._seek())
        self.assertFalse(self.reader.last_capture_seek_attempted)
        self.assertEqual(self.windows[0].clicks, [])


if __name__ == "__main__":
    unittest.main()
