from __future__ import annotations

import copy
import json
import os

from PySide6.QtCore import QObject, QTimer, Signal


APP_NAME = "DynamicIsland"
APP_DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.dirname(__file__)), APP_NAME)
SETTINGS_FILE = os.path.join(APP_DATA_DIR, "settings.json")

DEFAULT_MUSIC_AMBIENCE_VALUES = {
    "sea_level": {
        "ambient_intensity": 0.0,
        "music_flow_intensity": 0.74,
        "compact_duotone_balance": 50,
        "expanded_duotone_balance": 50,
    },
    "duotone_full": {
        "ambient_intensity": 0.0,
        "music_flow_intensity": 0.74,
        "compact_duotone_balance": 50,
        "expanded_duotone_balance": 50,
    },
    "pure_black": {
        "ambient_intensity": 0.0,
        "music_flow_intensity": 0.74,
        "compact_duotone_balance": 50,
        "expanded_duotone_balance": 50,
    },
    "bilateral_diffraction": {
        "ambient_intensity": 0.0,
        "music_flow_intensity": 0.74,
        "compact_duotone_balance": 50,
        "expanded_duotone_balance": 50,
    },
    "pure_white": {
        "ambient_intensity": 0.0,
        "music_flow_intensity": 0.74,
        "compact_duotone_balance": 50,
        "expanded_duotone_balance": 50,
    },
}

DEFAULT_SETTINGS = {
    "v2_music_playing": True,
    "v2_music_auto_detect": False,
    "v2_persistent_plugin": True,
    "v2_super_topmost": True,
    "v2_location_mode": "free",
    "v2_finder_mode": None,
    "v2_manual_mydockfinder": False,
    "v2_taskbar_corner_style": "win11",
    "v2_taskbar_left_handle_color": "#8E8E93",
    "v2_expanded": False,
    "v2_show_seconds": True,
    "v2_enabled_plugins": ["time", "weather", "agenda"],
    "v2_side_island_style": "standard",
    "v2_plugin_island_style": "standard",
    "v2_music_island_style": "standard",
    "v2_time_alarm_side_display": "note",
    "v2_ambient_intensity": 0.0,
    "v2_music_ambience_style": "sea_level",
    "v2_music_flow_intensity": 0.74,
    "v2_compact_duotone_balance": 50,
    "v2_expanded_duotone_balance": 50,
    "v2_music_ambience_values": copy.deepcopy(DEFAULT_MUSIC_AMBIENCE_VALUES),
    "v2_enable_player_progress_plugin": False,
    "v2_player_progress_auto_detect": False,
    "v2_music_player_mode": "netease",
    "v2_netease_betterncm_path": "",
    "v2_lyric_flow_style": "wave_stream",
    "v2_gelv_font_family": "Source Han Serif SC",
    "v2_gelv_primary_font_size": 22,
    "v2_gelv_secondary_font_size": 12,
    "v2_gelv_dual_line_font_size": 17,
    "v2_gelv_initial_word_spacing": 220,
    "v2_gelv_word_spacing": 320,
    "v2_gelv_size_shift_enabled": True,
    "v2_gelv_color_fill_enabled": False,
    "v2_gelv_color_fill_fallback": "#00D7FF",
    "v2_wave_color_fill_enabled": False,
    "v2_wave_color_fill_fallback": "#00D7FF",
    "v2_no_lyrics_display_mode": "text",
    "v2_album_island_cover_count": 5,
    "v2_album_island_cover_size": 30,
    "v2_album_island_spacing": 112,
    "v2_album_island_secondary_style": "perspective_dim",
    "v2_album_island_perspective_amount": 100,
    "v2_theme_mode": "system",
    "v2_theme_dark_start": "22:00",
    "v2_theme_dark_end": "07:00",
    "v2_font_family": "PingFang SC",
    "v2_settings_font_family": "PingFang SC",
    "v2_imported_settings_fonts": [],
    "v2_high_quality_font_rendering": False,
    "v2_animation_fps": 60,
    "v2_animation_speed": 1.0,
    "v2_empty_bar_palette": "aurora",
    "v2_empty_bar_width": 92,
    "v2_empty_bar_height": 14,
    "v2_empty_bar_y_offset": 0,
    "v2_empty_bar_opacity": 100,
    "v2_empty_bar_custom_colors": ["#46D6FF", "#73FFA6", "#FF5A86"],
    "v2_stack_center_ratio": 0.5,
    "v2_window_pos": None,
    "v2_stack_direction": 1,
    "v2_barrage_y_offset": 0,
    "v2_barrage_font_size": 10,
    "v2_barrage_speed": 100,
    "v2_barrage_use_backdrop": True,
    "v2_barrage_show_icon": True,
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
    "v2_enable_dingtalk_notification": False,
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
        self.load()

    def _ensure_dir(self):
        os.makedirs(APP_DATA_DIR, exist_ok=True)

    def load(self):
        if not os.path.exists(SETTINGS_FILE):
            return
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._settings.update(data)
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
