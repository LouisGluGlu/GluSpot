"""Native scene evidence uses physical bounds without constructing QApplication."""
from __future__ import annotations

import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from local.island_stack_visual import IslandStackVisual


class _NativeCall:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.callback(*args)


class _NativeSceneApi:
    def __init__(self, window=(0, 0, 1920, 1080), monitor=(0, 0, 1920, 1080)):
        self.window, self.monitor = window, monitor
        self.current_context = 777
        self.context_changes = []
        self.enter_allowed = True
        self.rect_available = True
        self.monitor_available = True
        self.info_available = True
        self.info_error = None
        self.foreground = 333
        self.zoomed = False
        self.GetForegroundWindow = _NativeCall(lambda: self.foreground)
        self.GetWindowRect = _NativeCall(self._window_rect)
        self.MonitorFromWindow = _NativeCall(lambda _hwnd, _flags: 55 if self.monitor_available else None)
        self.GetMonitorInfoW = _NativeCall(self._monitor_info)
        self.SetThreadDpiAwarenessContext = _NativeCall(self._set_context)
        self.IsZoomed = _NativeCall(lambda _hwnd: self.zoomed)

    def _window_rect(self, _hwnd, pointer):
        if not self.rect_available:
            return False
        rect = pointer._obj
        rect.left, rect.top, rect.right, rect.bottom = self.window
        return True

    def _monitor_info(self, _monitor, pointer):
        if self.info_error is not None:
            raise self.info_error
        if not self.info_available:
            return False
        rect = pointer._obj.rcMonitor
        rect.left, rect.top, rect.right, rect.bottom = self.monitor
        return True

    def _set_context(self, value):
        raw = value.value if isinstance(value, ctypes.c_void_p) else value
        requested = ctypes.c_ssize_t(raw).value
        self.context_changes.append(requested)
        if requested == -4 and not self.enter_allowed:
            return None
        previous = self.current_context
        self.current_context = requested
        return previous


