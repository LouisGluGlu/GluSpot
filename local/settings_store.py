from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil

from PySide6.QtCore import QObject, QTimer, Signal

from local.branding import APP_NAME, app_data_dir, legacy_app_data_dirs, settings_file_for
from local.music_lyric_motion import SPARSE_MIN_SECONDS_PER_UNIT, normalize_sparse_seconds_per_unit


APP_DATA_DIR = app_data_dir()
SETTINGS_FILE = settings_file_for()


MUSIC_READING_GRADIENT_DEFAULT_STEPS = 30
MUSIC_READING_LIFT_DEFAULT_LEVEL = 2
MUSIC_READING_LIFT_LEVELS = (0, 1, 2, 4)
MUSIC_READING_STRATEGY_DEFAULT = "gold"


def normalize_music_reading_strategy(value) -> str:
    """Keep the current interpolated reading unless low-perception is selected."""
    return value if isinstance(value, str) and value in {"gold", "low_perception"} else MUSIC_READING_STRATEGY_DEFAULT


def normalize_music_reading_lift_level(value) -> int:
    """Quarter units of the original reading lift height."""
    try:
        if isinstance(value, bool):
            return MUSIC_READING_LIFT_DEFAULT_LEVEL
        number = float(value)
        if not math.isfinite(number) or not number.is_integer():
            return MUSIC_READING_LIFT_DEFAULT_LEVEL
        level = int(number)
    except (TypeError, ValueError, OverflowError):
        return MUSIC_READING_LIFT_DEFAULT_LEVEL
    return level if level in MUSIC_READING_LIFT_LEVELS else MUSIC_READING_LIFT_DEFAULT_LEVEL


def normalize_music_reading_gradient_steps(value) -> int:
    """Tenths of a visual character, from a hard edge to the widest feather."""
    try:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite gradient width")
        return max(0, min(30, int(round(number))))
    except (TypeError, ValueError, OverflowError):
        return MUSIC_READING_GRADIENT_DEFAULT_STEPS

DEFAULT_MUSIC_AMBIENCE_VALUES = {
    "sea_level": {
        "ambient_intensity": 0.0,
        "music_flow_intensity": 0.74,
        "compact_duotone_balance": 50,
        "expanded_duotone_balance": 50,
    },
    "system": {
        "ambient_intensity": 0.0,
        "music_flow_intensity": 0.74,
        "compact_duotone_balance": 50,
        "expanded_duotone_balance": 50,
    },
    "solid_album": {
        "ambient_intensity": 0.0,
        "music_flow_intensity": 0.74,
        "compact_duotone_balance": 50,
        "expanded_duotone_balance": 50,
    },
}


def normalize_music_ambience_style(value) -> str:
    style = str(value or "sea_level").strip().lower()
    if style in {"pure_black", "pure_white"}:
        return "system"
    return style if style in DEFAULT_MUSIC_AMBIENCE_VALUES else "sea_level"


def normalize_music_ambience_values(value, preferred_style=None) -> dict:
    """Retain saved values when retired background names migrate to one mode."""
    result = copy.deepcopy(DEFAULT_MUSIC_AMBIENCE_VALUES)
    if not isinstance(value, dict):
        return result
    # Canonical entries win over aliases; the selected legacy mode wins its pair.
    aliases = [style for style in value if style not in result]
    if preferred_style in aliases:
        aliases.remove(preferred_style)
        aliases.append(preferred_style)
    for style in aliases + [style for style in value if style in result]:
        if style not in {"pure_black", "pure_white", "duotone_full"} and style not in result:
            continue
        values = value[style]
        if isinstance(values, dict):
            target = normalize_music_ambience_style(style)
            result[target].update({key: values[key] for key in result[target] if key in values})
    return result

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
    "v2_playback_onboarding_completed": False,
    "v2_owned_playback_enabled": True,
    "v2_owned_playback_mode": "sequential",
    "v2_personal_dj_enabled": False,
    "v2_personal_dj_voice_key": "",
    "v2_system_capture_enabled": False,
    "v2_startup_mode": "none",
    "v2_custom_icon_path": "",
    "v2_music_playing": True,
    "v2_music_auto_detect": False,
    "v2_persistent_plugin": False,
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
    "v2_enabled_plugins": [],
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
    "v2_solid_album_background_fallback": "#16BCFF",
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
    "v2_lyric_animation_fps": 120,
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
    "v2_music_surface_reading_lift_level": MUSIC_READING_LIFT_DEFAULT_LEVEL,
    "v2_music_surface_reading_gradient_steps": MUSIC_READING_GRADIENT_DEFAULT_STEPS,
    "v2_music_surface_reading_strategy": MUSIC_READING_STRATEGY_DEFAULT,
    "v2_music_sparse_highlight_seconds_per_unit": SPARSE_MIN_SECONDS_PER_UNIT,
    "v2_music_surface_preload_count": 6,
    "v2_music_intro_cleanup_enabled": True,
    "v2_enable_dual_line_lyrics": False,
    "v2_dual_line_font_size": 12,
    "v2_dual_line_secondary_weight": 72,
    "v2_dual_line_secondary_spacing": 100,
    "v2_dual_line_secondary_source": "translation",
    "v2_cantonese_secondary_priority": "phonetic_first",
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
    "v2_enable_message_detection": False,
    "v2_enable_wechat_notification": False,
    "v2_enable_wechat_detail_notification": False,
    "v2_enable_qq_notification": False,
    "v2_enable_qq_detail_notification": False,
    "v2_enable_dingtalk_notification": False,
    "v2_enable_dingtalk_detail_notification": False,
}


