"""Shared logical/native edge geometry for GluSpot's frameless music pages."""
from __future__ import annotations

import ctypes
import math
import sys
from functools import lru_cache

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from local.music_navigation_native import _MSG


RESIZE_BAND = 7.0  # logical pixels, rounded outwards at the window's current DPR
WM_NCHITTEST = 0x0084
WM_ENTERSIZEMOVE = 0x0231
WM_EXITSIZEMOVE = 0x0232
WM_SIZING = 0x0214
HTCLIENT = 1
HTLEFT, HTRIGHT, HTTOP = 10, 11, 12
HTTOPLEFT, HTTOPRIGHT, HTBOTTOM = 13, 14, 15
HTBOTTOMLEFT, HTBOTTOMRIGHT = 16, 17

_HITS = {
    frozenset(("left",)): HTLEFT,
    frozenset(("right",)): HTRIGHT,
    frozenset(("top",)): HTTOP,
    frozenset(("bottom",)): HTBOTTOM,
    frozenset(("left", "top")): HTTOPLEFT,
    frozenset(("right", "top")): HTTOPRIGHT,
    frozenset(("left", "bottom")): HTBOTTOMLEFT,
    frozenset(("right", "bottom")): HTBOTTOMRIGHT,
}
_SIZING_EDGES = {
    1: {"left"}, 2: {"right"}, 3: {"top"}, 4: {"left", "top"},
    5: {"right", "top"}, 6: {"bottom"}, 7: {"left", "bottom"},
    8: {"right", "bottom"},
}


def resize_edges(x, y, width, height, *, dpr=1.0, physical=False):
    """Use the same seven-DIP band for Qt positions and Win32 physical pixels."""
    dpr = max(.25, float(dpr))
    band = math.ceil(RESIZE_BAND * dpr)
    if not physical:
        x, y, width, height = (float(value) * dpr for value in (x, y, width, height))
    if x < 0 or y < 0 or x >= width or y >= height:
        return set()
    edges = set()
    if x < band:
        edges.add("left")
    elif x >= width - band:
        edges.add("right")
    if y < band:
        edges.add("top")
    elif y >= height - band:
        edges.add("bottom")
    return edges


def resize_cursor(edges):
    if edges == {"left", "top"} or edges == {"right", "bottom"}:
        return Qt.CursorShape.SizeFDiagCursor
    if edges == {"right", "top"} or edges == {"left", "bottom"}:
        return Qt.CursorShape.SizeBDiagCursor
    if edges & {"left", "right"}:
        return Qt.CursorShape.SizeHorCursor
    if edges & {"top", "bottom"}:
        return Qt.CursorShape.SizeVerCursor
    return Qt.CursorShape.ArrowCursor


def native_resize_hit(edges):
    return _HITS.get(frozenset(edges), 0)


class _RECT(ctypes.Structure):
    # Win32 LONG stays 32-bit on all development hosts.
    _fields_ = [(name, ctypes.c_int32) for name in ("left", "top", "right", "bottom")]


@lru_cache(maxsize=1)
def _get_window_rect():
    function = ctypes.WinDLL("user32", use_last_error=True).GetWindowRect
    function.argtypes = (ctypes.c_void_p, ctypes.POINTER(_RECT))
    function.restype = ctypes.c_int
    return function


def constrain_native_rect(rect, edges, minimum, maximum, *, aspect_ratio=None):
    """Clamp a Win32 exclusive RECT while preserving the opposite edge."""
    width = max(minimum[0], min(maximum[0], rect.right - rect.left))
    height = max(minimum[1], min(maximum[1], rect.bottom - rect.top))
    if aspect_ratio is not None and width > round(height * aspect_ratio):
        height = min(maximum[1], max(height, math.ceil(width / aspect_ratio)))
        width = min(width, round(height * aspect_ratio))
    if "left" in edges:
        rect.left = rect.right - width
    else:
        rect.right = rect.left + width
    if "top" in edges:
        rect.top = rect.bottom - height
    else:
        rect.bottom = rect.top + height


