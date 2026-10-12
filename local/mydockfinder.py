from __future__ import annotations

import ctypes
import math
import os
import sys
from io import StringIO
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage


MYDOCKFINDER_ROOT = Path(r"B:\SteamLibrary\steamapps\common\MyDockFinder")
MYDOCKFINDER_PROCESS_NAMES = {
    "mydockfinder.exe",
    "myfinder.exe",
    "mydock.exe",
    "dock.exe",
    "dock_64.exe",
    "dockmod.exe",
    "dockmod64.exe",
}
MYDOCKFINDER_WINDOW_TOKENS = ("mydockfinder", "myfinder", "mydock")
FINDER_BAR_WINDOW_CLASS = "MyFinderApp"


def _normalized_runtime_path(value: str) -> str:
    return str(value or "").strip().strip('"').replace("/", "\\").casefold()


def _rect_edges(value) -> tuple[float, float, float, float] | None:
    try:
        if len(value) != 4:
            return None
        result = tuple(float(part) for part in value)
        if not all(math.isfinite(part) for part in result):
            return None
        if result[2] <= result[0] or result[3] <= result[1]:
            return None
        return result
    except (TypeError, ValueError, OverflowError):
        return None


def _read_finder_enabled_for_runtime(process_path: str) -> bool | None:
    """The installed runtime's explicit flag supplements, never creates, a bar."""
    if not str(process_path or "").strip():
        return None
    try:
        runtime = Path(str(process_path).strip().strip('"'))
        config_file = runtime.parent / "config.ini"
        if not config_file.is_file() or config_file.stat().st_size > 1024 * 1024:
            return None
        parser = ConfigParser(interpolation=None)
        parser.read_file(StringIO(_read_text(config_file)))
        value = parser.get("dockfinder", "myfinder", fallback="").strip()
        return True if value == "1" else False if value == "0" else None
    except (OSError, ValueError, KeyError, UnicodeError):
        return None
    except Exception:
        # Third-party configuration may be incomplete while its owner saves it.
        return None


def _finder_bar_near_monitor_top(rect, monitor_rect) -> bool:
    bar = _rect_edges(rect)
    monitor = _rect_edges(monitor_rect)
    if bar is None or monitor is None:
        return False
    left, top, right, bottom = bar
    ml, mt, mr, _mb = monitor
    height = bottom - top
    # A real auto-hidden top bar may be just above its monitor. Management and
    # capture windows at -32000, or an unrelated lower window, cannot qualify.
    return (
        right > ml and left < mr
        and right - left >= height
        and top <= mt + height
        and bottom >= mt - 1.0
    )


def _finder_bar_identity(snapshot) -> tuple[int, int, str] | None:
    if not isinstance(snapshot, dict):
        return None
    try:
        hwnd, pid = int(snapshot.get("hwnd", 0)), int(snapshot.get("pid", 0))
    except (TypeError, ValueError, OverflowError):
        return None
    return (hwnd, pid, _normalized_runtime_path(snapshot.get("process_path", ""))) if hwnd > 0 and pid > 0 else None