# Removed controls keep compatibility keys, but old files and visual-state
# exports cannot restore custom styles or restart retired runtime services.
# Playback-page translation is a session action; its persisted default stays off.
FIXED_DEFAULT_SETTING_KEYS = frozenset({
    "v2_music_playing", "v2_music_auto_detect", "v2_persistent_plugin",
    "v2_enabled_plugins", "v2_super_topmost", "v2_location_mode", "v2_top_notch_height_mode",
    "v2_finder_mode", "v2_manual_mydockfinder", "v2_show_seconds",
    "v2_taskbar_corner_style", "v2_taskbar_auto_hide_mode",
    "v2_taskbar_left_handle_color", "v2_tasker_follow_taskbar_color",
    "v2_side_island_style", "v2_plugin_island_style", "v2_music_island_style",
    "v2_music_expanded_layout_style", "v2_plugin_fullscreen_alert_style",
    "v2_plugin_fullscreen_alert_x_offset", "v2_plugin_fullscreen_alert_y_offset",
    "v2_font_family", "v2_settings_font_family", "v2_imported_settings_fonts",
    "v2_high_quality_font_rendering", "v2_empty_island_display_mode",
    "v2_no_music_island_priority", "v2_empty_bar_palette", "v2_empty_bar_width",
    "v2_empty_bar_height", "v2_empty_bar_y_offset", "v2_empty_bar_opacity",
    "v2_empty_bar_custom_colors", "v2_stack_center_ratio", "v2_window_pos",
    "v2_last_window_position", "v2_position_preset", "v2_stack_direction",
    "v2_auto_expand_bounds", "v2_expand_direction", "v2_ambient_intensity",
    "v2_music_flow_intensity", "v2_music_ambience_values",
    "v2_music_reactive_background_enabled", "v2_music_reactive_background_intensity",
    "v2_compact_duotone_balance", "v2_expanded_duotone_balance",
    "v2_solid_album_background_fallback", "v2_lyric_font_size",
    "v2_compact_lyric_show_cover", "v2_no_lyrics_display_mode", "v2_no_lyrics_text",
    "v2_wave_font_weight", "v2_gelv_font_family", "v2_gelv_primary_font_size",
    "v2_gelv_secondary_font_size", "v2_gelv_dual_line_font_size",
    "v2_gelv_initial_word_spacing", "v2_gelv_word_spacing",
    "v2_gelv_color_fill_enabled", "v2_gelv_word_fill_enabled",
    "v2_enable_dual_line_lyrics", "v2_dual_line_font_size",
    "v2_dual_line_secondary_weight", "v2_dual_line_secondary_spacing",
    "v2_album_island_cover_count", "v2_album_island_cover_size",
    "v2_album_island_compact_cover_size", "v2_album_island_expanded_cover_size",
    "v2_album_island_spacing", "v2_album_island_secondary_style",
    "v2_album_island_perspective_amount", "v2_barrage_y_offset",
    "v2_barrage_font_size", "v2_barrage_speed", "v2_barrage_use_backdrop",
    "v2_barrage_show_icon", "v2_barrage_display_mode",
    "v2_enable_message_detection", "v2_enable_wechat_notification",
    "v2_enable_wechat_detail_notification", "v2_enable_qq_notification",
    "v2_enable_qq_detail_notification", "v2_enable_dingtalk_notification",
    "v2_enable_dingtalk_detail_notification",
})


