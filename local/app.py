from __future__ import annotations

import os
import sys
import threading
import time
import traceback
import ctypes

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from api.providers import WindowsMediaProvider
from local.branding import APP_NAME, local_app_data_dir, resolve_icon_paths
from local.island_stack_visual import IslandStackVisual
from local.audio_device_notice import AudioDeviceNotice
from local.settings_store import (
    DEFAULT_MUSIC_AMBIENCE_VALUES,
    DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES,
    MUSIC_READING_LIFT_DEFAULT_LEVEL,
    MUSIC_READING_STRATEGY_DEFAULT,
    MYDOCKFINDER_LISTEN_PROCESS_CHOICES,
    SettingsManager,
    normalize_mydockfinder_listener_process_names,
)
from local.startup import StartupManager, build_startup_command, normalize_startup_mode
from local.settings_window import SettingsWindow
from local.ui_theme import set_theme_settings_getter


MYDOCKFINDER_PROCESS_NAMES = {name for name, _label in MYDOCKFINDER_LISTEN_PROCESS_CHOICES}


def _set_windows_process_identity(app_id: str = APP_NAME) -> bool:
    if sys.platform != "win32":
        return False
    value = str(app_id or APP_NAME).strip() or APP_NAME
    applied = False
    try:
        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(value)
        applied = int(result) == 0
    except Exception:
        applied = False
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(value)
        applied = True
    except Exception:
        pass
    return applied


def _normalized_executable_path(path: str) -> str:
    return str(path or "").strip().strip('"').replace("/", "\\").casefold()


def _mydockfinder_listener_process_names(listener_process_names=None) -> tuple[str, ...]:
    return normalize_mydockfinder_listener_process_names(listener_process_names)


def _mydockfinder_candidate_process_names(configured_path: str, listener_process_names=None) -> set[str]:
    configured = _normalized_executable_path(configured_path)
    configured_name = configured.rsplit("\\", 1)[-1].casefold() if configured else ""
    names = set(_mydockfinder_listener_process_names(listener_process_names))
    if configured_name.endswith(".exe"):
        names.add(configured_name)
    return names


def _running_process_snapshots_for_names(names: set[str]) -> list[tuple[str, str]]:
    if sys.platform != "win32" or not names:
        return []
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        TH32CS_SNAPPROCESS = 0x00000002
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

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

        kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
        kernel32.Process32FirstW.restype = ctypes.c_bool
        kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.restype = ctypes.c_bool
        kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
        kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool
        kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        snapshots: list[tuple[str, str]] = []
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == INVALID_HANDLE_VALUE:
            return snapshots
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        try:
            if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                return snapshots
            lowered_names = {name.casefold() for name in names}
            while True:
                exe_name = str(entry.szExeFile or "")
                if exe_name.casefold() in lowered_names:
                    exe_path = ""
                    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(entry.th32ProcessID))
                    if handle:
                        try:
                            size = ctypes.c_ulong(32768)
                            buffer = ctypes.create_unicode_buffer(size.value)
                            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                                exe_path = buffer.value
                        finally:
                            kernel32.CloseHandle(handle)
                    snapshots.append((exe_name, exe_path))
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        finally:
            kernel32.CloseHandle(snapshot)
        return snapshots
    except Exception:
        return []


def _mydockfinder_process_running(configured_path: str, listener_process_names=None) -> bool:
    return bool(_mydockfinder_process_diagnostics(configured_path, listener_process_names=listener_process_names).get("running"))