def _finder_bar_presentation_snapshot(row, config_enabled, previous=None) -> dict[str, object] | None:
    identity = _finder_bar_identity(row)
    if identity is None or not str(row.get("monitor_name", "")):
        return None
    prior = previous if _finder_bar_identity(previous) == identity else None
    if (
        prior and not _finder_bar_near_monitor_top(row.get("physical_rect"), row.get("physical_monitor_rect"))
        and _finder_bar_near_monitor_top(row.get("physical_rect"), prior.get("physical_monitor_rect"))
    ):
        # On vertically stacked monitors an auto-hidden lower-screen bar can
        # physically enter the upper screen; nearest-monitor lookup alone then
        # loses its owner. Preserve only this same HWND/process's known owner.
        row = {**row, "monitor_name": prior.get("monitor_name", ""), "physical_monitor_rect": prior.get("physical_monitor_rect")}
    if not _finder_bar_near_monitor_top(row.get("physical_rect"), row.get("physical_monitor_rect")):
        return None
    bar = _rect_edges(row["physical_rect"])
    monitor = _rect_edges(row["physical_monitor_rect"])
    resting = _rect_edges(prior.get("physical_resting_rect")) if prior else None
    if prior and str(prior.get("monitor_name", "")).casefold() != str(row["monitor_name"]).casefold():
        resting = None
    full_height = resting[3] - resting[1] if resting else 0.0
    height = bar[3] - bar[1]
    cloaked = bool(row.get("cloaked"))
    minimized = bool(row.get("minimized"))
    full_shape = height > 2.0 and height >= full_height - 1.0
    at_top = monitor[1] - 1.0 <= bar[1] <= monitor[1] + 1.0
    visible = bool(row.get("visible")) and full_shape and at_top and not cloaked and not minimized
    if full_shape and not cloaked and not minimized:
        resting = (bar[0], monitor[1], bar[2], monitor[1] + height)
    elif resting:
        resting = (bar[0], monitor[1], bar[2], monitor[1] + full_height)
    enabled = (
        not cloaked and not minimized and config_enabled is not False
        and (config_enabled is True or visible or bool(prior and prior.get("enabled")))
    )
    snapshot = dict(row)
    snapshot.update(class_name=FINDER_BAR_WINDOW_CLASS, enabled=enabled, visible=visible and enabled, config_enabled=config_enabled)
    if resting is not None:
        snapshot["physical_resting_rect"] = list(resting)
    return snapshot


def read_live_finder_bars(
    *,
    matched_name: str,
    matched_path: str = "",
    previous_bars=(),
) -> list[dict[str, object]]:
    """Read actual Finder HWNDs owned by the app listener's trusted runtime.

    All coordinates remain native physical pixels. No QApplication, screenshot,
    installed-file presence check, or external setting mutation is involved.
    Hidden bars need an explicit enabled flag or the same previously visible
    HWND/process identity; an installed config alone can never create a bar.
    """
    name = str(matched_name or "").strip().casefold()
    path = _normalized_runtime_path(matched_path)
    if not name:
        return []
    previous = {_finder_bar_identity(item): item for item in (previous_bars or ()) if _finder_bar_identity(item) is not None}
    bars: list[dict[str, object]] = []
    for row in _native_finder_bar_window_snapshots(name, path):
        runtime_name = str(row.get("process_name", "")).casefold()
        runtime_path = _normalized_runtime_path(row.get("process_path", ""))
        if runtime_name != name or (path and runtime_path != path):
            continue
        if str(row.get("class_name", "")).casefold() != FINDER_BAR_WINDOW_CLASS.casefold():
            continue
        config_enabled = _read_finder_enabled_for_runtime(str(row.get("process_path", "")))
        snapshot = _finder_bar_presentation_snapshot(row, config_enabled, previous.get(_finder_bar_identity(row)))
        if snapshot is not None:
            bars.append(snapshot)
    return bars


def refresh_live_finder_bar(snapshot: dict[str, object]) -> dict[str, object] | None:
    """Refresh a trusted cached HWND without enumerating processes or files."""
    identity = _finder_bar_identity(snapshot)
    if identity is None or not bool(snapshot.get("enabled")):
        return None
    rows = _native_finder_bar_window_snapshots(
        str(snapshot.get("process_name", "")).casefold(), identity[2], _cached_snapshot=snapshot,
    )
    if not rows:
        return None
    return _finder_bar_presentation_snapshot(rows[0], snapshot.get("config_enabled"), snapshot)


