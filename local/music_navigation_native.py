"""Limit late native moves to the destination owned by a page transition.

This is a Qt event filter inside this process, not a Windows hook. It neither
converts coordinates nor calls QWidget/QWindow methods from a native callback.
The transition registers its existing HWND for its bounded placement lease and
unregisters it before handing placement back to ordinary user interaction.
"""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication


WM_WINDOWPOSCHANGING = 0x0046
WM_DISPLAYCHANGE = 0x007E
WM_NCDESTROY = 0x0082
WM_DPICHANGED = 0x02E0
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_FRAMECHANGED = 0x0020


# The fixed-width fields follow WinUser.h rather than host c_long, which is not
# a Windows LONG on every development platform. Pointer fields remain native
# width. Definitions: learn.microsoft.com/windows/win32/api/winuser/
# ns-winuser-msg and ns-winuser-windowpos. lPrivate occupies MSG's final DWORD.
class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint32),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint32),
        ("pt", _POINT),
        ("lPrivate", ctypes.c_uint32),
    ]


class _WINDOWPOS(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("hwndInsertAfter", ctypes.c_void_p),
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
        ("cx", ctypes.c_int32),
        ("cy", ctypes.c_int32),
        ("flags", ctypes.c_uint32),
    ]


class NativeNavigationGeometryGuard(QAbstractNativeEventFilter):
    """Preserve already corrected native placement during a bounded handoff.

    All methods are called on Qt's GUI thread. ``bypass`` must only read the
    transition's geometry-application flag; it must not call Qt or change state.
    Its true value allows the transition's own deliberate geometry correction.
    The application is injectable for buffer-only tests; no app is created here.
    """

    def __init__(self, application=None):
        super().__init__()
        self._application = application
        self._owners = {}
        self._installed = False

    def register(self, hwnd, *, bypass=None):
        if sys.platform != "win32":
            return False
        try:
            hwnd = int(hwnd)
        except (TypeError, ValueError, OverflowError):
            return False
        if hwnd <= 0 or hwnd > ctypes.c_size_t(-1).value:
            return False
        if bypass is not None and not callable(bypass):
            raise TypeError("bypass must be a flag-reading callable")
        if not self._installed:
            application = self._application or QCoreApplication.instance()
            if application is None:
                return False
            try:
                application.installNativeEventFilter(self)
            except RuntimeError:
                return False
            self._application = application
            self._installed = True
        self._owners[hwnd] = bypass
        return True

    def unregister(self, hwnd):
        self._owners.pop(int(hwnd), None)
        if not self._owners:
            self._uninstall()

    def close(self):
        self._owners.clear()
        self._uninstall()

    def _uninstall(self):
        if self._installed:
            self._installed = False
            try:
                self._application.removeNativeEventFilter(self)
            except RuntimeError:
                # Qt may already have destroyed its application at shutdown.
                pass

    def nativeEventFilter(self, eventType, message):
        # Qt documents MSG* only for these Windows events. We deliberately
        # leave dispatcher/system-wide messages and every unowned HWND alone.
        if not self._owners or bytes(eventType) != b"windows_generic_MSG" or not message:
            return False, 0
        native = _MSG.from_address(int(message))
        hwnd = native.hwnd
        if hwnd not in self._owners:
            return False, 0
        if native.message in (WM_DPICHANGED, WM_DISPLAYCHANGE, WM_NCDESTROY):
            # DPI/display messages precede Qt's screen notifications. Release
            # before Qt applies their new rectangle; don't call Qt from here.
            # The transition's normal cleanup still uninstalls the filter even
            # when this native invalidation has already removed the owner.
            self._owners.pop(hwnd, None)
            return False, 0
        if native.message != WM_WINDOWPOSCHANGING or not native.lParam:
            return False, 0
        position = _WINDOWPOS.from_address(ctypes.c_void_p(native.lParam).value)
        if position.hwnd != hwnd or position.flags & SWP_FRAMECHANGED:
            # Native style/frame work (including alpha restoration) may need
            # to proceed. This bypass is local to this message, not the lease.
            return False, 0
        bypass = self._owners[hwnd]
        if bypass is not None:
            try:
                if bypass():
                    return False, 0
            except Exception:
                # A dead owner cannot authorize continued placement control.
                self._owners.pop(hwnd, None)
                return False, 0
        # WM_WINDOWPOSCHANGING explicitly permits flags to prevent a proposed
        # move/resize. Preserve Z order, activation, visibility and all other
        # flags. Keeping existing native placement needs no DPI conversion.
        position.flags |= SWP_NOMOVE | SWP_NOSIZE
        # Continue normal Qt/DefWindowProc processing with the amended flags.
        # PySide's native-event binding expects a (handled, LRESULT) tuple.
        return False, 0
