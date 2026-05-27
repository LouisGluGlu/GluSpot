from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton

from local.ui_theme import app_dark_mode


def mac_preferences_theme(dark: bool | None = None) -> dict[str, str]:
    if dark is None:
        dark = app_dark_mode()
    if dark:
        return {
            "window": "#1f1f21",
            "sidebar": "#2a2a2c",
            "sidebar_border": "#3a3a3c",
            "content": "#1f1f21",
            "titlebar": "#242426",
            "card": "#2c2c2e",
            "card_border": "#3a3a3c",
            "search": "#343436",
            "search_border": "#464649",
            "text": "#f5f5f7",
            "muted": "#b8b8be",
            "secondary": "#8e8e93",
            "control": "#3a3a3c",
            "control_hover": "#48484a",
            "popup_hover": "#3a3a3c",
            "track": "#515154",
            "scroll": "#77777c",
            "accent": "#0a84ff",
        }
    return {
        "window": "#ececec",
        "sidebar": "#c9c9c9",
        "sidebar_border": "#b7b7b7",
        "content": "#f2f2f2",
        "titlebar": "#efefef",
        "card": "#e9e9e9",
        "card_border": "#dddddd",
        "search": "#bbbbbb",
        "search_border": "#b0b0b0",
        "text": "#1d1d1f",
        "muted": "#5f6368",
        "secondary": "#8e8e93",
        "control": "#ffffff",
        "control_hover": "#f7f7f7",
        "popup_hover": "#dedede",
        "track": "#d7d7d7",
        "scroll": "#9a9a9a",
        "accent": "#0a84ff",
    }


def mac_sidebar_icon_colors() -> tuple[str, ...]:
    return (
        "#0a84ff",
        "#34c759",
        "#ff9500",
        "#af52de",
        "#ff3b30",
        "#5856d6",
        "#5ac8fa",
        "#8e8e93",
    )


class MacTrafficButton(QPushButton):
    def __init__(self, object_name: str, tooltip: str, parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setToolTip(tooltip)
        self.setFixedSize(12, 12)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)


def mac_traffic_lights(window=None) -> QFrame:
    traffic = QFrame()
    traffic.setObjectName("MacTrafficLights")
    layout = QHBoxLayout(traffic)
    layout.setContentsMargins(2, 2, 0, 2)
    layout.setSpacing(8)
    controls = (
        ("MacTrafficRed", "关闭", lambda: window.close() if window is not None else None),
        ("MacTrafficYellow", "最小化", lambda: window.showMinimized() if window is not None else None),
        (
            "MacTrafficGreen",
            "缩放",
            lambda: (
                window.showNormal()
                if window is not None and window.isMaximized()
                else window.showMaximized()
                if window is not None
                else None
            ),
        ),
    )
    for name, tooltip, callback in controls:
        button = MacTrafficButton(name, tooltip)
        button.clicked.connect(callback)
        layout.addWidget(button)
    return traffic


def mac_sidebar_icon(color: str, glyph: str = "") -> QIcon:
    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(1, 1, 16, 16, 4, 4)
    if glyph:
        painter.setPen(QColor(255, 255, 255, 230))
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, glyph[:1].upper())
    painter.end()
    return QIcon(pixmap)