def finder_bar_logical_rect(
    snapshot: dict[str, object],
    *,
    screen_name: str,
    screen_rect,
    device_pixel_ratio: float,
    allow_geometry_fallback: bool = False,
) -> tuple[float, float, float, float] | None:
    """Map physical bar bounds into the matching QScreen's logical geometry.

    Windows/Qt retain monitor origins while scaling sizes. Dividing the whole
    global rectangle would move negative-origin and mixed-DPI displays.
    """
    if not isinstance(snapshot, dict) or not bool(snapshot.get("enabled")):
        return None
    native_name = str(snapshot.get("monitor_name", "")).casefold()
    qt_name = str(screen_name or "").casefold()
    if not native_name or (native_name != qt_name and not allow_geometry_fallback):
        return None
    bar = _rect_edges(snapshot.get("physical_resting_rect", snapshot.get("physical_rect")))
    monitor = _rect_edges(snapshot.get("physical_monitor_rect"))
    try:
        sx, sy, sw, sh = (float(part) for part in screen_rect)
        dpr = float(device_pixel_ratio)
    except (TypeError, ValueError, OverflowError):
        return None
    if bar is None or monitor is None or not all(math.isfinite(part) for part in (sx, sy, sw, sh, dpr)):
        return None
    if sw <= 0.0 or sh <= 0.0 or dpr <= 0.0 or not _finder_bar_near_monitor_top(bar, monitor):
        return None
    # QScreen::name() may be a friendly monitor name in Qt 6.12. Even a
    # matching native-looking name is not enough after display reconfiguration:
    # verify the preserved origin and physical size of this exact screen.
    expected_monitor = (sx, sy, sx + sw * dpr, sy + sh * dpr)
    if any(abs(actual - expected) > 2.0 for actual, expected in zip(monitor, expected_monitor)):
        return None
    # Limit horizontal bounds to this monitor; the un-clipped vertical position
    # is needed to follow the Finder owner's real auto-hide motion.
    left, right = max(bar[0], monitor[0]), min(bar[2], monitor[2])
    return (
        sx + (left - monitor[0]) / dpr,
        sy + (bar[1] - monitor[1]) / dpr,
        (right - left) / dpr,
        (bar[3] - bar[1]) / dpr,
    )


def finder_bar_screen_mapping(snapshot: dict[str, object], screens) -> tuple[object, tuple[float, float, float, float]] | None:
    """Select one screen using native bounds, including friendly-name screens.

    Geometry fallback is safe only when the full current screen inventory has
    one match. It never chooses the primary/nearest screen or resolves a cloned
    or overlapping-screen ambiguity by iteration order.
    """
    if not isinstance(snapshot, dict):
        return None
    candidates: list[tuple[object, tuple[float, float, float, float]]] = []
    seen: set[int] = set()
    for screen in screens or ():
        if id(screen) in seen:
            continue
        seen.add(id(screen))
        try:
            geometry = screen.geometry()
            mapped = finder_bar_logical_rect(
                snapshot,
                screen_name=screen.name(),
                screen_rect=(geometry.x(), geometry.y(), geometry.width(), geometry.height()),
                device_pixel_ratio=screen.devicePixelRatio(),
                allow_geometry_fallback=True,
            )
        except (AttributeError, TypeError, ValueError, RuntimeError, OverflowError):
            continue
        if mapped is not None:
            candidates.append((screen, mapped))
    return candidates[0] if len(candidates) == 1 else None