def _mydockfinder_process_diagnostics(configured_path: str, listener_process_names=None) -> dict[str, object]:
    configured = _normalized_executable_path(configured_path)
    configured_name = configured.rsplit("\\", 1)[-1].casefold() if configured else ""
    configured_is_exe = configured_name.endswith(".exe")
    configured_root = configured.rsplit("\\", 1)[0] if configured_is_exe and "\\" in configured else configured.rstrip("\\")
    listener_names = set(_mydockfinder_listener_process_names(listener_process_names))
    names = _mydockfinder_candidate_process_names(configured_path, listener_names)
    snapshots = _running_process_snapshots_for_names(names)
    diagnostics: dict[str, object] = {
        "configured_path": str(configured_path or "").strip().strip('"'),
        "configured_name": configured_name,
        "configured_root": configured_root,
        "listener_process_names": sorted(listener_names),
        "candidate_names": sorted(names),
        "snapshots": [{"name": exe_name, "path": exe_path} for exe_name, exe_path in snapshots],
        "running": False,
        "matched_name": "",
        "matched_path": "",
        "reason": "No matching MyDockFinder process found",
    }
    configured_mentions_mydockfinder = "mydockfinder" in configured_root or configured_name in MYDOCKFINDER_PROCESS_NAMES
    for exe_name, exe_path in snapshots:
        name = str(exe_name or "").casefold()
        path = _normalized_executable_path(exe_path)
        if configured:
            if path and path == configured:
                diagnostics.update(running=True, matched_name=exe_name, matched_path=exe_path, reason="Matched configured executable path")
                return diagnostics
            if path and configured_is_exe and configured_root and path.startswith(configured_root + "\\") and name in names:
                diagnostics.update(running=True, matched_name=exe_name, matched_path=exe_path, reason="Matched MyDockFinder runtime process inside configured folder")
                return diagnostics
            if path and not configured_is_exe and configured_root and path.startswith(configured_root + "\\") and name in names:
                diagnostics.update(running=True, matched_name=exe_name, matched_path=exe_path, reason="Matched process inside configured folder")
                return diagnostics
            if not path and configured_name and name == configured_name:
                diagnostics.update(running=True, matched_name=exe_name, matched_path=exe_path, reason="Matched configured process name without path access")
                return diagnostics
            if not path and configured_mentions_mydockfinder and name in listener_names:
                diagnostics.update(running=True, matched_name=exe_name, matched_path=exe_path, reason="Matched MyDockFinder process name without path access")
                return diagnostics
        elif name in names:
            diagnostics.update(running=True, matched_name=exe_name, matched_path=exe_path, reason="Matched known MyDockFinder process name")
            return diagnostics
    return diagnostics


