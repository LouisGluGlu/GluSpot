from __future__ import annotations

import copy
import json
import os
import shutil

from PySide6.QtCore import QObject, QTimer, Signal

from local.branding import APP_NAME, app_data_dir, legacy_app_data_dirs, settings_file_for


APP_DATA_DIR = app_data_dir()
SETTINGS_FILE = settings_file_for()

DEFAULT_MUSIC_AMBIENCE_VALUES = {
    "sea_level": {
        "ambient_intensity": 0.0,
        "music_flow_intensity": 0.74,
        "compact_duotone_balance": 50,
        "expanded_duotone_balance": 50,
        "diffraction_frequency": 0.30,
    },
    "duotone_full": {
        "ambient_intensity": 0.0,
        "music_flow_intensity": 0.74,
        "compact_duotone_balance": 50,
        "expanded_duotone_balance": 50,
        "diffraction_frequency": 0.30,
    },
    "pure_black": {
        "ambient_intensity": 0.0,
        "music_flow_intensity": 0.74,
        "compact_duotone_balance": 50,
        "expanded_duotone_balance": 50,
        "diffraction_frequency": 0.30,
    },
    "bilateral_diffraction": {
        "ambient_intensity": 0.0,
        "music_flow_intensity": 0.74,
        "compact_duotone_balance": 50,
        "expanded_duotone_balance": 50,
        "diffraction_frequency": 0.30,
    },
    "yaoguang": {
        "ambient_intensity": 0.0,
        "music_flow_intensity": 0.74,
        "compact_duotone_balance": 50,
        "expanded_duotone_balance": 50,
        "diffraction_frequency": 0.30,
    },
    "solid_album": {
        "ambient_intensity": 0.0,
        "music_flow_intensity": 0.74,
        "compact_duotone_balance": 50,
        "expanded_duotone_balance": 50,
        "diffraction_frequency": 0.30,
    },
    "pure_white": {
        "ambient_intensity": 0.0,
        "music_flow_intensity": 0.74,
        "compact_duotone_balance": 50,
        "expanded_duotone_balance": 50,
        "diffraction_frequency": 0.30,
    },
}

DEFAULT_MUSIC_APP_PROFILES = {
    "netease": {
        "path": "",
        "sync_enabled": False,
        "lyric_offset_seconds": 0.0,
        "betterncm_enabled": False,
        "betterncm_path": "",
    },
    "qq": {
        "path": "",
        "sync_enabled": False,
        "lyric_offset_seconds": 0.0,
    },
    "qishui": {
        "path": "",
        "sync_enabled": False,
        "lyric_offset_seconds": 0.0,
    },
    "kugou": {
        "path": "",
        "sync_enabled": False,
        "lyric_offset_seconds": 0.0,
    },
}

MYDOCKFINDER_LISTEN_PROCESS_CHOICES = (
    ("dock.exe", "dock.exe"),
    ("dock_64.exe", "dock_64.exe"),
    ("dockmod.exe", "dockmod.exe"),
    ("dockmod64.exe", "dockmod64.exe"),
    ("mydockfinder.exe", "MyDockFinder.exe"),
    ("myfinder.exe", "MyFinder.exe"),
    ("mydock.exe", "Mydock.exe"),
)

DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES = ("dock.exe",)


def normalize_mydockfinder_listener_process_names(listener_process_names=None) -> tuple[str, ...]:
    if listener_process_names is None:
        raw_names = DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES
    elif isinstance(listener_process_names, str):
        raw_names = listener_process_names.replace(";", ",").split(",")
    else:
        try:
            raw_names = list(listener_process_names)
        except TypeError:
            raw_names = ()
    selected = {str(name or "").strip().casefold() for name in raw_names}
    return tuple(name for name, _label in MYDOCKFINDER_LISTEN_PROCESS_CHOICES if name in selected)