def _native_finder_bar_window_snapshots(matched_name: str, matched_path: str, *, _cached_snapshot=None) -> list[dict[str, object]]:
    if sys.platform != "win32":
        return []
    from ctypes import wintypes as w

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        try:
            dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
            dwmapi.DwmGetWindowAttribute.argtypes = [w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD]
            dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
        except (OSError, AttributeError):
            dwmapi = None

        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", w.DWORD),
                ("cntThreads", w.DWORD), ("th32ParentProcessID", w.DWORD),
                ("pcPriClassBase", w.LONG), ("dwFlags", w.DWORD), ("szExeFile", w.WCHAR * 260),
            ]

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT), ("rcWork", w.RECT), ("dwFlags", w.DWORD), ("szDevice", w.WCHAR * 32)]

        enum_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        user32.EnumWindows.argtypes = [enum_type, w.LPARAM]
        user32.EnumWindows.restype = w.BOOL
        user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        user32.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
        user32.GetWindowRect.restype = w.BOOL
        user32.IsWindowVisible.argtypes = [w.HWND]
        user32.IsWindowVisible.restype = w.BOOL
        user32.IsIconic.argtypes = [w.HWND]
        user32.IsIconic.restype = w.BOOL
        user32.MonitorFromWindow.argtypes = [w.HWND, w.DWORD]
        user32.MonitorFromWindow.restype = w.HMONITOR
        user32.GetMonitorInfoW.argtypes = [w.HMONITOR, ctypes.POINTER(MonitorInfo)]
        user32.GetMonitorInfoW.restype = w.BOOL
        kernel32.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = w.HANDLE
        kernel32.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel32.Process32FirstW.restype = w.BOOL
        kernel32.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel32.Process32NextW.restype = w.BOOL
        kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel32.OpenProcess.restype = w.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
        kernel32.QueryFullProcessImageNameW.restype = w.BOOL
        kernel32.CloseHandle.argtypes = [w.HANDLE]

        identities: dict[int, tuple[str, str]] = {}
        if _cached_snapshot is not None:
            identity = _finder_bar_identity(_cached_snapshot)
            if identity is None:
                return []
            identities[identity[1]] = (str(_cached_snapshot.get("process_name", "")), str(_cached_snapshot.get("process_path", "")))
        else:
            process_snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
            if process_snapshot == ctypes.c_void_p(-1).value:
                return []
            try:
                entry = ProcessEntry()
                entry.dwSize = ctypes.sizeof(entry)
                more = kernel32.Process32FirstW(process_snapshot, ctypes.byref(entry))
                while more:
                    name = str(entry.szExeFile or "")
                    if name.casefold() == matched_name:
                        path = ""
                        handle = kernel32.OpenProcess(0x1000, False, entry.th32ProcessID)
                        if handle:
                            try:
                                size = w.DWORD(32768)
                                buffer = ctypes.create_unicode_buffer(size.value)
                                if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                                    path = buffer.value
                            finally:
                                kernel32.CloseHandle(handle)
                        if not matched_path or _normalized_runtime_path(path) == matched_path:
                            identities[int(entry.th32ProcessID)] = (name, path)
                    more = kernel32.Process32NextW(process_snapshot, ctypes.byref(entry))
            finally:
                kernel32.CloseHandle(process_snapshot)
        if not identities:
            return []

        results: list[dict[str, object]] = []

        def collect(hwnd, _lparam):
            pid = w.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) not in identities:
                return True
            class_buffer = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
            if class_buffer.value.casefold() != FINDER_BAR_WINDOW_CLASS.casefold():
                return True
            rect = w.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            monitor = user32.MonitorFromWindow(hwnd, 2)  # nearest, including hidden top bars
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return True
            cloaked = w.DWORD()
            if dwmapi is not None:
                if dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) != 0:
                    cloaked.value = 0
            name, path = identities[int(pid.value)]
            results.append({
                "hwnd": int(hwnd), "pid": int(pid.value), "class_name": class_buffer.value,
                "process_name": name, "process_path": path,
                "visible": bool(user32.IsWindowVisible(hwnd)), "cloaked": bool(cloaked.value),
                "minimized": bool(user32.IsIconic(hwnd)),
                "physical_rect": [rect.left, rect.top, rect.right, rect.bottom],
                "monitor_name": str(info.szDevice),
                "physical_monitor_rect": [info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom],
            })
            return True

        previous_dpi_context = None
        set_dpi_context = getattr(user32, "SetThreadDpiAwarenessContext", None)
        if set_dpi_context is None:
            return []  # Do not label DPI-virtualized coordinates as physical.
        set_dpi_context.argtypes = [ctypes.c_void_p]
        set_dpi_context.restype = ctypes.c_void_p
        try:
            previous_dpi_context = set_dpi_context(ctypes.c_void_p(-4))
            if not previous_dpi_context:
                return []
            if _cached_snapshot is not None:
                collect(int(_cached_snapshot["hwnd"]), 0)
            else:
                callback = enum_type(collect)
                user32.EnumWindows(callback, 0)
        finally:
            if previous_dpi_context:
                set_dpi_context(previous_dpi_context)
        return results
    except Exception:
        return []


@dataclass
class FinderPresence:
    available: bool
    source: str
    config_file: Path | None = None


