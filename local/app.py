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

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QStyle, QSystemTrayIcon, QWidget, QWidgetAction

from api.providers import PlaybackSourceProvider, WindowsMediaProvider
from api.online_music import make_online_music_service
from local.owned_music_player import OwnedMusicPlayer
from local.windows_smtc import WindowsSmtcBridge
from local.playback_entry_controller import PlaybackEntryController
from local.music_library_controller import MusicLibraryController
from local.song_collection_controller import SongCollectionController
from local.mydockfinder import MYDOCKFINDER_PROCESS_NAMES, read_live_finder_bars
from local.branding import APP_NAME, local_app_data_dir, resolve_icon_paths
from local.island_stack_visual import IslandStackVisual
from local.settings_store import (
    DEFAULT_SETTINGS,
    DEFAULT_MUSIC_AMBIENCE_VALUES,
    FIXED_DEFAULT_SETTING_KEYS,
    MUSIC_READING_LIFT_DEFAULT_LEVEL,
    MUSIC_READING_STRATEGY_DEFAULT,
    SettingsManager,
)
from local.startup import StartupManager, build_startup_command, normalize_startup_mode
from local.settings_window import SettingsWindow, StatusLamp
from local.ios_widgets import IosSwitch
from local.ui_theme import set_theme_settings_getter


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
    # Per-process permission was retired. Neither a migrated selection nor a
    # manually configured executable may expand the current main-runtime names.
    return tuple(sorted(MYDOCKFINDER_PROCESS_NAMES))


def _mydockfinder_candidate_process_names(configured_path: str, listener_process_names=None) -> set[str]:
    return set(_mydockfinder_listener_process_names(listener_process_names))


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