_REMOVED_MUSIC_AMBIENCE_STYLES = {"yaoguang", "bilateral_diffraction"}
_REMOVED_VISUAL_SETTING_KEYS = {
    "v2_diffraction_frequency",
    "v2_yaoguang_outer_color",
    "v2_yaoguang_blur_strength",
    "v2_yaoguang_color_source",
    "v2_yaoguang_album_inverse_enabled",
    "v2_yaoguang_height_mode",
    "v2_wave_word_fill_enabled",
    "v2_wave_color_fill_enabled",
    "v2_lyric_elastic_strength",
    "v2_lyric_elastic_mode",
    "v2_lyric_word_spacing",
    "v2_fullscreen_reading_effect",
    "v2_plugin_count",
    "v2_taskbar_fusion_mode",
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
            if not isinstance(data, dict):
                return
            migrated = any(key in data for key in _REMOVED_VISUAL_SETTING_KEYS)
            self._settings.update({key: value for key, value in data.items() if key not in _REMOVED_VISUAL_SETTING_KEYS})
            for key, default in (("v2_owned_playback_enabled", True), ("v2_system_capture_enabled", False),
                                 ("v2_playback_onboarding_completed", False), ("v2_personal_dj_enabled", False)):
                if not isinstance(self._settings.get(key), bool):
                    self._settings[key] = default
                    migrated = True
            if not self._settings["v2_owned_playback_enabled"] and not self._settings["v2_system_capture_enabled"]:
                self._settings["v2_owned_playback_enabled"] = True
                migrated = True
            if self._settings.get("v2_owned_playback_mode") not in ("sequential", "shuffle", "repeat_one"):
                self._settings["v2_owned_playback_mode"] = "sequential"
                migrated = True
            voice = self._settings.get("v2_personal_dj_voice_key", "")
            if not isinstance(voice, str) or voice and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", voice) is None:
                self._settings["v2_personal_dj_voice_key"] = ""
                migrated = True
            original_style = self._settings.get("v2_music_ambience_style")
            for key in ("v2_music_ambience_style", "v2_music_background_follow_previous_style"):
                raw = self._settings.get(key, "")
                canonical = normalize_music_ambience_style(raw) if raw or key == "v2_music_ambience_style" else ""
                self._settings[key] = canonical
                migrated = migrated or raw != canonical
            ambience_values = self._settings.get("v2_music_ambience_values")
            self._settings["v2_music_ambience_values"] = normalize_music_ambience_values(ambience_values, original_style)
            migrated = migrated or ambience_values != self._settings["v2_music_ambience_values"]
            if str(self._settings.get("v2_lyric_flow_style")) not in {"wave_stream", "gelv_songscatter", "album_island"}:
                self._settings["v2_lyric_flow_style"] = "wave_stream"
                migrated = True
            if self._settings.get("v2_album_island_secondary_style") != "perspective_dim":
                self._settings["v2_album_island_secondary_style"] = "perspective_dim"
                migrated = True
            self._settings["v2_music_sparse_highlight_seconds_per_unit"] = normalize_sparse_seconds_per_unit(
                self._settings.get("v2_music_sparse_highlight_seconds_per_unit")
            )
            raw_lift_level = self._settings.get("v2_music_surface_reading_lift_level")
            lift_level = normalize_music_reading_lift_level(raw_lift_level)
            self._settings["v2_music_surface_reading_lift_level"] = lift_level
            migrated = migrated or raw_lift_level != lift_level
            raw_gradient_steps = self._settings.get("v2_music_surface_reading_gradient_steps")
            gradient_steps = normalize_music_reading_gradient_steps(raw_gradient_steps)
            self._settings["v2_music_surface_reading_gradient_steps"] = gradient_steps
            migrated = migrated or raw_gradient_steps != gradient_steps
            raw_reading_strategy = self._settings.get("v2_music_surface_reading_strategy")
            reading_strategy = normalize_music_reading_strategy(raw_reading_strategy)
            self._settings["v2_music_surface_reading_strategy"] = reading_strategy
            migrated = migrated or raw_reading_strategy != reading_strategy
            for key in FIXED_DEFAULT_SETTING_KEYS:
                default = DEFAULT_SETTINGS[key]
                migrated = migrated or self._settings.get(key) != default
                self._settings[key] = copy.deepcopy(default)
            if migrated:
                self.save()
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
        if key in FIXED_DEFAULT_SETTING_KEYS:
            return copy.deepcopy(DEFAULT_SETTINGS[key])
        return self._settings.get(key, default)

    def set(self, key: str, value):
        if key in _REMOVED_VISUAL_SETTING_KEYS or key in FIXED_DEFAULT_SETTING_KEYS:
            return
        if key == "v2_personal_dj_enabled" and not isinstance(value, bool):
            return
        if key == "v2_personal_dj_voice_key" and (not isinstance(value, str) or value and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is None):
            return
        if key == "v2_owned_playback_mode" and value not in ("sequential", "shuffle", "repeat_one"):
            value = "sequential"
        if key in {"v2_music_ambience_style", "v2_music_background_follow_previous_style"}:
            value = normalize_music_ambience_style(value) if value or key == "v2_music_ambience_style" else ""
        if key == "v2_music_ambience_values":
            value = normalize_music_ambience_values(value)
        if key == "v2_lyric_flow_style" and str(value) not in {"wave_stream", "gelv_songscatter", "album_island"}:
            value = "wave_stream"
        if key == "v2_album_island_secondary_style":
            value = "perspective_dim"
        if key == "v2_music_sparse_highlight_seconds_per_unit":
            value = normalize_sparse_seconds_per_unit(value)
        if key == "v2_music_surface_reading_lift_level":
            value = normalize_music_reading_lift_level(value)
        if key == "v2_music_surface_reading_gradient_steps":
            value = normalize_music_reading_gradient_steps(value)
        if key == "v2_music_surface_reading_strategy":
            value = normalize_music_reading_strategy(value)
        if self._settings.get(key) == value:
            return
        self._settings[key] = value
        self.setting_changed.emit(key, value)
        self.save()
