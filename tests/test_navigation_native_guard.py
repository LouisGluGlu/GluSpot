"""Native geometry ownership uses allocated buffers, never visible windows."""
from __future__ import annotations

import ctypes
import sys
import unittest
from unittest.mock import patch

from PySide6.QtCore import QByteArray

from local.music_navigation_native import (
    NativeNavigationGeometryGuard, SWP_FRAMECHANGED, SWP_NOMOVE, SWP_NOSIZE,
    WM_DISPLAYCHANGE, WM_DPICHANGED, WM_NCDESTROY, WM_WINDOWPOSCHANGING,
    _MSG, _WINDOWPOS,
)


_WINDOWS_HOST = sys.platform == "win32"


class _Application:
    def __init__(self):
        self.installed = []
        self.removed = []

    def installNativeEventFilter(self, value):
        self.installed.append(value)

    def removeNativeEventFilter(self, value):
        self.removed.append(value)


def _buffers(hwnd=0x1234, *, flags=0x0054, message=WM_WINDOWPOSCHANGING):
    position = _WINDOWPOS(hwnd, 0x5678, -830, 121, 960, 540, flags)
    native = _MSG(hwnd, message, 0, ctypes.addressof(position), 0, (0, 0), 0)
    return native, position


class NativeNavigationGeometryGuardTests(unittest.TestCase):
    def setUp(self):
        self.platform = patch("local.music_navigation_native.sys.platform", "win32")
        self.platform.start()
        self.addCleanup(self.platform.stop)
        self.application = _Application()
        self.guard = NativeNavigationGeometryGuard(self.application)
        self.addCleanup(self.guard.close)

    def send(self, native, event_type=b"windows_generic_MSG"):
        return self.guard.nativeEventFilter(event_type, ctypes.addressof(native))

    def test_winuser_layout_uses_32_bit_fields_and_native_pointers(self):
        pointer = ctypes.sizeof(ctypes.c_void_p)
        self.assertEqual(_WINDOWPOS.hwndInsertAfter.offset, pointer)
        self.assertEqual(_WINDOWPOS.x.offset, pointer * 2)
        self.assertEqual(_WINDOWPOS.flags.offset, pointer * 2 + 16)
        self.assertEqual(ctypes.sizeof(_WINDOWPOS), 40 if pointer == 8 else 28)
        self.assertEqual(_MSG.message.offset, pointer)
        self.assertEqual(_MSG.wParam.offset, 16 if pointer == 8 else 8)
        self.assertEqual(_MSG.lParam.offset, 24 if pointer == 8 else 12)
        self.assertEqual(_MSG.lPrivate.offset, 44 if pointer == 8 else 28)
        self.assertEqual(ctypes.sizeof(_MSG), 48 if pointer == 8 else 32)
        # Compare the actual Windows Python ABI too; its MSG omits the private
        # DWORD, which is trailing padding on x64, but preceding offsets agree.
        if _WINDOWS_HOST:
            from ctypes import wintypes
            for name in ("message", "wParam", "lParam", "time", "pt"):
                self.assertEqual(getattr(_MSG, name).offset, getattr(wintypes.MSG, name).offset)

    def test_only_move_and_size_flags_change_for_owned_hwnd(self):
        self.assertTrue(self.guard.register(0x1234))
        native, position = _buffers()
        old_message = bytes(native)
        old = (position.hwnd, position.hwndInsertAfter, position.x, position.y,
               position.cx, position.cy, position.flags)
        self.assertEqual(self.send(native, QByteArray(b"windows_generic_MSG")), (False, 0))
        self.assertEqual(bytes(native), old_message)
        self.assertEqual((position.hwnd, position.hwndInsertAfter, position.x,
                          position.y, position.cx, position.cy), old[:6])
        self.assertEqual(position.flags, old[6] | SWP_NOMOVE | SWP_NOSIZE)

    def test_other_hwnds_and_nonwindows_messages_are_untouched(self):
        self.guard.register(0x1234)
        for hwnd, event_type in ((0x5678, b"windows_generic_MSG"),
                                 (0x1234, b"windows_dispatcher_MSG"),
                                 (0x1234, b"xcb_generic_event_t")):
            with self.subTest(hwnd=hwnd, event_type=event_type):
                native, position = _buffers(hwnd)
                old = bytes(position)
                self.assertEqual(self.send(native, event_type), (False, 0))
                self.assertEqual(bytes(position), old)

    def test_mismatched_windowpos_handle_and_other_messages_are_untouched(self):
        self.guard.register(0x1234)
        native, position = _buffers()
        position.hwnd = 0x5678
        old = bytes(position)
        self.send(native)
        self.assertEqual(bytes(position), old)
        native.message = 0x000F  # WM_PAINT: lParam need not point to WINDOWPOS.
        native.lParam = 1
        self.assertEqual(self.send(native), (False, 0))

    def test_missing_message_window_or_windowpos_are_untouched(self):
        self.guard.register(0x1234)
        self.assertEqual(self.guard.nativeEventFilter(b"windows_generic_MSG", 0), (False, 0))
        native, position = _buffers()
        native.lParam = 0
        self.assertEqual(self.send(native), (False, 0))
        native.hwnd = None
        self.assertEqual(self.send(native), (False, 0))

    def test_transition_corrections_bypass_only_while_flag_is_true(self):
        applying = [True]
        self.guard.register(0x1234, bypass=lambda: applying[0])
        native, position = _buffers()
        flags = position.flags
        self.send(native)
        self.assertEqual(position.flags, flags)
        applying[0] = False
        self.send(native)
        self.assertEqual(position.flags, flags | SWP_NOMOVE | SWP_NOSIZE)

    def test_frame_change_bypasses_one_message_and_keeps_lease(self):
        self.guard.register(0x1234)
        native, position = _buffers(flags=SWP_FRAMECHANGED | 0x0040)
        self.send(native)
        self.assertEqual(position.flags, SWP_FRAMECHANGED | 0x0040)
        self.assertIn(0x1234, self.guard._owners)
        position.flags = 0x0040
        self.send(native)
        self.assertEqual(position.flags, 0x0040 | SWP_NOMOVE | SWP_NOSIZE)

    def test_dpi_display_destroy_invalidate_before_qt_handling(self):
        for message in (WM_DPICHANGED, WM_DISPLAYCHANGE, WM_NCDESTROY):
            with self.subTest(message=message):
                self.guard.register(0x1234)
                native, position = _buffers(message=message)
                old = bytes(position)
                self.assertEqual(self.send(native), (False, 0))
                self.assertEqual(bytes(position), old)
                self.assertNotIn(0x1234, self.guard._owners)
                # No Qt install/remove call is made from a native callback.
                self.assertEqual(len(self.application.installed), len(self.application.removed) + 1)
                native.message = WM_WINDOWPOSCHANGING
                self.send(native)
                self.assertEqual(bytes(position), old)
                # Owner was already removed, but normal lifecycle cleanup
                # still removes the process-local filter exactly once.
                self.guard.unregister(0x1234)
                self.assertFalse(self.guard._installed)
                self.assertEqual(len(self.application.installed), len(self.application.removed))

    def test_invalidation_of_one_owner_does_not_release_another(self):
        self.guard.register(0x1234)
        self.guard.register(0x5678)
        native, _ = _buffers(message=WM_DISPLAYCHANGE)
        self.send(native)
        self.assertEqual(set(self.guard._owners), {0x5678})
        self.guard.unregister(0x1234)
        self.assertTrue(self.guard._installed)
        self.guard.unregister(0x5678)
        self.assertFalse(self.guard._installed)

    def test_dead_bypass_owner_is_released_without_raising_into_qt(self):
        def dead_owner():
            raise RuntimeError("fixture owner is gone")
        self.guard.register(0x1234, bypass=dead_owner)
        native, position = _buffers()
        old = bytes(position)
        self.assertEqual(self.send(native), (False, 0))
        self.assertEqual(bytes(position), old)
        self.assertFalse(self.guard._owners)
        self.guard.unregister(0x1234)
        self.assertEqual(self.application.removed, [self.guard])

    def test_install_and_remove_are_idempotent_across_replacement_and_reuse(self):
        self.guard.register(0x1234)
        self.guard.register(0x1234, bypass=lambda: False)
        self.guard.register(0x5678)
        self.assertEqual(self.application.installed, [self.guard])
        self.guard.unregister(0x1234)
        self.assertEqual(self.application.removed, [])
        self.guard.unregister(0x5678)
        self.guard.unregister(0x5678)
        self.guard.close()
        self.assertEqual(self.application.removed, [self.guard])
        self.guard.register(0x1234)
        self.guard.close()
        self.assertEqual(self.application.installed, [self.guard, self.guard])
        self.assertEqual(self.application.removed, [self.guard, self.guard])

    def test_nonwindows_and_invalid_handles_never_install(self):
        with patch("local.music_navigation_native.sys.platform", "darwin"):
            self.assertFalse(self.guard.register(0x1234))
        for hwnd in (0, -1, None, "invalid", 1 << (ctypes.sizeof(ctypes.c_void_p) * 8)):
            self.assertFalse(self.guard.register(hwnd))
        self.assertFalse(self.guard._owners)
        self.assertEqual(self.application.installed, [])

    def test_unavailable_application_leaves_no_owner_or_filter(self):
        with patch.object(self.application, "installNativeEventFilter", side_effect=RuntimeError("gone")):
            self.assertFalse(self.guard.register(0x1234))
        self.assertFalse(self.guard._installed)
        self.assertFalse(self.guard._owners)

    def test_no_application_is_created_to_acquire_a_lease(self):
        guard = NativeNavigationGeometryGuard()
        with patch("local.music_navigation_native.QCoreApplication.instance", return_value=None):
            self.assertFalse(guard.register(0x1234))
        self.assertFalse(guard._installed)
        self.assertFalse(guard._owners)
        guard.close()


if __name__ == "__main__":
    unittest.main()