class NativeFullscreenSceneTests(unittest.TestCase):
    def state(self, api):
        return IslandStackVisual._native_fullscreen_state_for_window(333, api)

    def test_physical_monitor_bounds_identify_mixed_dpi_negative_origin_fullscreen(self):
        for dpr in (1., 1.5, 2.):
            with self.subTest(dpr=dpr):
                bounds = (-2880, -1800, -2880 + round(1920*dpr), -1800 + round(1080*dpr))
                api = _NativeSceneApi(window=bounds, monitor=bounds)
                self.assertIs(self.state(api), True)
                self.assertEqual(api.current_context, 777)
                self.assertEqual(api.context_changes, [-4, 777])
                self.assertEqual(api.MonitorFromWindow.calls, [(333, 2)])

    def test_logical_screen_sized_window_is_not_native_fullscreen_on_scaled_monitor(self):
        api = _NativeSceneApi(window=(-2880, -1800, -960, -720), monitor=(-2880, -1800, 960, 360))
        self.assertIs(self.state(api), False)
        self.assertEqual(api.current_context, 777)

    def test_tolerance_compares_all_four_native_edges(self):
        for window, expected in (((-4, -4, 1924, 1084), True),
                                 ((0, 0, 2000, 1080), False), ((-5, 0, 1920, 1080), False)):
            with self.subTest(window=window):
                self.assertIs(self.state(_NativeSceneApi(window=window)), expected)

    def test_failed_native_reads_restore_dpi_context_and_return_unknown(self):
        for setting, value in (("rect_available", False), ("monitor_available", False),
                               ("info_available", False), ("info_error", OSError("monitor changed"))):
            with self.subTest(setting=setting):
                api = _NativeSceneApi()
                setattr(api, setting, value)
                self.assertIsNone(self.state(api))
                self.assertEqual(api.current_context, 777)
                self.assertEqual(api.context_changes, [-4, 777])

    def test_dpi_context_failure_does_not_read_virtualized_bounds(self):
        api = _NativeSceneApi()
        api.enter_allowed = False
        self.assertIsNone(self.state(api))
        self.assertEqual(api.current_context, 777)
        self.assertEqual(api.context_changes, [-4])
        self.assertFalse(api.GetWindowRect.calls)
        self.assertFalse(api.MonitorFromWindow.calls)

    def test_missing_native_api_and_invalid_rectangles_return_unknown(self):
        self.assertIsNone(self.state(SimpleNamespace()))
        for window, monitor in (((0, 0, 0, 1080), (0, 0, 1920, 1080)),
                                ((0, 0, 1920, 1080), (0, 0, 1920, 0))):
            with self.subTest(window=window, monitor=monitor):
                api = _NativeSceneApi(window, monitor)
                self.assertIsNone(self.state(api))
                self.assertEqual(api.context_changes, [-4, 777])

    def instance(self):
        return SimpleNamespace(
            winId=lambda: 111, music_fullscreen_overlay=None,
            _window_title=lambda *_args: "External application",
            _window_class=lambda *_args: "NormalWindow",
            _window_process_name=lambda *_args: "external.exe",
            _is_shell_desktop_window=lambda *_args: False,
            _native_fullscreen_state_for_window=IslandStackVisual._native_fullscreen_state_for_window,
        )

    def screen(self):
        geo = SimpleNamespace(width=lambda: 1920, height=lambda: 1080, left=lambda: 0, top=lambda: 0)
        work = SimpleNamespace(width=lambda: 1880, height=lambda: 1040, left=lambda: 20, top=lambda: 20)
        return SimpleNamespace(geometry=lambda: geo, availableGeometry=lambda: work)

    def detect(self, api, instance=None, screen=None):
        with patch("local.island_stack_visual.sys.platform", "win32"), \
                patch("local.island_stack_visual.ctypes.windll", SimpleNamespace(user32=api), create=True), \
                patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=screen), \
                patch("local.island_stack_visual.QGuiApplication.primaryScreen", return_value=screen):
            return IslandStackVisual._detect_external_scene_suppression(instance or self.instance())

    def test_native_fullscreen_does_not_need_a_matching_qt_screen(self):
        api = _NativeSceneApi(window=(-2880, -1800, 960, 360), monitor=(-2880, -1800, 960, 360))
        with patch("local.island_stack_visual.QGuiApplication.screenAt", side_effect=AssertionError("native evidence suffices")), \
                patch("local.island_stack_visual.sys.platform", "win32"), \
                patch("local.island_stack_visual.ctypes.windll", SimpleNamespace(user32=api), create=True):
            self.assertEqual(IslandStackVisual._detect_external_scene_suppression(self.instance()), (True, "fullscreen"))
        self.assertEqual(api.current_context, 777)

    def test_trusted_native_false_cannot_be_overridden_by_logical_qt_match(self):
        api = _NativeSceneApi(window=(0, 0, 1920, 1080), monitor=(0, 0, 3840, 2160))
        self.assertEqual(self.detect(api, screen=self.screen()), (False, ""))

    def test_native_api_failure_retains_existing_qt_fullscreen_fallback(self):
        api = _NativeSceneApi()
        api.info_available = False
        self.assertEqual(self.detect(api, screen=self.screen()), (True, "fullscreen"))
        self.assertEqual(api.current_context, 777)

    def test_native_false_keeps_existing_maximized_classification(self):
        api = _NativeSceneApi(window=(0, 0, 1920, 1080), monitor=(0, 0, 3840, 2160))
        api.zoomed = True
        self.assertEqual(self.detect(api, screen=self.screen()), (True, "maximized"))

    def test_own_island_song_page_and_shell_are_excluded_before_native_fullscreen_probe(self):
        for owner in ("island", "song", "shell"):
            with self.subTest(owner=owner):
                api, instance = _NativeSceneApi(), self.instance()
                probe = Mock(side_effect=AssertionError("owned/shell surface must be excluded"))
                instance._native_fullscreen_state_for_window = probe
                if owner == "island":
                    api.foreground = 111
                elif owner == "song":
                    instance.music_fullscreen_overlay = SimpleNamespace(isVisible=lambda: True, winId=lambda: 333)
                else:
                    instance._is_shell_desktop_window = lambda *_args: True
                self.assertEqual(self.detect(api, instance), (False, ""))
                probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
