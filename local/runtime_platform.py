"""OS capabilities shared by startup, settings and the provider boundary.

No Qt or WinRT import is needed to decide whether system media is available.
The Qt 5 facade marks legacy builds, which remain owned-only even on a newer
test host. OS detection uses RtlGetVersion to avoid compatibility-mode lies.
"""
from __future__ import annotations

import ctypes
from functools import lru_cache
import sys


@lru_cache(maxsize=1)
def windows_version():
    if sys.platform != "win32":
        return None
    try:
        class Version(ctypes.Structure):
            _fields_ = [("size", ctypes.c_ulong), ("major", ctypes.c_ulong),
                        ("minor", ctypes.c_ulong), ("build", ctypes.c_ulong),
                        ("platform", ctypes.c_ulong), ("service_pack", ctypes.c_wchar * 128)]
        info = Version()
        info.size = ctypes.sizeof(info)
        query = ctypes.WinDLL("ntdll").RtlGetVersion
        query.argtypes = [ctypes.POINTER(Version)]
        query.restype = ctypes.c_long
        if query(ctypes.byref(info)) == 0:
            return int(info.major), int(info.minor), int(info.build)
    except (AttributeError, OSError):
        pass
    version = sys.getwindowsversion()
    return version.major, version.minor, version.build


def legacy_qt_runtime():
    return bool(getattr(sys.modules.get("PySide6"), "__legacy_qt5__", False))


def owned_playback_only():
    version = windows_version()
    return legacy_qt_runtime() or version is not None and version < (10, 0, 17763)


def normalize_playback_selection(owned, system):
    if owned_playback_only():
        return True, False
    return bool(owned), bool(system)


def system_media_unavailable_reason():
    version = windows_version()
    if version is not None and version[:2] == (6, 1):
        return "Windows 7 仅支持程序内播放，系统媒体抓取不可用。"
    return "此兼容版仅支持程序内播放，系统媒体抓取不可用。"