class NativeMusicResizeMixin:
    """Let Windows own resize cursors, edge drags and Aero window placement."""

    def _native_resize_enabled(self):
        return not self.isFullScreen() and not self.isMaximized()

    def _native_system_resize_enabled(self):
        return True

    def _native_window_rect(self, hwnd):
        rect = _RECT()
        return rect if _get_window_rect()(hwnd, ctypes.byref(rect)) else None

    def _native_resize_begin(self):
        self._native_resize_active = True
        hold = getattr(self, "_set_resize_chrome_hold", None)
        if callable(hold):
            hold(True)
        if hasattr(self, "_content_layout_user_resizing"):
            self._content_layout_user_resizing = True

    def _native_resize_end(self):
        self._native_resize_active = False
        self._native_pending_resize_edges = set()
        if hasattr(self, "_content_layout_user_resizing"):
            self._content_layout_user_resizing = False
        hold = getattr(self, "_set_resize_chrome_hold", None)
        if callable(hold):
            hold(False)
        end = getattr(self, "_end_page_resize", None)
        if callable(end):
            end()

    def nativeEvent(self, event_type, message):
        if sys.platform == "win32" and bytes(event_type) == b"windows_generic_MSG" and message:
            native = _MSG.from_address(int(message))
            if native.message == WM_EXITSIZEMOVE:
                self._native_resize_end()
            elif self._native_resize_enabled():
                if native.message == WM_NCHITTEST:
                    rect = self._native_window_rect(native.hwnd)
                    if rect is not None:
                        # GET_X/Y_LPARAM sign extension matters on left/top monitors.
                        x = ctypes.c_int16(native.lParam & 0xffff).value - rect.left
                        y = ctypes.c_int16((native.lParam >> 16) & 0xffff).value - rect.top
                        edges = resize_edges(x, y, rect.right - rect.left, rect.bottom - rect.top,
                                             dpr=self.devicePixelRatioF(), physical=True)
                        system_resize = self._native_system_resize_enabled()
                        self._native_pending_resize_edges = edges if system_resize else set()
                        hit = native_resize_hit(edges)
                        if hit:
                            if not system_resize:
                                # Keep the player's existing client-side
                                # overscroll gesture beyond the real minimum.
                                # Qt's WM_SETCURSOR path uses this widget cursor.
                                self.setCursor(resize_cursor(edges))
                                return True, HTCLIENT
                            return True, hit
                elif (native.message == WM_ENTERSIZEMOVE and self._native_system_resize_enabled()
                      and getattr(self, "_native_pending_resize_edges", None)):
                    self._native_resize_begin()
                elif native.message == WM_SIZING and native.lParam and self._native_system_resize_enabled():
                    edges = _SIZING_EDGES.get(native.wParam)
                    if edges:
                        if not getattr(self, "_native_resize_active", False):
                            self._native_resize_begin()
                        dpr = max(.25, self.devicePixelRatioF())
                        available_geometry = getattr(self, "_available_geometry", None)
                        if callable(available_geometry):
                            available = available_geometry().size()
                        else:
                            # Application queries retain the shared QScreen
                            # wrapper when transient login/library pages close.
                            screen = (QGuiApplication.screenAt(self.frameGeometry().center())
                                      or QGuiApplication.primaryScreen())
                            available = screen.availableGeometry().size() if screen is not None else self.maximumSize()
                        minimum = (math.ceil(self.minimumWidth() * dpr), math.ceil(self.minimumHeight() * dpr))
                        maximum = (max(minimum[0], round(available.width() * dpr)),
                                   max(minimum[1], round(available.height() * dpr)))
                        rect = _RECT.from_address(ctypes.c_void_p(native.lParam).value)
                        constrain_native_rect(rect, edges, minimum, maximum,
                                              aspect_ratio=getattr(self, "_native_resize_aspect_ratio", None))
                        return True, 1
        return super().nativeEvent(event_type, message)