DEFAULT_SETTINGS = {
    "v2_startup_mode": "none",
    "v2_custom_icon_path": "",
    "v2_music_playing": True,
    "v2_music_auto_detect": False,
    "v2_persistent_plugin": True,
    "v2_super_topmost": True,
    "v2_location_mode": "top",
    "v2_top_notch_height_mode": "standard",
    "v2_finder_mode": None,
    "v2_manual_mydockfinder": False,
    "v2_mydockfinder_path": "",
    "v2_mydockfinder_listener_process_names": list(DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES),
    "v2_taskbar_corner_style": "win11",
    "v2_taskbar_auto_hide_mode": "auto",
    "v2_taskbar_left_handle_color": "#8E8E93",
    "v2_expanded": False,
    "v2_show_seconds": True,
    "v2_enabled_plugins": ["time"],
    "v2_side_island_style": "standard",
    "v2_plugin_island_style": "standard",
    "v2_plugin_fullscreen_alert_style": "toast",
    "v2_plugin_fullscreen_alert_x_offset": 0,
    "v2_plugin_fullscreen_alert_y_offset": 0,
    "v2_music_island_style": "standard",
    "v2_music_expanded_layout_style": "standard",
    "v2_time_alarm_side_display": "note",
    "v2_ambient_intensity": 0.0,
    "v2_music_ambience_style": "sea_level",
    "v2_music_flow_intensity": 0.74,
    "v2_music_reactive_background_enabled": False,
    "v2_music_reactive_background_intensity": 55,
    "v2_compact_duotone_balance": 50,
    "v2_expanded_duotone_balance": 50,
    "v2_diffraction_frequency": 0.30,
    "v2_solid_album_background_fallback": "#16BCFF",
    "v2_yaoguang_outer_color": "#6C35F2",
    "v2_yaoguang_blur_strength": 58,
    "v2_yaoguang_color_source": "custom",
    "v2_yaoguang_album_inverse_enabled": False,
    "v2_yaoguang_height_mode": "regular",
    "v2_music_ambience_values": copy.deepcopy(DEFAULT_MUSIC_AMBIENCE_VALUES),
    "v2_enable_player_progress_plugin": False,
    "v2_player_progress_auto_detect": False,
    "v2_music_player_mode": "netease",
    "v2_netease_betterncm_path": "",
    "v2_music_app_profiles": copy.deepcopy(DEFAULT_MUSIC_APP_PROFILES),
    "v2_lyric_flow_style": "wave_stream",
    "v2_gelv_font_family": "Noto Serif SC ExtraBold",
    "v2_gelv_primary_font_size": 22,
    "v2_gelv_secondary_font_size": 12,
    "v2_gelv_dual_line_font_size": 17,
    "v2_gelv_initial_word_spacing": 220,
    "v2_gelv_word_spacing": 320,
    "v2_gelv_color_fill_enabled": False,
    "v2_gelv_word_fill_enabled": False,
    "v2_wave_font_weight": "bold",
    "v2_wave_word_fill_enabled": False,
    "v2_wave_color_fill_enabled": False,
    "v2_ember_font_weight": "normal",
    "v2_ember_font_size": 18,
    "v2_ember_line_mode": "split",
    "v2_ember_dual_line_enabled": False,
    "v2_ember_italic_enabled": False,
    "v2_ember_show_cover": True,
    "v2_ember_word_fill_enabled": True,
    "v2_ember_color_fill_enabled": False,
    "v2_ember_back_gray_mix": 62,
    "v2_ember_color_back_gray_mix": 0,
    "v2_ember_back_opacity": 58,
    "v2_ember_blur_panel_strength": 18,
    "v2_ember_lyric_offset_seconds": 0.0,
    "v2_no_lyrics_display_mode": "text",
    "v2_album_island_cover_count": 5,
    "v2_album_island_cover_size": 30,
    "v2_album_island_compact_cover_size": 30,
    "v2_album_island_expanded_cover_size": 72,
    "v2_album_island_spacing": 112,
    "v2_album_island_secondary_style": "perspective_dim",
    "v2_album_island_perspective_amount": 100,
    "v2_album_island_listened_seconds_total": 0.0,
    "v2_album_island_history": [],
    "v2_theme_mode": "system",
    "v2_theme_dark_start": "22:00",
    "v2_theme_dark_end": "07:00",
    "v2_music_background_follow_theme": False,
    "v2_tasker_follow_taskbar_color": False,
    "v2_music_background_follow_previous_style": "",
    "v2_font_family": "Alibaba PuHuiTi",
    "v2_settings_font_family": "Alibaba PuHuiTi",
    "v2_imported_settings_fonts": [],
    "v2_high_quality_font_rendering": False,
    "v2_animation_fps": 60,
    "v2_lyric_animation_fps": 60,
    "v2_animation_speed": 1.0,
    "v2_empty_island_display_mode": "visible_bar",
    "v2_no_music_island_priority": "plugin",
    "v2_empty_bar_palette": "aurora",
    "v2_empty_bar_width": 92,
    "v2_empty_bar_height": 14,
    "v2_empty_bar_y_offset": 0,
    "v2_empty_bar_opacity": 100,
    "v2_empty_bar_custom_colors": ["#46D6FF", "#73FFA6", "#FF5A86"],
    "v2_stack_center_ratio": 0.5,
    "v2_window_pos": None,
    "v2_last_window_position": None,
    "v2_position_preset": "last",
    "v2_stack_direction": 1,
    "v2_barrage_y_offset": 0,
    "v2_barrage_font_size": 10,
    "v2_barrage_speed": 100,
    "v2_barrage_use_backdrop": True,
    "v2_barrage_show_icon": True,
    "v2_barrage_display_mode": "fullscreen",
    "v2_auto_expand_bounds": True,
    "v2_expand_direction": "auto",
    "v2_lyric_offset_seconds": 0.0,
    "v2_lyric_font_size": 18,
    "v2_lyric_elastic_strength": 100,
    "v2_lyric_elastic_mode": "both",
    "v2_lyric_word_spacing": 100,
    "v2_enable_dual_line_lyrics": False,
    "v2_dual_line_font_size": 12,
    "v2_dual_line_secondary_weight": 72,
    "v2_dual_line_secondary_spacing": 100,
    "v2_dual_line_secondary_source": "translation",
    "v2_lyric_chinese_conversion": "off",
    "v2_compact_lyric_show_cover": True,
    "v2_no_lyrics_text": "暂无歌词",
    "v2_weather_location": "北京",
    "v2_weather_use_location": True,
    "v2_weather_manual_location": "北京",
    "v2_weather_api_key": "",
    "v2_weather_extensions": "base",
    "v2_weather_refresh_nonce": "",
    "v2_plugin_timezone": "local",
    "v2_timer_started_at": "",
    "v2_timer_end_at": "",
    "v2_alarm_at": "",
    "v2_alarm_rows": "",
    "v2_schedule_reminder_minutes": "15,30,5",
    "v2_schedule_json_path": "",
    "v2_schedule_ics_path": "",
    "v2_enable_message_detection": True,
    "v2_enable_wechat_notification": True,
    "v2_enable_wechat_detail_notification": False,
    "v2_enable_qq_notification": False,
    "v2_enable_qq_detail_notification": False,
    "v2_enable_dingtalk_notification": False,
    "v2_enable_dingtalk_detail_notification": False,
}