def _mydockfinder_process_diagnostics(configured_path: str, listener_process_names=None, *, previous_bars=()) -> dict[str, object]:
    configured = _normalized_executable_path(configured_path)
    configured_name = configured.rsplit("\\", 1)[-1].casefold() if configured else ""
    configured_is_exe = configured_name.endswith(".exe")
    configured_root = configured.rsplit("\\", 1)[0] if configured_is_exe and "\\" in configured else configured.rstrip("\\")
    listener_names = set(_mydockfinder_listener_process_names(listener_process_names))
    names = _mydockfinder_candidate_process_names(configured_path, listener_names)
    snapshots = [
        (exe_name, exe_path)
        for exe_name, exe_path in _running_process_snapshots_for_names(names)
        if str(exe_name or "").casefold() in names
    ]
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
        "reason": "No running dock.exe or Dock_64.exe found",
        "finder_bars": [],
        "finder_enabled": False,
    }

    trusted_runtimes: list[tuple[str, str, str]] = []
    for exe_name, exe_path in snapshots:
        name = str(exe_name or "").casefold()
        path = _normalized_executable_path(exe_path)
        # Retired path selections are diagnostic migration data, not runtime
        # permissions. Toolhelp's exact executable name remains available for
        # service/elevated processes even when querying their path is denied.
        if name in names:
            reason = "Matched known MyDockFinder process name" if path else "Matched MyDockFinder process name without path access"
            trusted_runtimes.append((exe_name, exe_path, reason))
    if trusted_runtimes:
        first_name, first_path, first_reason = trusted_runtimes[0]
        diagnostics.update(running=True, matched_name=first_name, matched_path=first_path, reason=first_reason)
        bars: dict[int, dict[str, object]] = {}
        seen_runtimes: set[tuple[str, str]] = set()
        for exe_name, exe_path, _reason in trusted_runtimes:
            runtime_key = (str(exe_name).casefold(), _normalized_executable_path(exe_path))
            if runtime_key in seen_runtimes:
                continue
            seen_runtimes.add(runtime_key)
            for bar in read_live_finder_bars(matched_name=exe_name, matched_path=exe_path, previous_bars=previous_bars):
                bars[int(bar["hwnd"])] = bar
        diagnostics["finder_bars"] = list(bars.values())
        diagnostics["finder_enabled"] = any(bool(bar.get("enabled")) for bar in bars.values())
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
    def __init__(self, single_instance_guard: SingleInstanceGuard | None = None, *, installer_handoff=None, installer_sync=None):
        self.single_instance_guard = single_instance_guard
        self._installer_handoff = installer_handoff
        self._installer_sync = installer_sync
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
        self.system_provider = WindowsMediaProvider(settings_getter=self._runtime_setting)
        self.online_music = make_online_music_service()
        self.owned_player = OwnedMusicPlayer(self.online_music, self.system_provider.lyrics_store, self.app)
        self.owned_player.set_playback_mode(self.settings.get("v2_owned_playback_mode", "sequential"))
        self.owned_player.set_personal_dj_enabled(self.settings.get("v2_personal_dj_enabled", False))
        self.owned_player.restore_personal_dj_voice(self.settings.get("v2_personal_dj_voice_key", ""))
        self.data_provider = PlaybackSourceProvider(self.system_provider, self.owned_player, self._runtime_setting)
        self.data_provider.settings_setter = self.settings.set
        self.system_media_controls = WindowsSmtcBridge(self.owned_player, self.data_provider._owned_active, self.app)
        self.app.aboutToQuit.connect(self.system_media_controls.close)
        self.app.aboutToQuit.connect(self.data_provider.close_audio_outputs)
        if self.settings.get("v2_owned_playback_enabled", True) or self.settings.get("v2_system_capture_enabled", False):
            self.data_provider.start_audio_outputs()
        app_font_family = IslandStackVisual.best_available_font_family(DEFAULT_SETTINGS["v2_font_family"])
        self.app.setFont(QFont(app_font_family, 9))
        self.window = IslandStackVisual(data_provider=self.data_provider)
        self.window._keep_minimum_surface = True
        self.window._tray_hidden = False
        self.window._application_quitting = False
        self.window.setWindowIcon(QIcon(str(self.icon_paths.app_icon)))
        self.window.settings_getter = self._runtime_setting
        self.window.settings_setter = self.settings.set
        self._apply_startup_setting()
        self._apply_saved_settings()
        self._owned_surface_key = None
        self.owned_player.changed.connect(self._owned_playback_changed)
        self.settings_window = SettingsWindow(self.window, self.settings)
        self.playback_entry = PlaybackEntryController(self.settings, self.system_provider, self.window,
                                                       self.online_music, self.app)
        self.music_library = MusicLibraryController(self.settings, self.online_music, self.owned_player,
                                                   self.window, self.playback_entry, self.app)
        self.song_collection = SongCollectionController(self.online_music, self.owned_player, self.app,
            artwork_lookup=self.music_library.cached_playlist_artwork)
        self.window._song_collection_controller = self.song_collection
        self.window._music_login_required = self._music_login_required
        self.window._music_login_callback = self._open_music_login
        self.app.aboutToQuit.connect(self.song_collection.close)
        self.window._music_library_back_callback = self.music_library.return_from_music_surface
        self.playback_entry.login_callback = self.music_library.open_source
        self.settings_window.online_music_login_callback = self.playback_entry.show_login
        from local.personal_dj_voice_window import PersonalDjVoiceWindow
        self.personal_dj_voices = PersonalDjVoiceWindow(self.data_provider, self.settings_window)
        self.settings_window.personal_dj_voice_callback = self._show_personal_dj_voices
        self.window._personal_dj_voice_hover_callback = self._show_personal_dj_hover
        self.app.aboutToQuit.connect(self.personal_dj_voices.tasks.close)
        self.settings.setting_changed.connect(self._playback_mode_changed)
        self.app.aboutToQuit.connect(self.playback_entry.close)
        self.app.aboutToQuit.connect(self.music_library.close)
        self.app.aboutToQuit.connect(self.online_music.close)
        self.settings_window.icon_changed_callback = self._apply_global_icon
        self.settings_window.setWindowIcon(QIcon(str(self.icon_paths.app_icon)))
        self.window.settings_changed_callback = self._save_visual_state
        self._setup_mydockfinder_listener()
        self.tray = None
        self._setup_tray()

    def _music_login_required(self):
        if not self.settings.get("v2_owned_playback_enabled", True):
            return False
        # Cached account snapshots are lock-only. No disk/network access from paint.
        from local.playback_entry_window import ENTRY_SOURCES
        return not any(self.online_music.account_status(source.source_id).logged_in
                       for source in ENTRY_SOURCES)

    def _open_music_login(self):
        if not self._music_login_required():
            return False
        from local.music_navigation_transition import capture_navigation_frame
        frame = capture_navigation_frame(getattr(self.window, "music_fullscreen_overlay", None))
        self.window._music_library_active = True
        if self.window._music_surface_active:
            self.window._restore_island_from_music_surface()
        self.window.hide()
        self.playback_entry._library_origin = frame
        self.playback_entry.show_login()
        return True

    def _runtime_setting(self, key, default=None):
        # The song-page button controls this session's lyric lookup as well as
        # its painter. The persisted default remains fixed for the next launch.
        if key == "v2_enable_dual_line_lyrics":
            window = getattr(self, "window", None)
            return bool(getattr(window, "enable_dual_line_lyrics", DEFAULT_SETTINGS[key]))
        return self.settings.get(key, default)

    def _owned_playback_changed(self):
        """Publish source/play-state/queue boundaries before another lyric poll.

        Owned player mutations and their changed signal belong to Qt's GUI
        thread. Both music surfaces read this same island owner; ordinary
        position/artwork/lyric updates keep the existing bounded poll cadence.
        """
        active = self.data_provider._owned_active()
        track = self.owned_player.track if active else None
        queue_token = getattr(self.owned_player, "playback_queue_token", None)
        key = (active, str(getattr(track, "track_id", "") or ""),
               bool(getattr(track, "is_playing", False)),
               queue_token() if active and callable(queue_token) else None)
        now = time.monotonic()
        # The DJ title, artist and completion belong to this source snapshot.
        # Publish a changed song before its synchronous speaking/EOF signal
        # can observe the previous owner metadata.
        if key != self._owned_surface_key:
            self._owned_surface_key = key
            self.window.last_media_refresh_at = 0.0
            if self.window._refresh_media_data(now):
                self.window.update()
            # A paused song's full queue can change without its visual track
            # or next-three provider snapshot changing. Repaint that surface
            # on the queue boundary even when the owner reports no ink change.
            surface = getattr(self.window, "music_fullscreen_overlay", None)
            if surface is not None and surface.isVisible():
                surface._finish_stale_track_transition()
                surface.update()
        sync_dj = getattr(self.window, "_sync_personal_dj_island", None)
        if callable(sync_dj) and sync_dj(now):
            self.window.update()

    def _playback_mode_changed(self, key, value):
        if key == "v2_owned_playback_mode":
            self.owned_player.set_playback_mode(value)
        if key == "v2_personal_dj_enabled":
            self.owned_player.set_personal_dj_enabled(value)
        if key == "v2_owned_playback_enabled" and value is False:
            self.owned_player.stop()
        if key == "v2_owned_playback_enabled":
            self.system_media_controls.refresh()
        if key == "v2_system_capture_enabled" and value is False:
            self.system_provider.suspend_live_audio()
            self.system_provider.stop_audio_cast()
        if key == "v2_system_capture_enabled" and value is True:
            self.system_provider.start_audio_outputs()
        if key == "v2_mydockfinder_monitor_enabled":
            self._configure_mydockfinder_listener()
        if key == "v2_bar_hide_follow_enabled" and hasattr(self.window, "set_bar_hide_follow_enabled"):
            self.window.set_bar_hide_follow_enabled(bool(value), notify=False)

    def _show_personal_dj_voices(self):
        self.personal_dj_voices.show()
        self.personal_dj_voices.raise_()
        self.personal_dj_voices.activateWindow()

    def _show_personal_dj_hover(self, surface, visible):
        from local.personal_dj_voice_popover import PersonalDjVoicePopover
        popover = getattr(surface, "_dj_voice_selector_widget", None)
        if popover is None and visible:
            popover = PersonalDjVoicePopover(self.data_provider, surface)
            surface._dj_voice_selector_widget = popover
            self.app.aboutToQuit.connect(popover.tasks.close)
        return popover.present(visible) if popover is not None else None

    def _music_ambience_values_for_style(self, style: str) -> dict[str, float | int]:
        if style not in DEFAULT_MUSIC_AMBIENCE_VALUES:
            style = "sea_level"
        return dict(DEFAULT_MUSIC_AMBIENCE_VALUES[style])

    def _location_mode_setting(self) -> str:
        return DEFAULT_SETTINGS["v2_location_mode"]

    def _mydockfinder_listener_process_names_setting(self) -> tuple[str, ...]:
        # Legacy choices remain on disk only as migration data.
        return _mydockfinder_listener_process_names()

    def _apply_startup_setting(self):
        mode = normalize_startup_mode(str(self.settings.get("v2_startup_mode", "none") or "none"))
        if mode == "none":
            return
        StartupManager().apply(mode, build_startup_command())

    def _apply_saved_settings(self):
        def saved_setting(key, fallback=None):
            if key in FIXED_DEFAULT_SETTING_KEYS:
                return DEFAULT_SETTINGS[key]
            return self.settings.get(key, fallback)

        self.window.set_saved_placement(
            saved_setting("v2_stack_center_ratio", 0.5),
            saved_setting("v2_window_pos", None),
            notify=False,
            stack_direction=saved_setting("v2_stack_direction", 1),
            last_window_position=saved_setting("v2_last_window_position", None),
        )
        self.window.position_preset = str(saved_setting("v2_position_preset", "last") or "last")
        self.window.music_playing = bool(saved_setting("v2_music_playing", True))
        self.window.music_auto_detect = bool(saved_setting("v2_music_auto_detect", False))
        self.window.persistent_plugin = False
        self.window.set_super_topmost(bool(saved_setting("v2_super_topmost", True)), notify=False)
        if hasattr(self.window, "set_finder_bar_style"):
            self.window.set_finder_bar_style(self.settings.get("v2_finder_bar_style", "transparent"), notify=False)
        if hasattr(self.window, "set_finder_bar_center_ratio"):
            self.window.set_finder_bar_center_ratio(self.settings.get("v2_finder_bar_center_ratio", 0.5), notify=False)
        if hasattr(self.window, "set_bar_hide_follow_enabled"):
            self.window.set_bar_hide_follow_enabled(bool(saved_setting("v2_bar_hide_follow_enabled", True)), notify=False)
        mydockfinder_listener_process_names = self._mydockfinder_listener_process_names_setting()
        if hasattr(self.window, "set_mydockfinder_listener_process_names"):
            self.window.set_mydockfinder_listener_process_names(mydockfinder_listener_process_names, notify=False)
        else:
            self.window.mydockfinder_listener_process_names = mydockfinder_listener_process_names
        monitor_enabled = bool(saved_setting("v2_mydockfinder_monitor_enabled", True))
        self.window.mydockfinder_monitor_enabled = monitor_enabled
        mydockfinder_diagnostics = _mydockfinder_process_diagnostics(
            "",
            listener_process_names=mydockfinder_listener_process_names,
        ) if monitor_enabled else {"running": False, "finder_bars": [], "reason": "monitor_disabled"}
        self.window.set_mydockfinder_path(str(mydockfinder_diagnostics.get("matched_path") or ""), notify=False)
        self.window.set_mydockfinder_diagnostics(mydockfinder_diagnostics, notify=False)
        self.window.set_manual_finder_available(bool(saved_setting("v2_manual_mydockfinder", False)), notify=False)
        self.window.set_top_notch_height_mode(str(saved_setting("v2_top_notch_height_mode", "standard")), notify=False)
        self.window.set_mydockfinder_running(bool(mydockfinder_diagnostics.get("running")), notify=False)
        self.window.set_location_mode(self._location_mode_setting(), notify=False)
        self.window.set_taskbar_corner_style(str(saved_setting("v2_taskbar_corner_style", "win11")), notify=False)
        self.window.set_taskbar_auto_hide_mode(str(saved_setting("v2_taskbar_auto_hide_mode", "auto")), notify=False)
        if hasattr(self.window, "set_tasker_follow_taskbar_color"):
            self.window.set_tasker_follow_taskbar_color(bool(saved_setting("v2_tasker_follow_taskbar_color", False)), notify=False)
        else:
            self.window.tasker_follow_taskbar_color = bool(saved_setting("v2_tasker_follow_taskbar_color", False))
        self.window.music_background_follow_previous_style = str(saved_setting("v2_music_background_follow_previous_style", "") or "")
        self.window.set_taskbar_left_handle_color(str(saved_setting("v2_taskbar_left_handle_color", "#8E8E93")), notify=False)
        self.window.expanded = bool(saved_setting("v2_expanded", False))
        self.window.show_seconds = bool(saved_setting("v2_show_seconds", True))
        self.window.enabled_plugin_ids = set()
        self.window.enabled_plugin_count = 0
        self.window.side_island_style = str(saved_setting("v2_side_island_style", "standard") or "standard")
        self.window.plugin_island_style = str(saved_setting("v2_plugin_island_style", "standard") or "standard")
        self.window.music_island_style = str(saved_setting("v2_music_island_style", "standard") or "standard")
        self.window.set_music_expanded_layout_style(
            str(saved_setting("v2_music_expanded_layout_style", "standard") or "standard"),
            notify=False,
        )
        self.window.time_alarm_side_display = str(saved_setting("v2_time_alarm_side_display", "note") or "note")
        ambience_style = str(saved_setting("v2_music_ambience_style", "sea_level") or "sea_level")
        if ambience_style not in DEFAULT_MUSIC_AMBIENCE_VALUES:
            ambience_style = "sea_level"
        ambience_values = self._music_ambience_values_for_style(ambience_style)
        self.window.ambient_intensity = max(0.0, min(1.0, float(ambience_values["ambient_intensity"])))
        self.window.set_music_reactive_background_enabled(
            bool(saved_setting("v2_music_reactive_background_enabled", False)),
            notify=False,
        )
        self.window.set_music_reactive_background_intensity(
            int(saved_setting("v2_music_reactive_background_intensity", 55)),
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
                str(saved_setting("v2_solid_album_background_fallback", "#16BCFF") or "#16BCFF"),
                notify=False,
            )
        self.window.set_ui_font(str(saved_setting("v2_font_family", IslandStackVisual.DEFAULT_FONT_FAMILY)), notify=False)
        self.window.set_high_quality_font_rendering(bool(saved_setting("v2_high_quality_font_rendering", False)), notify=False)
        self.window.set_animation_fps(int(saved_setting("v2_animation_fps", 60)), notify=False)
        self.window.set_lyric_animation_fps(int(saved_setting("v2_lyric_animation_fps", 120)), notify=False)
        self.window.set_animation_speed(float(saved_setting("v2_animation_speed", 1.0)), notify=False)
        self.window.set_empty_island_display_mode(str(saved_setting("v2_empty_island_display_mode", "visible_bar")), notify=False)
        if hasattr(self.window, "set_no_music_island_priority"):
            self.window.set_no_music_island_priority(str(saved_setting("v2_no_music_island_priority", "plugin")), notify=False)
        else:
            self.window.no_music_island_priority = str(saved_setting("v2_no_music_island_priority", "plugin") or "plugin")
        self.window.set_empty_bar_palette(str(saved_setting("v2_empty_bar_palette", "aurora")), notify=False)
        self.window.set_empty_bar_geometry(
            width=int(saved_setting("v2_empty_bar_width", 92)),
            height=int(saved_setting("v2_empty_bar_height", 14)),
            y_offset=int(saved_setting("v2_empty_bar_y_offset", 0)),
            opacity=int(saved_setting("v2_empty_bar_opacity", 100)),
            notify=False,
        )
        self.window.set_empty_bar_custom_colors(
            saved_setting("v2_empty_bar_custom_colors", ["#46D6FF", "#73FFA6", "#FF5A86"]),
            notify=False,
        )
        self.window.set_auto_expand_bounds(bool(saved_setting("v2_auto_expand_bounds", True)), notify=False)
        self.window.set_expand_direction(str(saved_setting("v2_expand_direction", "auto")), notify=False)
        self.window.set_lyric_offset_seconds(float(saved_setting("v2_lyric_offset_seconds", 0.0)), notify=False)
        self.window.set_lyric_font_size(int(saved_setting("v2_lyric_font_size", 18)), notify=False)
        self.window.set_music_surface_reading_lift_level(
            saved_setting("v2_music_surface_reading_lift_level", MUSIC_READING_LIFT_DEFAULT_LEVEL), notify=False
        )
        self.window.set_music_surface_reading_gradient_steps(
            saved_setting("v2_music_surface_reading_gradient_steps", 30), notify=False
        )
        self.window.set_music_surface_reading_strategy(
            saved_setting("v2_music_surface_reading_strategy", MUSIC_READING_STRATEGY_DEFAULT), notify=False
        )
        self.window.set_music_sparse_highlight_seconds_per_unit(
            saved_setting("v2_music_sparse_highlight_seconds_per_unit", 0.8), notify=False
        )
        self.window.set_music_intro_cleanup_enabled(bool(saved_setting("v2_music_intro_cleanup_enabled", False)), notify=False)
        self.window.set_enable_dual_line_lyrics(bool(saved_setting("v2_enable_dual_line_lyrics", False)), notify=False)
        self.window.set_dual_line_font_size(int(saved_setting("v2_dual_line_font_size", 12)), notify=False)
        self.window.set_dual_line_secondary_weight(int(saved_setting("v2_dual_line_secondary_weight", 72)), notify=False)
        self.window.set_dual_line_secondary_spacing(int(saved_setting("v2_dual_line_secondary_spacing", 100)), notify=False)
        self.window.set_compact_lyric_show_cover(bool(saved_setting("v2_compact_lyric_show_cover", True)), notify=False)
        self.window.set_wave_font_weight(str(saved_setting("v2_wave_font_weight", "bold")), notify=False)
        self.window.set_lyric_flow_style(str(saved_setting("v2_lyric_flow_style", "wave_stream")), notify=False)
        self.window.set_gelv_font_family(IslandStackVisual.GELV_FONT_FAMILY, notify=False)
        self.window.set_no_lyrics_display_mode(str(saved_setting("v2_no_lyrics_display_mode", "text")), notify=False)
        self.window.set_album_island_cover_count(int(saved_setting("v2_album_island_cover_count", 5)), notify=False)
        self.window.set_album_island_cover_size(int(saved_setting("v2_album_island_cover_size", 30)), notify=False)
        self.window.set_album_island_compact_cover_size(
            int(saved_setting("v2_album_island_compact_cover_size", saved_setting("v2_album_island_cover_size", 30))),
            notify=False,
        )
        self.window.set_album_island_expanded_cover_size(int(saved_setting("v2_album_island_expanded_cover_size", 72)), notify=False)
        self.window.set_album_island_spacing(int(saved_setting("v2_album_island_spacing", 112)), notify=False)
        self.window.set_album_island_secondary_style(str(saved_setting("v2_album_island_secondary_style", "perspective_dim")), notify=False)
        self.window.set_album_island_perspective_amount(int(saved_setting("v2_album_island_perspective_amount", 100)), notify=False)
        self.window.set_album_island_record(
            float(saved_setting("v2_album_island_listened_seconds_total", 0.0) or 0.0),
            saved_setting("v2_album_island_history", []),
            notify=False,
        )
        self.window.set_no_lyrics_text(str(saved_setting("v2_no_lyrics_text", "暂无歌词")), notify=False)
        position_preset = str(saved_setting("v2_position_preset", "last") or "last")
        self.window.apply_position_preset(position_preset, notify=False)
        self.window._set_plugin_focus(min(self.window.plugin_focus_target, max(0, self.window.enabled_plugin_count - 1)), immediate=True)
        # The general placement preset may recenter the standalone surface.
        # Attach only after every saved setting has finished applying.
        if hasattr(self.window, "set_finder_bar_presence"):
            self.window.set_finder_bar_presence(mydockfinder_diagnostics.get("finder_bars", []), notify=False)

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
        self._configure_mydockfinder_listener()

    def _configure_mydockfinder_listener(self):
        enabled = bool(self.settings.get("v2_mydockfinder_monitor_enabled", True))
        self.window.mydockfinder_monitor_enabled = enabled
        timer = getattr(self, "mydockfinder_listener_timer", None)
        if timer is not None:
            timer.start(2500) if enabled else timer.stop()
        self._poll_mydockfinder_presence()

    def _poll_mydockfinder_presence(self):
        if not bool(self.settings.get("v2_mydockfinder_monitor_enabled", True)):
            self.window.mydockfinder_monitor_enabled = False
            self.window.set_mydockfinder_diagnostics({"running": False, "finder_bars": [], "reason": "monitor_disabled"}, notify=False)
            self.window.set_mydockfinder_running(False, notify=False)
            self.window.set_finder_bar_presence([], notify=False)
            if hasattr(self, "settings_window"):
                self.settings_window.sync_from_widget()
            self._sync_tray_status()
            return
        self.window.mydockfinder_monitor_enabled = True
        listener_process_names = self._mydockfinder_listener_process_names_setting()
        if tuple(getattr(self.window, "mydockfinder_listener_process_names", ())) != listener_process_names:
            if hasattr(self.window, "set_mydockfinder_listener_process_names"):
                self.window.set_mydockfinder_listener_process_names(listener_process_names, notify=False)
            else:
                self.window.mydockfinder_listener_process_names = listener_process_names
        previous_diagnostics = getattr(self.window, "mydockfinder_diagnostics", {}) or {}
        previous_bars = previous_diagnostics.get("finder_bars", ()) if isinstance(previous_diagnostics, dict) else ()
        diagnostics = _mydockfinder_process_diagnostics("", listener_process_names=listener_process_names, previous_bars=previous_bars)
        path = str(diagnostics.get("matched_path") or "")
        if str(getattr(self.window, "mydockfinder_path", "") or "") != path:
            self.window.set_mydockfinder_path(path, notify=False)
        if hasattr(self.window, "set_mydockfinder_diagnostics"):
            self.window.set_mydockfinder_diagnostics(diagnostics, notify=False)
        else:
            self.window.mydockfinder_diagnostics = diagnostics
        running = bool(diagnostics.get("running"))
        if bool(getattr(self.window, "mydockfinder_running", False)) != running:
            self.window.set_mydockfinder_running(running, notify=True)
        if hasattr(self.window, "set_finder_bar_presence"):
            self.window.set_finder_bar_presence(diagnostics.get("finder_bars", []), notify=False)
        if hasattr(self, "settings_window"):
            self.settings_window.sync_from_widget()
        self._sync_tray_status()

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
        show_action.setCheckable(True)
        show_action.setChecked(not bool(getattr(self.window, "_tray_hidden", False)))
        self.tray_visibility_action = show_action
        show_action.triggered.connect(self.toggle_window)
        menu.addAction(show_action)

        settings_action = QAction("设置", menu)
        settings_action.triggered.connect(self.show_settings)
        menu.addAction(settings_action)

        monitor_action = QWidgetAction(menu)
        row = QWidget(menu)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 8, 12, 8)
        self.tray_monitor_lamp = StatusLamp(False, "MyDockFinder", row)
        row_layout.addWidget(self.tray_monitor_lamp)
        row_layout.addWidget(QLabel("MyDockFinder", row))
        row_layout.addStretch(1)
        self.tray_monitor_switch = IosSwitch("", parent=row)
        self.tray_monitor_switch.setChecked(bool(self.settings.get("v2_mydockfinder_monitor_enabled", True)))
        self.tray_monitor_switch.toggled.connect(lambda enabled: self.settings.set("v2_mydockfinder_monitor_enabled", enabled))
        row_layout.addWidget(self.tray_monitor_switch)
        monitor_action.setDefaultWidget(row)
        menu.addAction(monitor_action)
        menu.aboutToShow.connect(self._sync_tray_status)

        menu.addSeparator()

        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        self._sync_tray_status()

    def _sync_tray_status(self):
        enabled = bool(self.settings.get("v2_mydockfinder_monitor_enabled", True))
        lamp = getattr(self, "tray_monitor_lamp", None)
        if lamp is not None:
            lamp.set_monitor_enabled(enabled)
            lamp.set_active(bool(getattr(self.window, "mydockfinder_running", False)), "监听已关闭" if not enabled else "已检测" if self.window.mydockfinder_running else "监听中，未检测")
        switch = getattr(self, "tray_monitor_switch", None)
        if switch is not None:
            switch.blockSignals(True)
            switch.setChecked(enabled)
            switch.blockSignals(False)
        action = getattr(self, "tray_visibility_action", None)
        if action is not None:
            visible = not bool(getattr(self.window, "_tray_hidden", False))
            action.setChecked(visible)
            lamp_icon = QPixmap(16, 16)
            lamp_icon.fill(Qt.GlobalColor.transparent)
            with QPainter(lamp_icon) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#1fc95b" if visible else "#8e8e93"))
                painter.drawEllipse(3, 3, 10, 10)
            action.setIcon(QIcon(lamp_icon))

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
        hidden = not bool(getattr(self.window, "_tray_hidden", False))
        self.window._tray_hidden = hidden
        if hidden:
            for controller_name in ("music_library", "playback_entry"):
                controller = getattr(self, controller_name, None)
                surface = getattr(controller, "window", None)
                if surface is not None and surface.isVisible():
                    surface.close()
            overlay = getattr(self.window, "music_fullscreen_overlay", None)
            if overlay is not None:
                overlay.hide()
            self.window._music_surface_active = False
            self.window._music_library_active = False
            self.settings_window.hide()
            self.window.hide()
        else:
            self.window.ensure_minimum_surface()
        self._sync_tray_status()

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
        self.window._application_quitting = True
        if self.tray:
            self.tray.hide()
        self._flush_visual_state_now()
        self.settings_window.close()
        self.window.close()
        if self.single_instance_guard is not None:
            self.single_instance_guard.close()
        self.app.quit()

    def run(self):
        handoff = self._logical_installer_handoff(self._installer_handoff)
        def start():
            if handoff is None:
                self.playback_entry.start()
            else:
                self.playback_entry.start(installer_handoff=handoff)
        from local.installer_handoff import InstallerHandoffEvents, start_synchronized_handoff
        token = getattr(self, "_installer_sync", None)
        events = InstallerHandoffEvents.connect(token) if token is not None else None
        if events is None:
            start()
        else:
            self._installer_gate = start_synchronized_handoff(
                self.app, start, (self.window, self.playback_entry.window, self.music_library.window), events)
        return self.app.exec()

    def _logical_installer_handoff(self, values):
        """Resolve physical Windows installer bounds using Qt's monitor origin."""
        if values is None:
            return None
        from local.installer_handoff import logical_handoff_rect
        return logical_handoff_rect(values, QGuiApplication.screens())


def main(*, installer_handoff=None, installer_sync=None):
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")

    single_instance_guard = SingleInstanceGuard()
    if not single_instance_guard.acquired:
        return

    app = LocalIslandApp(single_instance_guard, installer_handoff=installer_handoff, installer_sync=installer_sync)
    sys.exit(app.run())


if __name__ == "__main__":
    main()
