from __future__ import annotations

import os
import sys
import threading
import time
import traceback

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from api.providers import WindowsMediaProvider
from local.island_stack_visual import IslandStackVisual
from local.settings_store import SettingsManager
from local.settings_window import SettingsWindow
from local.ui_theme import set_theme_settings_getter


class SingleInstanceGuard:
    def __init__(self):
        self.acquired = True
        self._file = None
        self._msvcrt = None
        if sys.platform != "win32":
            return
        try:
            import msvcrt

            lock_dir = os.path.join(os.environ.get("LOCALAPPDATA", ROOT_DIR), "gpt-island")
            os.makedirs(lock_dir, exist_ok=True)
            self._file = open(os.path.join(lock_dir, "gpt-island.lock"), "a+b")
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
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("gpt-island")
        self.app.setQuitOnLastWindowClosed(False)
        self.watchdog = MainThreadWatchdog(self.app)

        self.settings = SettingsManager()
        set_theme_settings_getter(self.settings.get)
        self.data_provider = WindowsMediaProvider(settings_getter=self.settings.get)
        app_font_family = IslandStackVisual.best_available_font_family(str(self.settings.get("v2_font_family", "PingFang SC")))
        self.app.setFont(QFont(app_font_family, 9))
        self.window = IslandStackVisual(data_provider=self.data_provider)
        self.window.settings_getter = self.settings.get
        self.window.settings_setter = self.settings.set
        self._apply_saved_settings()
        self.settings_window = SettingsWindow(self.window, self.settings)
        self.window.settings_changed_callback = self._save_visual_state
        self.tray = None
        self._setup_tray()

    def _apply_saved_settings(self):
        self.window.set_saved_placement(
            self.settings.get("v2_stack_center_ratio", 0.5),
            self.settings.get("v2_window_pos", None),
            notify=False,
            stack_direction=self.settings.get("v2_stack_direction", 1),
        )
        self.window.music_playing = bool(self.settings.get("v2_music_playing", True))
        self.window.music_auto_detect = bool(self.settings.get("v2_music_auto_detect", False))
        self.window.persistent_plugin = bool(self.settings.get("v2_persistent_plugin", True))
        self.window.set_manual_finder_available(bool(self.settings.get("v2_manual_mydockfinder", False)), notify=False)
        finder_setting = self.settings.get("v2_finder_mode", None)
        self.window.set_finder_mode(
            bool(self.window.finder_material.available) if finder_setting is None else bool(finder_setting),
            notify=False,
        )
        self.window.expanded = bool(self.settings.get("v2_expanded", False))
        self.window.show_seconds = bool(self.settings.get("v2_show_seconds", True))
        self.window.enabled_plugin_count = max(0, min(3, int(self.settings.get("v2_plugin_count", 3))))
        self.window.ambient_intensity = max(0.0, min(1.0, float(self.settings.get("v2_ambient_intensity", 0.0))))
        self.window.set_music_ambience_style(str(self.settings.get("v2_music_ambience_style", "sea_level")), notify=False)
        self.window.set_music_flow_intensity(float(self.settings.get("v2_music_flow_intensity", 0.74)), notify=False)
        self.window.set_duotone_balance(
            compact=int(self.settings.get("v2_compact_duotone_balance", 50)),
            expanded=int(self.settings.get("v2_expanded_duotone_balance", 50)),
            notify=False,
        )
        self.window.set_ui_font(str(self.settings.get("v2_font_family", "PingFang SC")), notify=False)
        self.window.set_high_quality_font_rendering(bool(self.settings.get("v2_high_quality_font_rendering", False)), notify=False)
        self.window.set_animation_fps(int(self.settings.get("v2_animation_fps", 60)), notify=False)
        self.window.set_animation_speed(float(self.settings.get("v2_animation_speed", 1.0)), notify=False)
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
            notify=False,
        )
        self.window.set_auto_expand_bounds(bool(self.settings.get("v2_auto_expand_bounds", True)), notify=False)
        self.window.set_expand_direction(str(self.settings.get("v2_expand_direction", "auto")), notify=False)
        self.window.set_lyric_offset_seconds(float(self.settings.get("v2_lyric_offset_seconds", 0.0)), notify=False)
        self.window.set_lyric_font_size(int(self.settings.get("v2_lyric_font_size", 18)), notify=False)
        self.window.set_lyric_elastic_strength(int(self.settings.get("v2_lyric_elastic_strength", 100)), notify=False)
        self.window.set_lyric_elastic_mode(str(self.settings.get("v2_lyric_elastic_mode", "both")), notify=False)
        self.window.set_lyric_word_spacing(int(self.settings.get("v2_lyric_word_spacing", 100)), notify=False)
        self.window.set_enable_dual_line_lyrics(bool(self.settings.get("v2_enable_dual_line_lyrics", False)), notify=False)
        self.window.set_dual_line_secondary_weight(int(self.settings.get("v2_dual_line_secondary_weight", 72)), notify=False)
        self.window.set_dual_line_secondary_spacing(int(self.settings.get("v2_dual_line_secondary_spacing", 100)), notify=False)
        self.window.set_no_lyrics_text(str(self.settings.get("v2_no_lyrics_text", "暂无歌词")), notify=False)
        self.window._set_plugin_focus(min(self.window.plugin_focus_target, max(0, self.window.enabled_plugin_count - 1)), immediate=True)

    def _save_visual_state(self, state: dict[str, object]):
        for key, value in state.items():
            if key == "v2_window_pos" and value is None and self.settings.get(key, None) is not None:
                continue
            self.settings.set(key, value)
        if hasattr(self, "settings_window"):
            self.settings_window.sync_from_widget()

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        icon = self.app.windowIcon()
        if icon.isNull():
            icon = self.app.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)

        self.tray = QSystemTrayIcon(icon, self.app)
        self.tray.setToolTip("gpt-island")

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

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.toggle_window()

    def show_settings(self):
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def toggle_window(self):
        if self.window.isVisible():
            self.window.hide()
        else:
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()

    def quit(self):
        if self.tray:
            self.tray.hide()
        self.settings.save_now()
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