@dataclass
class FinderGlassMaterial:
    top_color: QColor
    mid_color: QColor
    bottom_color: QColor
    highlight_alpha: int
    border_alpha: int
    shadow_alpha: int
    bar_height: float
    blur_value: int
    source: str
    border_width: float = 1.2
    corner_radius: float = 17.0
    has_border: bool = False
    available: bool = False
    texture_image: QImage | None = None
    texture_screen_left: int = 0
    texture_screen_top: int = 0
    texture_is_full_screen: bool = False
    finder_opacity: int | None = None
    finder_blur: int | None = None


@dataclass
class FinderBarSample:
    top_color: QColor
    mid_color: QColor
    bottom_color: QColor
    highlight_alpha: int
    border_alpha: int
    shadow_alpha: int
    bar_height: float
    border_width: float
    corner_radius: float
    has_border: bool
    texture_image: QImage | None
    texture_screen_left: int
    texture_screen_top: int
    texture_is_full_screen: bool


def read_finder_material(
    root: Path = MYDOCKFINDER_ROOT,
    *,
    force_screen_sample: bool = False,
    include_center: bool = True,
    screen=None,
) -> FinderGlassMaterial:
    presence = detect_mydockfinder(root)
    material = FinderGlassMaterial(
        top_color=QColor(242, 244, 244, 104),
        mid_color=QColor(186, 190, 190, 132),
        bottom_color=QColor(112, 116, 116, 128),
        highlight_alpha=88,
        border_alpha=166,
        shadow_alpha=50,
        bar_height=28.0,
        blur_value=11,
        source=presence.source,
        available=presence.available,
    )

    config_file = presence.config_file
    if config_file and config_file.exists():
        try:
            parser = ConfigParser()
            parser.read_file(StringIO(_read_text(config_file)))
            normal = parser["normal"] if parser.has_section("normal") else {}
            themes = parser["themes"] if parser.has_section("themes") else {}
            material.blur_value = _safe_int(normal.get("blurvalue"), material.blur_value)
            material.finder_opacity = _safe_optional_int(_first_value(themes, ("opacity_finder", "finder_opacity", "opacityfinder")))
            material.finder_blur = _safe_optional_int(_first_value(themes, ("blur_finder", "finder_blur", "blurfinder")))
            if material.finder_blur is not None:
                material.blur_value = material.finder_blur
            material.bar_height = _safe_float(
                _first_value(normal, ("barheight", "topbarheight", "menuheight", "height")),
                24.0,
            )
            material.highlight_alpha = min(122, 64 + material.blur_value * 3)
            material.border_alpha = min(196, 138 + material.blur_value * 2)
            material.shadow_alpha = min(76, 32 + material.blur_value * 2)
            material.corner_radius = max(12.0, min(24.0, material.bar_height * 0.48 + material.blur_value * 0.25))
            material.source = str(config_file)
            material.available = True
        except Exception:
            material.source = f"{presence.source}; config unreadable"

    if not (material.available or force_screen_sample):
        return material

    sampled = sample_live_finder_bar(
        material.bar_height,
        material.blur_value,
        include_center=include_center,
        opacity_percent=material.finder_opacity,
        screen=screen,
    )
    if sampled:
        material.top_color = sampled.top_color
        material.mid_color = sampled.mid_color
        material.bottom_color = sampled.bottom_color
        material.highlight_alpha = sampled.highlight_alpha
        material.border_alpha = sampled.border_alpha
        material.shadow_alpha = sampled.shadow_alpha
        material.bar_height = sampled.bar_height
        material.border_width = sampled.border_width
        material.corner_radius = sampled.corner_radius
        material.has_border = sampled.has_border
        material.texture_image = sampled.texture_image
        material.texture_screen_left = sampled.texture_screen_left
        material.texture_screen_top = sampled.texture_screen_top
        material.texture_is_full_screen = sampled.texture_is_full_screen
        material.available = True
        material.source = f"{material.source}; screen sample"

    return material


