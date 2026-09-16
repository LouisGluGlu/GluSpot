from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, time as clock_time
from typing import Callable

from PySide6.QtCore import QEvent
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QComboBox


THEME_CHANGE_EVENT_TYPES = (
    QEvent.Type.ApplicationPaletteChange,
    QEvent.Type.PaletteChange,
    QEvent.Type.ThemeChange,
)
_theme_settings_getter: Callable[[str, object], object] | None = None


def set_theme_settings_getter(getter: Callable[[str, object], object] | None):
    global _theme_settings_getter
    _theme_settings_getter = getter


def _theme_setting(key: str, default=None, settings_getter: Callable[[str, object], object] | None = None):
    getter = settings_getter or _theme_settings_getter
    if getter is None:
        return default
    try:
        return getter(key, default)
    except Exception:
        return default


def _windows_apps_use_light_theme() -> int | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value)
    except Exception:
        return None


def _parse_clock(value: object, fallback: clock_time) -> clock_time:
    text = str(value or "").strip()
    if not text:
        return fallback
    for separator in (":", "."):
        if separator in text:
            hours, minutes = text.split(separator, 1)
            break
    else:
        hours, minutes = text, "0"
    try:
        hour_value = max(0, min(23, int(hours)))
        minute_value = max(0, min(59, int(minutes[:2] or "0")))
        return clock_time(hour_value, minute_value)
    except ValueError:
        return fallback


def _clock_from_now(now: datetime | clock_time | None) -> clock_time:
    if isinstance(now, datetime):
        return now.time().replace(second=0, microsecond=0)
    if isinstance(now, clock_time):
        return now.replace(second=0, microsecond=0)
    return datetime.now().time().replace(second=0, microsecond=0)


def _clock_in_window(current: clock_time, start: clock_time, end: clock_time) -> bool:
    current_value = current.hour * 60 + current.minute
    start_value = start.hour * 60 + start.minute
    end_value = end.hour * 60 + end.minute
    if start_value == end_value:
        return False
    if start_value < end_value:
        return start_value <= current_value < end_value
    return current_value >= start_value or current_value < end_value


def _scheduled_dark_mode(settings_getter: Callable[[str, object], object] | None = None, now: datetime | clock_time | None = None) -> bool:
    start = _parse_clock(_theme_setting("v2_theme_dark_start", "22:00", settings_getter), clock_time(22, 0))
    end = _parse_clock(_theme_setting("v2_theme_dark_end", "07:00", settings_getter), clock_time(7, 0))
    return _clock_in_window(_clock_from_now(now), start, end)


def app_dark_mode(settings_getter: Callable[[str, object], object] | None = None, now: datetime | clock_time | None = None) -> bool:
    mode = str(_theme_setting("v2_theme_mode", "system", settings_getter) or "system").strip().lower()
    if mode in {"dark", "night"}:
        return True
    if mode in {"light", "day"}:
        return False
    if mode in {"schedule", "scheduled", "custom"}:
        return _scheduled_dark_mode(settings_getter, now)
    apps_use_light_theme = _windows_apps_use_light_theme()
    if apps_use_light_theme is not None:
        return int(apps_use_light_theme) == 0
    try:
        app = QApplication.instance()
        palette = app.palette() if app is not None else QApplication.palette()
        return palette.color(QPalette.ColorRole.Window).lightness() < 128
    except Exception:
        return False


LIGHT_MUSIC_AMBIENCE_STYLES = {"pure_white", "yaoguang"}


@dataclass(frozen=True)
class MusicBackgroundFollowPlan:
    style_changed: bool
    target_style: str
    previous_style_to_save: str | None = None
    notify_settings: bool = False


def music_ambience_theme_category(style: str) -> str:
    return "light" if str(style or "").strip().lower() in LIGHT_MUSIC_AMBIENCE_STYLES else "dark"