def mac_preferences_stylesheet(theme: dict[str, str]) -> str:
    t = theme
    return f"""
        QDialog {{
            background: {t["content"]};
            color: {t["text"]};
            selection-background-color: {t["accent"]};
            selection-color: #ffffff;
        }}
        QWidget {{
            color: {t["text"]};
        }}
        QFrame#MacSidebar {{
            background: {t["sidebar"]};
            border-right: 1px solid {t["sidebar_border"]};
        }}
        QWidget#MacContentPane {{
            background: {t["content"]};
        }}
        QWidget#MacSidebarHeader {{
            background: transparent;
        }}
        QFrame#MacTrafficLights {{
            background: transparent;
            border: 0;
        }}
        QPushButton#MacTrafficRed, QPushButton#MacTrafficYellow, QPushButton#MacTrafficGreen {{
            min-width: 12px;
            max-width: 12px;
            min-height: 12px;
            max-height: 12px;
            border-radius: 6px;
            padding: 0;
        }}
        QPushButton#MacTrafficRed {{ background: #ff5f57; border: 1px solid rgba(0, 0, 0, 28); }}
        QPushButton#MacTrafficYellow {{ background: #ffbd2e; border: 1px solid rgba(0, 0, 0, 28); }}
        QPushButton#MacTrafficGreen {{ background: #28c840; border: 1px solid rgba(0, 0, 0, 28); }}
        QLineEdit#MacSearch {{
            min-height: 26px;
            border: 1px solid {t["search_border"]};
            border-radius: 5px;
            padding: 2px 9px;
            background: {t["search"]};
            color: {t["text"]};
        }}
        QLineEdit#MacSearch:focus {{
            border-color: {t["accent"]};
            background: {t["control"]};
        }}
        QListWidget#MacSidebarList {{
            background: transparent;
            color: {t["text"]};
            border: 0;
            outline: 0;
            padding: 6px 8px 10px 8px;
            font-size: 13px;
        }}
        QListWidget#MacSidebarList::item {{
            min-height: 26px;
            border-radius: 5px;
            padding: 4px 8px;
            margin: 1px 0;
            color: {t["text"]};
        }}
        QListWidget#MacSidebarList::item:hover {{
            background: rgba(255, 255, 255, 42);
        }}
        QListWidget#MacSidebarList::item:selected {{
            background: {t["accent"]};
            color: #ffffff;
        }}
        QFrame#MacTitleBar {{
            background: {t["titlebar"]};
            border-bottom: 1px solid {t["card_border"]};
        }}
        QLabel#MacTitle {{
            font-size: 14px;
            font-weight: 500;
            color: {t["text"]};
        }}
        QScrollArea#MacContentScroll {{
            border: 0;
            background: {t["content"]};
        }}
        QScrollArea#MacContentScroll QWidget#MacScrollBody {{
            background: {t["content"]};
        }}
        QScrollArea#MacContentScroll > QWidget > QWidget {{
            background: {t["content"]};
        }}
        QFrame#MacCard {{
            background: {t["card"]};
            border: 1px solid {t["card_border"]};
            border-radius: 8px;
        }}
        QFrame#MacCard QWidget {{
            background: transparent;
        }}
        QLabel {{
            background: transparent;
            color: {t["text"]};
        }}
        QLabel#Title {{
            font-size: 20px;
            font-weight: 600;
            color: {t["text"]};
            padding: 0 0 8px 0;
        }}
        QLabel#Subtitle {{
            color: {t["muted"]};
            font-size: 12px;
        }}
        QLabel#Section {{
            color: {t["muted"]};
            font-size: 12px;
            font-weight: 600;
            padding: 14px 0 6px 2px;
        }}
        QLabel#RowTitle {{
            font-size: 13px;
            color: {t["text"]};
        }}
        QLabel#Value, QLabel#Muted {{
            color: {t["muted"]};
            font-size: 12px;
        }}
        QLineEdit, QComboBox, QSpinBox {{
            min-height: 28px;
            border: 1px solid {t["card_border"]};
            border-radius: 5px;
            padding: 3px 8px;
            background: {t["control"]};
            color: {t["text"]};
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
            border-color: {t["accent"]};
            background: {t["control_hover"]};
        }}
        QPushButton {{
            min-height: 28px;
            border: 1px solid {t["card_border"]};
            border-radius: 5px;
            background: {t["control"]};
            color: {t["text"]};
            padding: 0 13px;
        }}
        QPushButton:hover {{
            background: {t["control_hover"]};
        }}
        QPushButton#Primary {{
            background: {t["accent"]};
            border-color: {t["accent"]};
            color: #ffffff;
        }}
        QPushButton#Danger {{
            background: #ff3b30;
            border-color: #ff3b30;
            color: #ffffff;
        }}
        QSlider::groove:horizontal {{
            height: 4px;
            border-radius: 2px;
            background: {t["track"]};
        }}
        QSlider::sub-page:horizontal {{
            height: 4px;
            border-radius: 2px;
            background: {t["accent"]};
        }}
        QSlider::handle:horizontal {{
            width: 14px;
            height: 14px;
            margin: -5px 0;
            border-radius: 7px;
            background: #ffffff;
            border: 1px solid {t["secondary"]};
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 8px 2px 8px 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {t["scroll"]};
            border-radius: 5px;
            min-height: 36px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QComboBox QAbstractItemView {{
            background: {t["control"]};
            color: {t["text"]};
            border: 1px solid {t["card_border"]};
            selection-background-color: {t["accent"]};
            selection-color: #ffffff;
            outline: 0;
        }}
        QComboBox QAbstractItemView::item {{
            min-height: 26px;
            padding: 5px 9px;
            background: transparent;
            color: {t["text"]};
        }}
        QComboBox QAbstractItemView::item:hover {{
            background: {t["control_hover"]};
        }}
    """