class SettingsManager(QObject):
    setting_changed = Signal(str, object)

    def __init__(self):
        super().__init__()
        self._settings = copy.deepcopy(DEFAULT_SETTINGS)
        self._dirty = False
        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._do_save)
        self._ensure_dir()
        self._import_legacy_settings_if_needed()
        self.load()

    def _ensure_dir(self):
        os.makedirs(APP_DATA_DIR, exist_ok=True)

    def _import_legacy_settings_if_needed(self):
        if os.path.exists(SETTINGS_FILE):
            return
        for legacy_dir in legacy_app_data_dirs():
            legacy_file = os.path.join(legacy_dir, "settings.json")
            if not os.path.exists(legacy_file):
                continue
            try:
                shutil.copy2(legacy_file, SETTINGS_FILE)
            except OSError:
                return
            return

    def load(self):
        if not os.path.exists(SETTINGS_FILE):
            return
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._settings.update(data)
            if "v2_wave_word_fill_enabled" not in data and "v2_wave_color_fill_enabled" in data:
                self._settings["v2_wave_word_fill_enabled"] = bool(data.get("v2_wave_color_fill_enabled", False))
                self._settings["v2_wave_color_fill_enabled"] = False
        except (json.JSONDecodeError, OSError):
            pass

    def save(self):
        self._dirty = True
        self._save_timer.start(2000)

    def save_now(self):
        self._save_timer.stop()
        self._do_save()

    def _do_save(self):
        if not self._dirty:
            return
        self._ensure_dir()
        temp_file = SETTINGS_FILE + ".tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, indent=2, ensure_ascii=False)
            os.replace(temp_file, SETTINGS_FILE)
            self._dirty = False
        except OSError:
            pass

    def get(self, key: str, default=None):
        return self._settings.get(key, default)

    def set(self, key: str, value):
        if self._settings.get(key) == value:
            return
        self._settings[key] = value
        self.setting_changed.emit(key, value)
        self.save()