def music_ambience_style_for_theme(
    dark: bool,
    current_style: str,
    follow_enabled: bool,
    previous_dark_style: str | None = None,
) -> str:
    style = str(current_style or "sea_level")
    if not follow_enabled:
        return style
    if bool(dark):
        if music_ambience_theme_category(style) == "light":
            previous = str(previous_dark_style or "").strip().lower()
            return previous if previous and music_ambience_theme_category(previous) == "dark" else "pure_black"
        return style
    return "pure_white" if music_ambience_theme_category(style) == "dark" else style


def music_background_light_sync_plan(
    *,
    light: bool,
    enabled: bool,
    current_style: str,
    previous_dark_style: str | None = None,
    allowed_styles: object = None,
) -> MusicBackgroundFollowPlan:
    current = str(current_style or "sea_level").strip().lower()
    if allowed_styles is not None:
        allowed = {str(style or "").strip().lower() for style in allowed_styles}
        if current not in allowed:
            current = "sea_level"
    previous = str(previous_dark_style or "").strip().lower()
    if not bool(enabled):
        return MusicBackgroundFollowPlan(style_changed=False, target_style=current)

    target = music_ambience_style_for_theme(
        not bool(light),
        current,
        follow_enabled=True,
        previous_dark_style=previous,
    )
    if target == current:
        previous_style_to_save = "" if not bool(light) and previous else None
        return MusicBackgroundFollowPlan(
            style_changed=False,
            target_style=target,
            previous_style_to_save=previous_style_to_save,
        )

    previous_style_to_save = None
    if bool(light) and music_ambience_theme_category(current) == "dark":
        previous_style_to_save = current
    if not bool(light):
        previous_style_to_save = ""
    return MusicBackgroundFollowPlan(
        style_changed=True,
        target_style=target,
        previous_style_to_save=previous_style_to_save,
        notify_settings=True,
    )


def theme_change_event_types() -> tuple[QEvent.Type, ...]:
    return THEME_CHANGE_EVENT_TYPES


def is_theme_change_event(event) -> bool:
    try:
        return event.type() in THEME_CHANGE_EVENT_TYPES
    except Exception:
        return False


def ios_theme(settings_getter: Callable[[str, object], object] | None = None, now: datetime | clock_time | None = None) -> dict[str, str]:
    if app_dark_mode(settings_getter=settings_getter, now=now):
        return {
            "window": "#1c1c1e",
            "panel": "#2c2c2e",
            "panel_alt": "#242426",
            "card": "#2c2c2e",
            "card_border": "#3a3a3c",
            "text": "#f5f5f7",
            "muted": "#b8b8be",
            "input": "#1f1f21",
            "input_focus": "#2a2a2d",
            "button": "#3a3a3c",
            "button_hover": "#48484a",
            "track": "#4a4a4d",
            "scroll": "#5a5a5f",
            "popup_hover": "#3a3a3c",
        }
    return {
        "window": "#f2f2f7",
        "panel": "#fbfbfd",
        "panel_alt": "#f2f2f7",
        "card": "#ffffff",
        "card_border": "#e5e5ea",
        "text": "#111827",
        "muted": "#6b7280",
        "input": "#f8f8fa",
        "input_focus": "#ffffff",
        "button": "#e5e5ea",
        "button_hover": "#dcdce2",
        "track": "#d1d1d6",
        "scroll": "#c7c7cc",
        "popup_hover": "#f2f2f7",
    }


def combo_popup_stylesheet(theme: dict[str, str] | None = None) -> str:
    t = theme or ios_theme()
    return f"""
        QAbstractItemView {{ background: {t["card"]}; color: {t["text"]}; border: 1px solid {t["card_border"]}; selection-background-color: #007aff; selection-color: #ffffff; outline: 0; }}
        QAbstractItemView::item {{ min-height: 30px; padding: 6px 10px; background: transparent; color: {t["text"]}; }}
        QAbstractItemView::item:hover {{ background: {t["popup_hover"]}; color: {t["text"]}; }}
        QAbstractItemView::item:selected {{ background: #007aff; color: #ffffff; }}
    """


def style_combo_popup(combo: QComboBox, theme: dict[str, str] | None = None):
    view = combo.view()
    view.setUniformItemSizes(True)
    view.setAutoFillBackground(True)
    view.setStyleSheet(combo_popup_stylesheet(theme))