def detect_mydockfinder(root: Path = MYDOCKFINDER_ROOT) -> FinderPresence:
    for candidate in _known_roots(root):
        config_file = candidate / "config.ini"
        if config_file.exists():
            return FinderPresence(True, str(candidate), config_file)
        if any((candidate / name).exists() for name in ("MyDockFinder.exe", "mydockfinder.exe", "MyFinder.exe", "Mydock.exe", "dock.exe", "dock_64.exe", "Dockmod.exe", "Dockmod64.exe")):
            return FinderPresence(True, str(candidate), config_file)

    process_name = _running_mydockfinder_process()
    if process_name:
        return FinderPresence(True, f"running process: {process_name}")

    window_title = _visible_mydockfinder_window()
    if window_title:
        return FinderPresence(True, f"visible window: {window_title}")

    return FinderPresence(False, "fallback (MyDockFinder not detected)")


def sample_live_finder_bar(
    bar_height: float,
    blur_value: int = 11,
    *,
    include_center: bool = True,
    opacity_percent: int | None = None,
    screen=None,
) -> FinderBarSample | None:
    screen = screen if screen is not None else QGuiApplication.primaryScreen()
    if not screen:
        return None

    geo = screen.geometry()
    capture_height = max(48, min(104, int(max(22.0, bar_height) * 3.0)))
    width = max(160, min(420, int(geo.width() * 0.18)))
    full_bar_image = None
    if include_center:
        full_pixmap = screen.grabWindow(0, 0, 0, geo.width(), capture_height)
        if not full_pixmap.isNull():
            image = _logical_finder_capture_image(full_pixmap)
            if not image.isNull():
                full_bar_image = image
    ratios = (0.04, 0.41, 0.78) if include_center else (0.04, 0.78)
    images = []
    for left_ratio in ratios:
        # Qt's grabWindow(0) offsets are local to this QScreen, in logical
        # pixels. Adding its virtual-desktop origin samples the wrong screen.
        left = int((geo.width() - width) * left_ratio)
        left = max(0, min(left, geo.width() - width))
        pixmap = screen.grabWindow(0, left, 0, width, capture_height)
        if pixmap.isNull():
            continue
        image = _logical_finder_capture_image(pixmap)
        if image.isNull():
            continue
        images.append(image)
    if not images:
        return None

    detected_height = _detect_bar_height(images, bar_height)
    top = _average_colors([_average_strip(image, 0.05, 0.30, detected_height) for image in images])
    mid = _average_colors([_average_strip(image, 0.35, 0.62, detected_height) for image in images])
    bottom = _average_colors([_average_strip(image, 0.68, 0.95, detected_height) for image in images])
    if not top or not mid or not bottom:
        return None
    if max(_saturation(top), _saturation(mid), _saturation(bottom)) > 0.62:
        return None

    if opacity_percent is not None:
        base_alpha = max(72, min(210, int(255 * max(0, min(100, opacity_percent)) / 100)))
    else:
        base_alpha = max(102, min(174, 100 + blur_value * 4))
    edge = _infer_edge(images, detected_height)
    highlight_alpha = max(38, min(128, int(46 + max(0.0, _luminance(top) - _luminance(mid)) * 2.8 + blur_value * 2.2)))
    shadow_alpha = max(24, min(90, int(28 + max(0.0, _luminance(mid) - _luminance(bottom)) * 2.3 + blur_value * 1.3)))

    top_color = QColor(top.red(), top.green(), top.blue(), max(72, base_alpha - 28))
    mid_color = QColor(mid.red(), mid.green(), mid.blue(), base_alpha)
    bottom_color = QColor(bottom.red(), bottom.green(), bottom.blue(), min(188, base_alpha + 8))
    texture = _make_material_texture([full_bar_image] if full_bar_image else images, int(detected_height), mid_color, bottom_color)

    return FinderBarSample(
        top_color=top_color,
        mid_color=mid_color,
        bottom_color=bottom_color,
        highlight_alpha=highlight_alpha,
        border_alpha=edge["border_alpha"],
        shadow_alpha=shadow_alpha,
        bar_height=float(detected_height),
        border_width=edge["border_width"],
        corner_radius=max(13.0, min(25.0, detected_height * 0.48 + edge["border_width"] * 1.7)),
        has_border=edge["has_border"],
        texture_image=texture,
        texture_screen_left=0,
        texture_screen_top=0,
        texture_is_full_screen=False,
    )