class SingleInstanceGuard:
    def __init__(self):
        self.acquired = True
        self._file = None
        self._msvcrt = None
        if sys.platform != "win32":
            return
        try:
            import msvcrt

            lock_dir = local_app_data_dir()
            os.makedirs(lock_dir, exist_ok=True)
            self._file = open(os.path.join(lock_dir, f"{APP_NAME}.lock"), "a+b")
            self._file.seek(0)
            self._file.write(b"0")
            self._file.flush()
            self._file.seek(0)
            msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            self._msvcrt = msvcrt
        except OSError:
            self.acquired = False
            self.close()

    def close(self):
        if self._file is None:
            return
        try:
            if self._msvcrt is not None and self.acquired:
                self._file.seek(0)
                self._msvcrt.locking(self._file.fileno(), self._msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        try:
            self._file.close()
        except OSError:
            pass
        self._file = None


class MainThreadWatchdog:
    def __init__(self, app: QApplication):
        self.app = app
        self.main_thread_id = threading.main_thread().ident
        self.last_heartbeat = time.monotonic()
        self.last_dump_at = 0.0
        self.log_path = os.path.join(ROOT_DIR, "logs", "freeze-dump.log")
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

        self.timer = QTimer(app)
        self.timer.timeout.connect(self._heartbeat)
        self.timer.start(250)

        self.thread = threading.Thread(target=self._watch, name="MainThreadWatchdog", daemon=True)
        self.thread.start()

    def _heartbeat(self):
        self.last_heartbeat = time.monotonic()

    def _watch(self):
        while True:
            time.sleep(0.8)
            now = time.monotonic()
            if now - self.last_heartbeat < 2.4 or now - self.last_dump_at < 8.0:
                continue
            self.last_dump_at = now
            frame = sys._current_frames().get(self.main_thread_id)
            if frame is None:
                continue
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(f"\n--- main thread stalled for {now - self.last_heartbeat:.2f}s at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                    traceback.print_stack(frame, file=f)
            except OSError:
                pass


class LocalIslandApp:
    def __init__(self, single_instance_guard: SingleInstanceGuard | None = None):
        self.single_instance_guard = single_instance_guard
        _set_windows_process_identity()
        self.app = QApplication(sys.argv)
        self.app.setApplicationName(APP_NAME)
        self.app.setQuitOnLastWindowClosed(False)
        self.watchdog = MainThreadWatchdog(self.app)

        self.settings = SettingsManager()
        self.icon_paths = resolve_icon_paths(self.settings.get("v2_custom_icon_path", ""))
        self.app.setWindowIcon(QIcon(str(self.icon_paths.app_icon)))
        set_theme_settings_getter(self.settings.get)
        IslandStackVisual.load_embedded_lyric_fonts()
        self.data_provider = WindowsMediaProvider(settings_getter=self.settings.get)
        self.data_provider.settings_setter = self.settings.set
        self.app.aboutToQuit.connect(self.data_provider.close_audio_outputs)
        self.data_provider.start_audio_outputs()
        app_font_family = IslandStackVisual.best_available_font_family(str(self.settings.get("v2_font_family", IslandStackVisual.DEFAULT_FONT_FAMILY)))
        self.app.setFont(QFont(app_font_family, 9))
        self.window = IslandStackVisual(data_provider=self.data_provider)
        self.window.setWindowIcon(QIcon(str(self.icon_paths.app_icon)))
        self.window.settings_getter = self.settings.get
        self.window.settings_setter = self.settings.set
        self.audio_device_notice = AudioDeviceNotice()
        self._audio_device_event_serial = 0
        self.audio_device_notice_timer = QTimer(self.app)
        self.audio_device_notice_timer.setInterval(350)
        self.audio_device_notice_timer.timeout.connect(self._poll_audio_device_notice)
        self.audio_device_notice_timer.start()
        self.app.aboutToQuit.connect(self.audio_device_notice.close)
        self._apply_startup_setting()
        self._apply_saved_settings()
        self.settings_window = SettingsWindow(self.window, self.settings)
        self.settings_window.icon_changed_callback = self._apply_global_icon
        self.settings_window.setWindowIcon(QIcon(str(self.icon_paths.app_icon)))
        self.window.settings_changed_callback = self._save_visual_state
        self._setup_mydockfinder_listener()
        self.tray = None
        self._setup_tray()

    def _poll_audio_device_notice(self):
        route = self.data_provider.get_audio_route_snapshot()
        surface = getattr(self.window, "music_fullscreen_overlay", None)
        surface_visible = surface is not None and surface.isVisible()
        music_surface_owned = surface_visible or bool(getattr(self.window, "_music_surface_active", False))
        if music_surface_owned:
            # A notice that started on the island must not float over the
            # music surface when it opens, even without a newer route event.
            self.audio_device_notice.close()
        if route.event_serial <= self._audio_device_event_serial:
            return
        self._audio_device_event_serial = route.event_serial
        identity = str(getattr(route, "event_device_id", "") or "")
        if identity:
            device = next((d for d in route.devices if d.id == identity), None)
        else:
            # Older snapshots only have a label. Two endpoints may share it;
            # retain a generic glyph rather than inventing a device identity.
            matches = tuple(d for d in route.devices if d.name == route.event_name)
            device = matches[0] if len(matches) == 1 else None
        if music_surface_owned:
            if surface_visible and not surface.isMinimized():
                handler = getattr(surface, "on_audio_device_event", None)
                if callable(handler):
                    handler(route, device)
            # Minimized/hidden music surfaces should not open a separate
            # popup or replay a stale connection animation when restored.
            return
        self.audio_device_notice.close()
        handler = getattr(self.window, "on_audio_device_event", None)
        if callable(handler):
            handler(route, device)

    def _music_ambience_values_for_style(self, style: str) -> dict[str, float | int]:
        if style not in DEFAULT_MUSIC_AMBIENCE_VALUES:
            style = "sea_level"
        values_by_style = self.settings.get("v2_music_ambience_values", {}) or {}
        defaults = DEFAULT_MUSIC_AMBIENCE_VALUES.get(style, DEFAULT_MUSIC_AMBIENCE_VALUES["sea_level"])
        if not isinstance(values_by_style, dict):
            values_by_style = {}
        raw_values = values_by_style.get(style, {})
        if not isinstance(raw_values, dict):
            raw_values = {}
        return {
            "ambient_intensity": raw_values.get("ambient_intensity", self.settings.get("v2_ambient_intensity", defaults["ambient_intensity"])),
            "music_flow_intensity": raw_values.get("music_flow_intensity", self.settings.get("v2_music_flow_intensity", defaults["music_flow_intensity"])),
            "compact_duotone_balance": raw_values.get("compact_duotone_balance", self.settings.get("v2_compact_duotone_balance", defaults["compact_duotone_balance"])),
            "expanded_duotone_balance": raw_values.get("expanded_duotone_balance", self.settings.get("v2_expanded_duotone_balance", defaults["expanded_duotone_balance"])),
        }

    def _location_mode_setting(self) -> str:
        value = str(self.settings.get("v2_location_mode", "") or "").strip().lower()
        if value in {"finder", "free", "top", "tasker", "stick"}:
            return value
        legacy_taskbar = str(self.settings.get("v2_taskbar_fusion_mode", "") or "").strip().lower()
        if legacy_taskbar in {"compact", "expanded"}:
            return "tasker"
        if self.settings.get("v2_finder_mode", None) is True:
            return "finder"
        return "top"

    def _enabled_plugins_setting(self) -> list[str]:
        known = ["time", "weather", "agenda"]
        raw = self.settings.get("v2_enabled_plugins", None)
        if isinstance(raw, (list, tuple, set)):
            enabled = {str(item or "").strip().casefold() for item in raw}
            return [plugin_id for plugin_id in known if plugin_id in enabled]
        try:
            legacy_count = max(0, min(len(known), int(self.settings.get("v2_plugin_count", 1))))
        except (TypeError, ValueError):
            legacy_count = 1
        return known[:legacy_count]

    def _mydockfinder_listener_process_names_setting(self) -> tuple[str, ...]:
        return _mydockfinder_listener_process_names(
            self.settings.get(
                "v2_mydockfinder_listener_process_names",
                list(DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES),
            )
        )

    def _apply_startup_setting(self):
        mode = normalize_startup_mode(str(self.settings.get("v2_startup_mode", "none") or "none"))
        if mode == "none":
            return
        StartupManager().apply(mode, build_startup_command())

    def _apply_saved_settings(self):
        self.window.set_saved_placement(
            self.settings.get("v2_stack_center_ratio", 0.5),
            self.settings.get("v2_window_pos", None),
            notify=False,
            stack_direction=self.settings.get("v2_stack_direction", 1),
            last_window_position=self.settings.get("v2_last_window_position", None),
        )
        self.window.position_preset = str(self.settings.get("v2_position_preset", "last") or "last")
        self.window.music_playing = bool(self.settings.get("v2_music_playing", True))
        self.window.music_auto_detect = bool(self.settings.get("v2_music_auto_detect", False))
        self.window.persistent_plugin = bool(self.settings.get("v2_persistent_plugin", True))
        self.window.set_super_topmost(bool(self.settings.get("v2_super_topmost", True)), notify=False)
        mydockfinder_path = str(self.settings.get("v2_mydockfinder_path", "") or "")
        self.window.set_mydockfinder_path(mydockfinder_path, notify=False)
        mydockfinder_listener_process_names = self._mydockfinder_listener_process_names_setting()
        if hasattr(self.window, "set_mydockfinder_listener_process_names"):
            self.window.set_mydockfinder_listener_process_names(mydockfinder_listener_process_names, notify=False)
        else:
            self.window.mydockfinder_listener_process_names = mydockfinder_listener_process_names
        mydockfinder_diagnostics = _mydockfinder_process_diagnostics(
            mydockfinder_path,
            listener_process_names=mydockfinder_listener_process_names,
        )
        self.window.set_mydockfinder_diagnostics(mydockfinder_diagnostics, notify=False)
        self.window.set_manual_finder_available(bool(self.settings.get("v2_manual_mydockfinder", False)), notify=False)
        self.window.set_top_notch_height_mode(str(self.settings.get("v2_top_notch_height_mode", "standard")), notify=False)
        self.window.set_mydockfinder_running(bool(mydockfinder_diagnostics.get("running")), notify=False)
        self.window.set_location_mode(self._location_mode_setting(), notify=False)
        self.window.set_taskbar_corner_style(str(self.settings.get("v2_taskbar_corner_style", "win11")), notify=False)
        self.window.set_taskbar_auto_hide_mode(str(self.settings.get("v2_taskbar_auto_hide_mode", "auto")), notify=False)
        if hasattr(self.window, "set_tasker_follow_taskbar_color"):
            self.window.set_tasker_follow_taskbar_color(bool(self.settings.get("v2_tasker_follow_taskbar_color", False)), notify=False)
        else:
            self.window.tasker_follow_taskbar_color = bool(self.settings.get("v2_tasker_follow_taskbar_color", False))
        self.window.music_background_follow_previous_style = str(self.settings.get("v2_music_background_follow_previous_style", "") or "")
        self.window.set_taskbar_left_handle_color(str(self.settings.get("v2_taskbar_left_handle_color", "#8E8E93")), notify=False)
        self.window.expanded = bool(self.settings.get("v2_expanded", False))
        self.window.show_seconds = bool(self.settings.get("v2_show_seconds", True))
        enabled_plugins = self._enabled_plugins_setting()
        self.window.enabled_plugin_ids = set(enabled_plugins)
        self.window.enabled_plugin_count = len(enabled_plugins)
        self.window.side_island_style = str(self.settings.get("v2_side_island_style", "standard") or "standard")
        self.window.plugin_island_style = str(self.settings.get("v2_plugin_island_style", "standard") or "standard")
        self.window.set_plugin_fullscreen_alert_style(str(self.settings.get("v2_plugin_fullscreen_alert_style", "toast") or "toast"), notify=False)
        self.window.set_plugin_fullscreen_alert_position(
            x_offset=int(self.settings.get("v2_plugin_fullscreen_alert_x_offset", 0)),
            y_offset=int(self.settings.get("v2_plugin_fullscreen_alert_y_offset", 0)),
            notify=False,
        )
        self.window.music_island_style = str(self.settings.get("v2_music_island_style", "standard") or "standard")
        self.window.set_music_expanded_layout_style(
            str(self.settings.get("v2_music_expanded_layout_style", "standard") or "standard"),
            notify=False,
        )
        self.window.time_alarm_side_display = str(self.settings.get("v2_time_alarm_side_display", "note") or "note")
        ambience_style = str(self.settings.get("v2_music_ambience_style", "sea_level") or "sea_level")
        if ambience_style not in DEFAULT_MUSIC_AMBIENCE_VALUES:
            ambience_style = "sea_level"
        ambience_values = self._music_ambience_values_for_style(ambience_style)
        self.window.ambient_intensity = max(0.0, min(1.0, float(ambience_values["ambient_intensity"])))
        self.window.set_music_reactive_background_enabled(
            bool(self.settings.get("v2_music_reactive_background_enabled", False)),
            notify=False,
        )
        self.window.set_music_reactive_background_intensity(
            int(self.settings.get("v2_music_reactive_background_intensity", 55)),
            notify=False,
        )
        self.window.set_music_ambience_style(ambience_style, notify=False)
        self.window.set_music_flow_intensity(float(ambience_values["music_flow_intensity"]), notify=False)
        self.window.set_duotone_balance(
            compact=int(ambience_values["compact_duotone_balance"]),
            expanded=int(ambience_values["expanded_duotone_balance"]),
            notify=False,
        )
        if hasattr(self.window, "set_solid_album_background_fallback"):
            self.window.set_solid_album_background_fallback(
                str(self.settings.get("v2_solid_album_background_fallback", "#16BCFF") or "#16BCFF"),
                notify=False,
            )
        self.window.set_ui_font(str(self.settings.get("v2_font_family", IslandStackVisual.DEFAULT_FONT_FAMILY)), notify=False)
        self.window.set_high_quality_font_rendering(bool(self.settings.get("v2_high_quality_font_rendering", False)), notify=False)
        self.window.set_animation_fps(int(self.settings.get("v2_animation_fps", 60)), notify=False)
        self.window.set_lyric_animation_fps(int(self.settings.get("v2_lyric_animation_fps", 120)), notify=False)
        self.window.set_animation_speed(float(self.settings.get("v2_animation_speed", 1.0)), notify=False)
        self.window.set_empty_island_display_mode(str(self.settings.get("v2_empty_island_display_mode", "visible_bar")), notify=False)
        if hasattr(self.window, "set_no_music_island_priority"):
            self.window.set_no_music_island_priority(str(self.settings.get("v2_no_music_island_priority", "plugin")), notify=False)
        else:
            self.window.no_music_island_priority = str(self.settings.get("v2_no_music_island_priority", "plugin") or "plugin")
        self.window.set_empty_bar_palette(str(self.settings.get("v2_empty_bar_palette", "aurora")), notify=False)
        self.window.set_empty_bar_geometry(
            width=int(self.settings.get("v2_empty_bar_width", 92)),
            height=int(self.settings.get("v2_empty_bar_height", 14)),
            y_offset=int(self.settings.get("v2_empty_bar_y_offset", 0)),
            opacity=int(self.settings.get("v2_empty_bar_opacity", 100)),
            notify=False,
        )
        self.window.set_empty_bar_custom_colors(
            self.settings.get("v2_empty_bar_custom_colors", ["#46D6FF", "#73FFA6", "#FF5A86"]),
            notify=False,
        )
        self.window.set_barrage_settings(
            y_offset=int(self.settings.get("v2_barrage_y_offset", 0)),
            font_size=int(self.settings.get("v2_barrage_font_size", 10)),
            speed=int(self.settings.get("v2_barrage_speed", 100)),
            use_backdrop=bool(self.settings.get("v2_barrage_use_backdrop", True)),
            show_icon=bool(self.settings.get("v2_barrage_show_icon", True)),
            display_mode=str(self.settings.get("v2_barrage_display_mode", "fullscreen") or "fullscreen"),
            notify=False,
        )
        self.window.set_auto_expand_bounds(bool(self.settings.get("v2_auto_expand_bounds", True)), notify=False)
        self.window.set_expand_direction(str(self.settings.get("v2_expand_direction", "auto")), notify=False)
        self.window.set_lyric_offset_seconds(float(self.settings.get("v2_lyric_offset_seconds", 0.0)), notify=False)
        self.window.set_lyric_font_size(int(self.settings.get("v2_lyric_font_size", 18)), notify=False)
        self.window.set_music_surface_reading_lift_level(
            self.settings.get("v2_music_surface_reading_lift_level", MUSIC_READING_LIFT_DEFAULT_LEVEL), notify=False
        )
        self.window.set_music_surface_reading_gradient_steps(
            self.settings.get("v2_music_surface_reading_gradient_steps", 30), notify=False
        )
        self.window.set_music_surface_reading_strategy(
            self.settings.get("v2_music_surface_reading_strategy", MUSIC_READING_STRATEGY_DEFAULT), notify=False
        )
        self.window.set_music_sparse_highlight_seconds_per_unit(
            self.settings.get("v2_music_sparse_highlight_seconds_per_unit", 1.5), notify=False
        )
        self.window.set_music_intro_cleanup_enabled(bool(self.settings.get("v2_music_intro_cleanup_enabled", True)), notify=False)
        self.window.set_enable_dual_line_lyrics(bool(self.settings.get("v2_enable_dual_line_lyrics", False)), notify=False)
        self.window.set_dual_line_font_size(int(self.settings.get("v2_dual_line_font_size", 12)), notify=False)
        self.window.set_dual_line_secondary_weight(int(self.settings.get("v2_dual_line_secondary_weight", 72)), notify=False)
        self.window.set_dual_line_secondary_spacing(int(self.settings.get("v2_dual_line_secondary_spacing", 100)), notify=False)
        self.window.set_compact_lyric_show_cover(bool(self.settings.get("v2_compact_lyric_show_cover", True)), notify=False)
        self.window.set_wave_font_weight(str(self.settings.get("v2_wave_font_weight", "bold")), notify=False)
        self.window.set_lyric_flow_style(str(self.settings.get("v2_lyric_flow_style", "wave_stream")), notify=False)
        self.window.set_gelv_font_family(IslandStackVisual.GELV_FONT_FAMILY, notify=False)
        self.window.set_no_lyrics_display_mode(str(self.settings.get("v2_no_lyrics_display_mode", "text")), notify=False)
        self.window.set_album_island_cover_count(int(self.settings.get("v2_album_island_cover_count", 5)), notify=False)
        self.window.set_album_island_cover_size(int(self.settings.get("v2_album_island_cover_size", 30)), notify=False)
        self.window.set_album_island_compact_cover_size(
            int(self.settings.get("v2_album_island_compact_cover_size", self.settings.get("v2_album_island_cover_size", 30))),
            notify=False,
        )
        self.window.set_album_island_expanded_cover_size(int(self.settings.get("v2_album_island_expanded_cover_size", 72)), notify=False)
        self.window.set_album_island_spacing(int(self.settings.get("v2_album_island_spacing", 112)), notify=False)
        self.window.set_album_island_secondary_style(str(self.settings.get("v2_album_island_secondary_style", "perspective_dim")), notify=False)
        self.window.set_album_island_perspective_amount(int(self.settings.get("v2_album_island_perspective_amount", 100)), notify=False)
        self.window.set_album_island_record(
            float(self.settings.get("v2_album_island_listened_seconds_total", 0.0) or 0.0),
            self.settings.get("v2_album_island_history", []),
            notify=False,
        )
        self.window.set_no_lyrics_text(str(self.settings.get("v2_no_lyrics_text", "暂无歌词")), notify=False)
        position_preset = str(self.settings.get("v2_position_preset", "last") or "last")
        self.window.apply_position_preset(position_preset, notify=False)
        self.window._set_plugin_focus(min(self.window.plugin_focus_target, max(0, self.window.enabled_plugin_count - 1)), immediate=True)

    def _save_visual_state(self, state: dict[str, object]):
        for key, value in state.items():
            if key == "v2_window_pos" and value is None and self.settings.get(key, None) is not None:
                continue
            self.settings.set(key, value)
        if hasattr(self, "settings_window"):
            self.settings_window.sync_from_widget()

    def _setup_mydockfinder_listener(self):
        self.mydockfinder_listener_timer = QTimer(self.app)
        self.mydockfinder_listener_timer.timeout.connect(self._poll_mydockfinder_presence)
        self._poll_mydockfinder_presence()
        self.mydockfinder_listener_timer.start(2500)

    def _poll_mydockfinder_presence(self):
        path = str(self.settings.get("v2_mydockfinder_path", "") or "").strip().strip('"')
        listener_process_names = self._mydockfinder_listener_process_names_setting()
        if str(getattr(self.window, "mydockfinder_path", "") or "") != path:
            self.window.set_mydockfinder_path(path, notify=False)
        if tuple(getattr(self.window, "mydockfinder_listener_process_names", ())) != listener_process_names:
            if hasattr(self.window, "set_mydockfinder_listener_process_names"):
                self.window.set_mydockfinder_listener_process_names(listener_process_names, notify=False)
            else:
                self.window.mydockfinder_listener_process_names = listener_process_names
        diagnostics = _mydockfinder_process_diagnostics(path, listener_process_names=listener_process_names)
        if hasattr(self.window, "set_mydockfinder_diagnostics"):
            self.window.set_mydockfinder_diagnostics(diagnostics, notify=False)
        else:
            self.window.mydockfinder_diagnostics = diagnostics
        running = bool(diagnostics.get("running"))
        if bool(getattr(self.window, "mydockfinder_running", False)) == running:
            if hasattr(self, "settings_window"):
                self.settings_window.sync_from_widget()
            return
        self.window.set_mydockfinder_running(running, notify=True)

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        icon_paths = resolve_icon_paths(self.settings.get("v2_custom_icon_path", ""))
        self.tray = QSystemTrayIcon(QIcon(str(icon_paths.tray_icon)), self.app)
        if self.tray.icon().isNull():
            self.tray.setIcon(self.app.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.tray.setToolTip(APP_NAME)

        menu = QMenu()

        show_action = QAction("显示 / 隐藏", menu)
        show_action.triggered.connect(self.toggle_window)
        menu.addAction(show_action)

        settings_action = QAction("设置", menu)
        settings_action.triggered.connect(self.show_settings)
        menu.addAction(settings_action)

        menu.addSeparator()

        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _apply_global_icon(self, custom_icon_path: str | None = None):
        self.icon_paths = resolve_icon_paths(custom_icon_path if custom_icon_path is not None else self.settings.get("v2_custom_icon_path", ""))
        app_icon = QIcon(str(self.icon_paths.app_icon))
        self.app.setWindowIcon(app_icon)
        self.window.setWindowIcon(app_icon)
        if hasattr(self, "settings_window"):
            self.settings_window.setWindowIcon(app_icon)
        if getattr(self, "tray", None) is not None:
            tray_icon = QIcon(str(self.icon_paths.tray_icon))
            if tray_icon.isNull():
                tray_icon = self.app.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
            self.tray.setIcon(tray_icon)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.toggle_window()

    def show_settings(self):
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def toggle_window(self):
        focus_music_surface = getattr(self.window, "_focus_active_music_surface", None)
        if callable(focus_music_surface) and focus_music_surface():
            return
        if bool(getattr(self.window, "_music_surface_active", False)):
            return
        if self.window.isVisible():
            self.window.hide()
        else:
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()

    def _flush_visual_state_now(self):
        try:
            snapshot = self.window.settings_snapshot()
        except Exception:
            snapshot = {}
        if isinstance(snapshot, dict):
            for key, value in snapshot.items():
                self.settings.set(key, value)
        self.settings.save_now()

    def quit(self):
        if self.tray:
            self.tray.hide()
        self._flush_visual_state_now()
        self.settings_window.close()
        self.window.close()
        if self.single_instance_guard is not None:
            self.single_instance_guard.close()
        self.app.quit()

    def run(self):
        self.window.show()
        return self.app.exec()


def main():
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")

    single_instance_guard = SingleInstanceGuard()
    if not single_instance_guard.acquired:
        return

    app = LocalIslandApp(single_instance_guard)
    sys.exit(app.run())


if __name__ == "__main__":
    main()
