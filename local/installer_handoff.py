"""Validate installer-to-app geometry without trusting a monitor index."""
from __future__ import annotations

import re
import sys


def parse_handoff_request(arguments):
    """Accept legacy bounds, with an optional installer-owned event token."""
    token = None
    if len(arguments) == 7 and arguments[5] == "--installer-sync":
        token = validate_handoff_token(arguments[6])
        arguments = arguments[:5]
    return parse_handoff_arguments(arguments), token


def validate_handoff_token(token):
    if not isinstance(token, str) or re.fullmatch(r"[0-9]{1,10}-[0-9]{1,10}", token) is None:
        raise ValueError("Invalid installer synchronization token")
    return token


def handoff_event_name(token, role):
    validate_handoff_token(token)
    if role not in {"ready", "reveal", "painted"}:
        raise ValueError("Invalid installer synchronization event")
    return "Local\\GluSpot.InstallerHandoff." + token + "." + role


class InstallerHandoffEvents:
    """Open existing same-user kernel events; never create an absent installer."""
    def __init__(self, kernel, handles):
        self.kernel = kernel
        self.handles = handles

    @classmethod
    def connect(cls, token):
        validate_handoff_token(token)
        if sys.platform != "win32":
            return None
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        kernel.OpenEventW.restype = wintypes.HANDLE
        kernel.SetEvent.argtypes = (wintypes.HANDLE,)
        kernel.SetEvent.restype = wintypes.BOOL
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        result = cls(kernel, {})
        for role in ("ready", "reveal", "painted"):
            handle = kernel.OpenEventW(0x00100002, False, handoff_event_name(token, role))
            if not handle:
                result.close()
                return None
            result.handles[role] = handle
        return result

    def signal(self, role):
        handle = self.handles.get(role)
        return bool(handle and self.kernel.SetEvent(handle))

    def revealed(self):
        handle = self.handles.get("reveal")
        return bool(handle and self.kernel.WaitForSingleObject(handle, 0) == 0)

    def close(self):
        for handle in self.handles.values():
            self.kernel.CloseHandle(handle)
        self.handles.clear()


def start_synchronized_handoff(application, start, surfaces, events, *, timeout_ms=30000):
    """Keep startup offscreen until arrival, then acknowledge a completed paint.

    Timers keep Qt responsive while the native installer moves. An abandoned
    installer cannot indefinitely hide an otherwise usable application.
    """
    from PySide6.QtCore import QEvent, QObject, QTimer

    class Gate(QObject):
        def __init__(self):
            super().__init__(application)
            self.events = events
            self.started = False
            self.paint_pending = False
            self.surfaces = tuple(surfaces)
            self.poll = QTimer(self)
            self.poll.setInterval(16)
            self.poll.timeout.connect(self.check)
            self.deadline = QTimer(self)
            self.deadline.setSingleShot(True)
            self.deadline.timeout.connect(self.expire)
            for surface in self.surfaces:
                surface.installEventFilter(self)
            application.aboutToQuit.connect(self.close)
            if not self.events.signal("ready"):
                self.expire()
                return
            self.poll.start()
            self.deadline.start(timeout_ms)
            self.check()

        def check(self):
            if self.events is not None and not self.started and self.events.revealed():
                self.begin()

        def begin(self):
            if self.started:
                return
            self.started = True
            self.poll.stop()
            start()

        def eventFilter(self, watched, event):
            if (self.events is not None and self.started and event.type() == QEvent.Type.Paint
                    and watched in self.surfaces and watched.isVisible()
                    and watched.windowOpacity() > 0 and not self.paint_pending):
                self.paint_pending = True
                # The event filter runs before paintEvent; acknowledge only on
                # the following event-loop turn, after Qt returns from painting.
                QTimer.singleShot(0, lambda: self.painted(watched))
            return False

        def painted(self, surface):
            self.paint_pending = False
            # A navigation snapshot can paint a still-visible source and then
            # hide it in the same turn. It is not a displayed destination.
            if self.events is not None and surface.isVisible() and surface.windowOpacity() > 0:
                self.events.signal("painted")
                self.close()

        def expire(self):
            self.begin()
            self.close()

        def close(self):
            self.poll.stop()
            self.deadline.stop()
            for surface in self.surfaces:
                surface.removeEventFilter(self)
            if self.events is not None:
                self.events.close()
                self.events = None

    return Gate()


def parse_handoff_arguments(arguments):
    if len(arguments) != 5 or arguments[0] != "--installer-handoff":
        raise ValueError("Invalid installer handoff")
    try:
        x, y, width, height = (int(part) for part in arguments[1:])
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("Invalid installer bounds") from error
    if not (-100000 <= x <= 100000 and -100000 <= y <= 100000
            and 16 <= width <= 4096 and 8 <= height <= 2048):
        raise ValueError("Installer bounds out of range")
    return x, y, width, height


def logical_handoff_rect(values, screens):
    from PySide6.QtCore import QRect
    from local.mydockfinder import finder_bar_screen_mapping

    x, y, width, height = values
    # Reuse the bar mapper's checked physical-size/origin rules. Native
    # installer finishes at a monitor's top, just like the Finder bar.
    matches = []
    for screen in screens:
        geometry, dpr = screen.geometry(), screen.devicePixelRatio()
        snapshot = {
            "enabled": True,
            "monitor_name": screen.name(),
            "physical_monitor_rect": (geometry.x(), geometry.y(),
                round(geometry.x() + geometry.width()*dpr), round(geometry.y() + geometry.height()*dpr)),
            "physical_rect": (x, y, x + width, y + height),
        }
        mapping = finder_bar_screen_mapping(snapshot, screens)
        if mapping is not None and mapping[0] is screen:
            matches.append(mapping[1])
    if len(matches) != 1:
        return None
    return QRect(*(round(value) for value in matches[0]))