def _logical_finder_capture_image(pixmap) -> QImage:
    image = pixmap.toImage()
    if image.isNull():
        return image
    # grabWindow accepts logical coordinates but returns native-size pixels.
    # All bar-edge/color heuristics below operate in logical units.
    dpr = image.devicePixelRatio()
    if math.isfinite(dpr) and dpr > 0.0 and abs(dpr - 1.0) > 0.001:
        image = image.scaled(max(1, round(image.width() / dpr)),
            max(1, round(image.height() / dpr)),
            Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    image.setDevicePixelRatio(1.0)
    return image


def _known_roots(root: Path) -> list[Path]:
    roots = [root]
    for env_name in ("ProgramFiles(x86)", "ProgramFiles"):
        value = os.environ.get(env_name)
        if not value:
            continue
        base = Path(value)
        roots.extend(
            [
                base / "Steam" / "steamapps" / "common" / "MyDockFinder",
                base / "MyDockFinder",
            ]
        )
    unique = []
    seen = set()
    for candidate in roots:
        key = str(candidate).lower()
        if key not in seen:
            unique.append(candidate)
            seen.add(key)
    return unique


def _running_mydockfinder_process() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        TH32CS_SNAPPROCESS = 0x00000002
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
        kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_ulong),
                ("cntUsage", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", ctypes.c_ulong),
                ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_ulong),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == INVALID_HANDLE_VALUE:
            return None
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        kernel32.Process32FirstW.restype = ctypes.c_bool
        kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.restype = ctypes.c_bool
        kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        try:
            if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                return None
            while True:
                name = entry.szExeFile
                if name.lower() in MYDOCKFINDER_PROCESS_NAMES:
                    return name
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        finally:
            kernel32.CloseHandle(snapshot)
    except Exception:
        return None
    return None


def _visible_mydockfinder_window() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        found: list[str] = []

        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value
            lowered = title.lower()
            if any(token in lowered for token in MYDOCKFINDER_WINDOW_TOKENS):
                found.append(title)
                return False
            return True

        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(callback)
        user32.EnumWindows(enum_proc, 0)
        return found[0] if found else None
    except Exception:
        return None


def _detect_bar_height(images: list[QImage], fallback: float) -> int:
    fallback_int = max(18, min(56, int(round(fallback))))
    limit = min(image.height() for image in images)
    if limit <= fallback_int + 3:
        return fallback_int

    rows = [_average_colors([_average_row(image, y) for image in images]) for y in range(limit)]
    best_y = fallback_int
    best_score = 0.0
    search_start = max(12, fallback_int - 14)
    search_end = min(limit - 4, fallback_int + 26)
    for y in range(search_start, search_end):
        before = rows[max(0, y - 2)]
        after = rows[min(limit - 1, y + 2)]
        if not before or not after:
            continue
        score = _color_distance(before, after)
        if score > best_score:
            best_score = score
            best_y = y
    if best_score < 7.0:
        return fallback_int
    return max(18, min(56, best_y))


def _infer_edge(images: list[QImage], bar_height: int) -> dict[str, float | int | bool]:
    inside = _average_colors([_average_row(image, max(0, bar_height - 4)) for image in images])
    edge = _average_colors([_average_row(image, max(0, bar_height - 1)) for image in images])
    below = _average_colors([_average_row(image, min(image.height() - 1, bar_height + 2)) for image in images])
    if not inside or not edge or not below:
        return {"has_border": False, "border_alpha": 0, "border_width": 0.0}

    contrast = _color_distance(edge, inside) * 0.72 + _color_distance(edge, below) * 0.28
    has_border = contrast >= 18.0
    if not has_border:
        return {"has_border": False, "border_alpha": 0, "border_width": 0.0}

    border_alpha = max(86, min(214, int(92 + contrast * 6.4)))
    border_width = max(0.8, min(2.4, 0.78 + contrast / 18.0))
    return {"has_border": True, "border_alpha": border_alpha, "border_width": border_width}


