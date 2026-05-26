from __future__ import annotations

import sys

from PySide6.QtCore import QEvent
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QComboBox


THEME_CHANGE_EVENT_TYPES = (
    QEvent.Type.ApplicationPaletteChange,
    QEvent.Type.PaletteChange,
    QEvent.Type.ThemeChange,
)


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


def app_dark_mode() -> bool:
    apps_use_light_theme = _windows_apps_use_light_theme()
    if apps_use_light_theme is not None:
        return int(apps_use_light_theme) == 0
    try:
        app = QApplication.instance()
        palette = app.palette() if app is not None else QApplication.palette()
        return palette.color(QPalette.ColorRole.Window).lightness() < 128
    except Exception:
        return False


def theme_change_event_types() -> tuple[QEvent.Type, ...]:
    return THEME_CHANGE_EVENT_TYPES


def is_theme_change_event(event) -> bool:
    try:
        return event.type() in THEME_CHANGE_EVENT_TYPES
    except Exception:
        return False


def ios_theme() -> dict[str, str]:
    if app_dark_mode():
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