def _make_material_texture(
    images: list[QImage],
    bar_height: int,
    mid_color: QColor,
    bottom_color: QColor,
) -> QImage | None:
    height = max(2, min(64, bar_height))
    width = 128
    texture = QImage(width, height, QImage.Format.Format_ARGB32)
    if texture.isNull():
        return None
    image_height = min(image.height() for image in images)
    source_span = max(1, int(max(2, bar_height) * 0.18))
    for y in range(height):
        ratio = y / max(1, height - 1)
        source_y = max(0, min(image_height - 1, bar_height - 1 - int(ratio * source_span)))
        row_color = _average_colors([_average_row(image, source_y) for image in images])
        if not row_color:
            row_color = _blend(bottom_color, mid_color, min(0.18, ratio * 0.18))
        fade = min(1.0, ratio / 0.18)
        fade = fade * fade * (3.0 - 2.0 * fade)
        feather_base = _blend(bottom_color, row_color, fade)
        # Screen samples are already composited pixels; keep the generated texture opaque
        # so the fused cradle does not show the wallpaper a second time.
        clean = QColor(feather_base.red(), feather_base.green(), feather_base.blue(), 255)
        for x in range(width):
            texture.setPixelColor(x, y, clean)
    return texture


def _average_strip(image: QImage, y0_ratio: float, y1_ratio: float, sample_height: int | None = None) -> QColor | None:
    height = max(1, min(image.height(), sample_height or image.height()))
    y0 = max(0, min(height - 1, int(height * y0_ratio)))
    y1 = max(y0 + 1, min(height, int(height * y1_ratio)))
    return _average_area(image, y0, y1)


def _average_row(image: QImage, y: int) -> QColor | None:
    y = max(0, min(image.height() - 1, y))
    return _average_area(image, y, y + 1)


def _average_area(image: QImage, y0: int, y1: int) -> QColor | None:
    red = green = blue = count = 0
    all_red = all_green = all_blue = all_count = 0
    for y in range(y0, y1, 2):
        for x in range(0, image.width(), 5):
            pixel = image.pixelColor(x, y)
            all_red += pixel.red()
            all_green += pixel.green()
            all_blue += pixel.blue()
            all_count += 1
            if _looks_like_material_pixel(pixel):
                red += pixel.red()
                green += pixel.green()
                blue += pixel.blue()
                count += 1
    if count < max(6, all_count * 0.06):
        red, green, blue, count = all_red, all_green, all_blue, all_count
    if not count:
        return None
    return QColor(red // count, green // count, blue // count)


def _average_colors(colors: list[QColor | None]) -> QColor | None:
    valid = [color for color in colors if color]
    if not valid:
        return None
    red = sum(color.red() for color in valid) // len(valid)
    green = sum(color.green() for color in valid) // len(valid)
    blue = sum(color.blue() for color in valid) // len(valid)
    return QColor(red, green, blue)


def _safe_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _safe_optional_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _first_value(mapping, keys: tuple[str, ...]):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _saturation(color: QColor) -> float:
    _hue, saturation, _value, _alpha = color.getHsvF()
    return 0.0 if saturation < 0 else saturation


def _looks_like_material_pixel(color: QColor) -> bool:
    _hue, saturation, value, _alpha = color.getHsvF()
    if saturation < 0:
        saturation = 0.0
    return saturation <= 0.62 and 0.16 <= value <= 0.94


def _luminance(color: QColor) -> float:
    return color.red() * 0.2126 + color.green() * 0.7152 + color.blue() * 0.0722


def _color_distance(a: QColor, b: QColor) -> float:
    return math_sqrt(
        (a.red() - b.red()) ** 2 * 0.24
        + (a.green() - b.green()) ** 2 * 0.54
        + (a.blue() - b.blue()) ** 2 * 0.22
    )


def math_sqrt(value: float) -> float:
    return value ** 0.5


def _blend(a: QColor, b: QColor, amount: float) -> QColor:
    amount = max(0.0, min(1.0, amount))
    inv = 1.0 - amount
    return QColor(
        int(a.red() * inv + b.red() * amount),
        int(a.green() * inv + b.green() * amount),
        int(a.blue() * inv + b.blue() * amount),
        int(a.alpha() * inv + b.alpha() * amount),
    )


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")
